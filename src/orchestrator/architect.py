"""Arquitecto (V2): invoca el CLI de Claude Code (`claude -p`) para que estudie el
repositorio y proponga un plan.yaml -- nunca lo lanza, solo lo propone.

No usa la API de Anthropic ni ANTHROPIC_API_KEY: reutiliza la sesion ya autenticada del
CLI, igual que un `claude -p` cualquiera que el usuario lanzaria a mano.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import yaml

from orchestrator import plan as plan_module
from orchestrator import project, storage

TIMEOUT = 600

SYSTEM_PROMPT = """Eres el arquitecto de un orquestador hibrido: tu diseñas, un worker
DeepSeek implementa despues. Tu unico trabajo aqui es estudiar el repositorio (con Read,
Glob y Grep -- no puedes editar nada) y devolver un plan de tasks, no codigo.

Reglas que marcan la diferencia (rompelas y el plan sera inutil):
- nombra ficheros y simbolos concretos, con su ruta real del repo que acabas de leer;
- di explicitamente que NO debe tocar cada task;
- una task por objetivo, no mezcles varios objetivos en una;
- 'files' es propiedad exclusiva de esa task: si dos tasks se solapan, se serializan y se
  pierde el paralelismo -- reparte por directorios disjuntos;
- si dos tasks van en paralelo (sin depends_on entre ellas) y comparten un contrato (una
  API, un shape de datos), fijalo palabra por palabra en AMBAS descripciones -- ninguna
  vera el codigo de la otra mientras trabaja. Si el contrato es complejo o no puedes
  fijarlo con precision, no las pongas en paralelo: usa depends_on.
- una sola task si la feature es pequeña; no la trocees sin necesidad.

Devuelve solo el plan (feature + tasks), en el formato pedido. No implementes nada tu."""


def propose(root: Path, description: str, run_id: int,
           on_event: Callable[[str, str], None] = lambda kind, text: None) -> dict:
    """Pide al CLI de Claude un plan para 'description'. No lanza nada: si el plan es
    valido, crea la fila 'plan' hija (queued) a la espera de que alguien la confirme con
    'agents launch'."""
    if not shutil.which("claude"):
        return {"status": "failed", "summary": "falta el CLI de Claude Code ('claude') en el PATH"}

    on_event("architect", "estudiando el repositorio...")
    schema = plan_module.Plan.model_json_schema()
    try:
        r = subprocess.run(
            ["claude", "-p", description,
             "--output-format", "json",
             "--allowedTools", "Read,Glob,Grep",
             "--permission-mode", "dontAsk",
             "--append-system-prompt", SYSTEM_PROMPT,
             "--json-schema", json.dumps(schema)],
            cwd=root, capture_output=True, text=True, timeout=TIMEOUT,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"status": "aborted", "summary": f"ARCHITECT_ABORTED: timeout de {TIMEOUT}s"}
    except FileNotFoundError:
        return {"status": "failed", "summary": "falta el CLI de Claude Code ('claude') en el PATH"}

    if r.returncode != 0:
        return {"status": "failed", "summary": f"claude -p fallo: {(r.stderr or r.stdout).strip()[:1500]}"}

    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        return {"status": "failed", "summary": f"salida no es JSON valido: {exc}",
               "issues": [r.stdout[:1500]]}

    if payload.get("is_error"):
        return {"status": "failed", "summary": f"claude -p: {payload.get('result', '')[:1500]}"}

    estructurado = payload.get("structured_output")
    if not estructurado:
        return {"status": "failed", "summary": "el arquitecto no devolvio un plan estructurado",
               "issues": [str(payload.get("result", ""))[:1500]]}

    try:
        plan = plan_module.Plan(**estructurado)
    except Exception as exc:  # noqa: BLE001 - validacion de pydantic, queremos el detalle
        return {"status": "failed", "summary": f"el plan propuesto no es valido: {exc}",
               "issues": [json.dumps(estructurado, ensure_ascii=False)[:1500]]}

    cfg = project.resolved(root) or {}
    plan_id = storage.create_run(
        root, yaml.safe_dump(estructurado, sort_keys=False, allow_unicode=True),
        kind="plan", feature=plan.feature, parent_id=run_id,
        project_name=cfg.get("project", {}).get("name"),
    )
    on_event("architect", f"plan propuesto: {len(plan.tasks)} tasks (run {plan_id})")
    return {
        "status": "completed",
        "summary": f"plan propuesto: {len(plan.tasks)} tasks -- confirma con 'agents launch {plan_id}'",
        "cost": payload.get("total_cost_usd", 0.0),
        "plan_run_id": plan_id,
    }
