"""Router Flash/Pro: eleccion de modelo, reintento y escalado automatico (Fase 7)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from orchestrator import storage, worker

FLASH = "deepseek-v4-flash"
PRO = "deepseek-v4-pro"

# politica del roadmap: flash -> retry flash -> pro/high; si ya arrancamos en pro, pro/max al final
ESCALATION = ((FLASH, "high"), (FLASH, "high"), (PRO, "high"))
PRO_ESCALATION = ((PRO, "high"), (PRO, "high"), (PRO, "max"))

# estados que justifican otro intento (un plan invalido o una API caida, no)
RETRYABLE = ("validation_failed", "aborted")

DURA = re.compile(
    r"\b(refactor\w*|migraci\w+|arquitectur\w+|concurren\w+|deadlock|race condition|"
    r"rendimiento|performance|cross-module|multiples? modulos|reescrib\w+|seguridad)\b",
    re.I,
)

REINTENTO = """

# Intento anterior fallido

El intento previo termino en `{status}`: {summary}

Problemas detectados:
{issues}

El codigo parcial sigue en el repositorio. Diagnostica que fallo antes de tocar nada
y corrigelo; no repitas el mismo enfoque.
"""


def classify(task: str) -> str:
    """Clasificacion por reglas simples (roadmap Fase 7): simple | normal | hard."""
    if DURA.search(task):
        return "hard"
    return "simple" if len(task) < 400 else "normal"


def attempts(config: dict, model: str | None, task: str) -> tuple[tuple[str, str], ...]:
    """Secuencia de (modelo, reasoning) a intentar.

    El primer intento (y su repeticion) usan workers.reasoning del proyecto; la escalada
    a Pro siempre arranca fuerte (high, o max si ya se habia arrancado en Pro) porque es
    la ultima red de seguridad, no el sitio para heredar un reasoning bajo.
    """
    configurado = model or config.get("workers", {}).get("default_model", FLASH)
    if configurado == "auto":
        configurado = PRO if classify(task) == "hard" else FLASH
    reasoning = config.get("workers", {}).get("reasoning", "high")
    if configurado == PRO:
        return ((PRO, reasoning), (PRO, reasoning), (PRO, "max"))
    return ((FLASH, reasoning), (FLASH, reasoning), (PRO, "high"))


def _nota(result: dict) -> str:
    issues = result.get("issues") or []
    return REINTENTO.format(
        status=result.get("status"),
        summary=(result.get("summary") or "")[:1500],
        issues="\n".join(f"- {i}" for i in issues) or "- (sin detalle; revisa los tests)",
    )


def run_escalated(
    root: Path,
    task: str,
    config: dict,
    api_key: str,
    run_id: int,
    model: str | None = None,
    limits: dict | None = None,
    escalate: bool = True,
    on_event: Callable[[str, str], None] = lambda kind, text: None,
    extra_fields: dict | None = None,
) -> dict:
    """Ejecuta la tarea reintentando y escalando de modelo. Cada intento es un run propio."""
    max_parallel = config.get("workers", {}).get("max_parallel", 3)
    storage.acquire_slot(run_id, max_parallel, on_event=on_event)
    # 'root' aqui es el worktree donde trabaja el worker, no la raiz del proyecto: para el
    # create_run del reintento hace falta la raiz real (columna 'project'), si no el reintento
    # queda huerfano de 'agents status/history/metrics' del proyecto y de la cola global
    proyecto = storage.get_run(run_id)["project"]
    secuencia = attempts(config, model, task)
    if not escalate:
        secuencia = secuencia[:1]
    actual, result = run_id, {}

    for i, (modelo, reasoning) in enumerate(secuencia):
        storage.update_run(actual, model=modelo, reasoning=reasoning)
        texto = task if i == 0 else task + _nota(result)
        on_event("task", f"intento {i + 1}/{len(secuencia)} con {modelo}/{reasoning}")
        result = worker.run(root, texto, config, api_key, model=modelo, reasoning=reasoning,
                            limits=limits, on_event=on_event)
        result["attempt"] = i + 1
        storage.save_result(actual, result)
        if result["status"] not in RETRYABLE or i == len(secuencia) - 1:
            break
        siguiente = secuencia[i + 1]
        on_event("task", f"{result['status']}: escalando a {siguiente[0]}/{siguiente[1]}")
        actual = storage.create_run(proyecto, task, parent_id=actual, model=siguiente[0],
                                    kind="retry", **(extra_fields or {}))
        # el slot ya esta reservado (mismo hilo/proceso que el intento anterior); sin esto la
        # fila del reintento quedaria 'queued' mientras trabaja de verdad, y acquire_slot de
        # otros procesos la ignoraria al contar workers activos
        storage.update_run(actual, status="running", started_at=storage.now())

    result["run_id"] = actual
    return result
