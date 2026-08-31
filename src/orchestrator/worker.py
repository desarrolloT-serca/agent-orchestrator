"""Agent loop: DeepSeek trabaja sobre el repositorio con herramientas."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable

from orchestrator import deepseek, tools

DEFAULT_LIMITS = {
    "max_iterations": 60,
    "max_runtime_minutes": 60,
    "max_cost_usd": 5.0,
    "max_tool_errors": 10,
}

# orden en que se valida el proyecto al terminar el worker
VALIDATION = ("test", "lint", "typecheck", "build")

SYSTEM = """Eres un ingeniero de software trabajando directamente sobre un repositorio real.

Reglas:
- Explora antes de editar: usa list_directory, search_code y read_file.
- Sigue las convenciones existentes del proyecto; no introduzcas dependencias nuevas.
- Haz el cambio minimo que resuelva la tarea.
- Ejecuta los comandos de validacion del proyecto con shell y corrige los fallos.
- No puedes salir del repositorio, leer secretos ni hacer git push/reset --hard/clean.
- No dejes ficheros temporales: lee la salida de los comandos, no la redirijas a un fichero.
- Cuando la tarea este terminada y validada, responde SIN llamar a ninguna herramienta,
  con un resumen breve: que has cambiado, que ficheros y el resultado de los tests.

Contexto del proyecto:
{project}

Estructura superior:
{tree}

git status:
{status}
"""


def _context(root: Path, config: dict) -> str:
    return SYSTEM.format(
        project=json.dumps(config, ensure_ascii=False, indent=2),
        tree=tools.list_directory(root, ".", depth=2)[:4000],
        status=tools.shell(root, "git status --short")[:2000],
    )


def validate(root: Path, config: dict, timeout: int = 900) -> dict:
    """Ejecuta test/lint/typecheck/build segun project.yaml."""
    commands = config.get("commands", {})
    results = {}
    for name in VALIDATION:
        command = commands.get(name)
        if not command:
            continue
        try:
            r = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True,
                               timeout=timeout, encoding="utf-8", errors="replace")
            output = (r.stdout + r.stderr).strip()
            results[name] = {"ok": r.returncode == 0, "command": command, "output": output[-2000:]}
        except subprocess.TimeoutExpired:
            results[name] = {"ok": False, "command": command, "output": f"timeout de {timeout}s"}
    return results


def run(
    root: Path,
    task: str,
    config: dict,
    api_key: str,
    model: str | None = None,
    reasoning: str | None = None,
    limits: dict | None = None,
    on_event: Callable[[str, str], None] = lambda kind, text: None,
) -> dict:
    """Ejecuta la tarea con un unico worker. Devuelve el resultado estructurado."""
    workers = config.get("workers", {})
    model = model or workers.get("default_model", deepseek.DEFAULT_MODEL)
    reasoning = reasoning or workers.get("reasoning", "high")
    limits = {**DEFAULT_LIMITS, **config.get("limits", {}), **(limits or {})}
    deadline = time.time() + limits["max_runtime_minutes"] * 60

    messages = [
        {"role": "system", "content": _context(root, config)},
        {"role": "user", "content": task},
    ]
    started = time.time()
    usage = {"prompt_tokens": 0, "prompt_cache_hit_tokens": 0, "completion_tokens": 0}
    cost = 0.0
    tool_calls = tool_errors = iteration = 0
    status, summary = "completed", ""

    while True:
        iteration += 1
        if iteration > limits["max_iterations"]:
            status, summary = "aborted", f"WORKER_ABORTED: limite de {limits['max_iterations']} iteraciones"
            break
        if time.time() > deadline:
            status, summary = "aborted", f"WORKER_ABORTED: limite de {limits['max_runtime_minutes']} minutos"
            break

        result = deepseek.chat(messages, tools.SCHEMAS, api_key, model, reasoning)
        message, step_usage = result["message"], result["usage"]
        for key in usage:
            usage[key] += step_usage.get(key, 0)
        cost += deepseek.cost(model, step_usage)
        messages.append(message)

        calls = message.get("tool_calls") or []
        if not calls:
            summary = message.get("content") or ""
            on_event("finish", summary)
            break

        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError as exc:
                args, output = {}, f"ERROR: argumentos JSON invalidos: {exc}"
            else:
                output = tools.execute(root, name, args)
            tool_calls += 1
            if output.startswith("ERROR:"):
                tool_errors += 1
            on_event("tool", f"[{iteration}] {name} {json.dumps(args, ensure_ascii=False)[:120]}")
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})

        if tool_errors >= limits["max_tool_errors"]:
            status, summary = "aborted", f"WORKER_ABORTED: {tool_errors} errores de herramienta"
            break
        if cost >= limits["max_cost_usd"]:
            status, summary = "aborted", f"WORKER_ABORTED: limite de coste {limits['max_cost_usd']} USD"
            break

    tests = {}
    issues = []
    if status == "completed":
        on_event("validate", "validando el proyecto...")
        tests = validate(root, config)
        issues = [f"{name} fallo: {r['command']}" for name, r in tests.items() if not r["ok"]]
        if issues:
            status = "validation_failed"

    changed = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=root,
                             capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    files = [line[3:] for line in changed.splitlines() if line.strip()]
    files = [f for f in files if "__pycache__" not in f and not tools.SECRET_FILES.search("/" + f)]
    return {
        "status": status,
        "summary": summary,
        "files_changed": files,
        "tests": tests,
        "issues": issues,
        "model": model,
        "reasoning": reasoning,
        "iterations": iteration,
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
        "duration_seconds": round(time.time() - started, 1),
        "usage": usage,
        "cost": round(cost, 6),
    }
