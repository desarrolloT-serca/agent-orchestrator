"""Ejecuta un run guardado en SQLite. Se invoca en foreground o como proceso desacoplado."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Callable

import yaml

from orchestrator import plan as plan_module
from orchestrator import project, router, scheduler, storage, worker, worktree


def execute(run_id: int, on_event: Callable[[str, str], None] = lambda kind, text: None) -> dict:
    """Ejecuta el run: carga config, lanza el worker y persiste el resultado."""
    row = storage.get_run(run_id)
    if row is None:
        raise SystemExit(f"run {run_id} no encontrado")
    root = Path(row["project"])
    fields = {"pid": os.getpid(), "started_at": storage.now()}
    if row["kind"] == "plan":
        # el contenedor del plan no consume slot de worker (solo orquesta); sus tasks
        # hijas pasan por acquire_slot y se quedan 'queued' hasta que haya hueco global
        fields["status"] = "running"
    storage.update_run(run_id, **fields)
    try:
        config = project.resolved(root) or {}
        key = project.api_key(root)
        if not key:
            raise RuntimeError("falta DEEPSEEK_API_KEY")
        if row["kind"] == "plan":
            result = scheduler.execute_plan(root, plan_module.Plan(**yaml.safe_load(row["task"])),
                                            config, key, parent_id=run_id, on_event=on_event)
            storage.save_result(run_id, result)
            return result
        return _execute_task(root, row, run_id, config, key, on_event)
    except Exception as exc:  # noqa: BLE001 - el fallo se persiste, no se pierde
        traceback.print_exc()
        result = {"status": "failed", "summary": f"{exc.__class__.__name__}: {exc}"}
        try:
            storage.save_result(run_id, result)
        except Exception:  # noqa: BLE001 - p.ej. disco lleno: no ocultes el fallo original con este
            traceback.print_exc()
        return result


def _execute_task(root: Path, row, run_id: int, config: dict, key: str,
                  on_event: Callable[[str, str], None]) -> dict:
    """Tarea suelta: aislada en su propio worktree, igual que las tasks de un plan.

    El checkout principal no se toca nunca, tampoco para una unica tarea (agents run task.md):
    si no fuera asi, trabajo sin commitear del usuario o una sesion en paralelo podrian
    mezclarse con lo que edita DeepSeek.
    """
    limits = json.loads(row["limits"]) if row["limits"] else {}
    escalate = limits.pop("escalate", True)
    base = worktree.head(root)
    path, branch = worktree.create(root, f"run-{run_id}", base)
    storage.update_run(run_id, worktree=str(path), branch=branch)

    result = router.run_escalated(path, row["task"], config, key, run_id,
                                  model=row["model"], limits=limits, escalate=escalate,
                                  on_event=on_event,
                                  extra_fields={"task_file": row["task_file"],
                                                "project_name": row["project_name"]})
    final_id = result.get("run_id", run_id)
    if result["status"] in ("completed", "validation_failed"):
        result["sha"] = worktree.commit_all(path, f"agents(run-{run_id}): {row['task'].splitlines()[0][:60]}")
    result["branch"] = branch
    result["worktree"] = str(path)
    storage.update_run(final_id, worktree=str(path), branch=branch)
    storage.save_result(final_id, result)
    return result


def stop(run_id: int) -> tuple[bool, str]:
    """Intenta detener un run. Devuelve (rechazado, mensaje).

    rechazado=True: no se ha tocado nada (ya no esta activo, o es un plan/task-de-plan que
    corre como hilo del proceso que lo lanzo -- no hay proceso propio que matar).
    rechazado=False: se ha marcado 'stopped' (con o sin proceso real que matar; ver mensaje).

    Compartida por 'agents stop' y el dashboard: una sola implementacion de "que runs se
    pueden parar y como", no una copia en cada sitio que la invoca.
    """
    import psutil

    row = storage.get_run(run_id)
    if row is None:
        return True, f"no existe el run {run_id}"
    if row["status"] not in storage.ACTIVE:
        return True, f"el run {run_id} ya no esta activo ({row['status']})"
    if row["kind"] == "plan" or (row["status"] == "running" and not row["pid"]):
        # un plan, o una de sus tasks: corren como hilos dentro del proceso que lanzo el plan,
        # no como proceso propio. No hay nada que matar aqui, y marcarlo "stopped" mentiria:
        # el worker seguiria trabajando (y gastando DeepSeek) mientras el estado dice detenido.
        return True, ("es un plan (o una de sus tasks): corren como hilos del proceso que lo "
                      "lanzo. Mata ese proceso (o su terminal) para detenerlas de verdad.")
    if not row["pid"]:
        detalle = "no tenia un proceso propio todavia (sigue en cola)"
    elif not storage.pid_es_worker(row["pid"]):
        detalle = f"el pid {row['pid']} ya no es el worker (terminado, reutilizado, o desaparecio)"
    else:
        try:
            proc = psutil.Process(row["pid"])
            for child in proc.children(recursive=True):
                child.terminate()
            proc.terminate()
            detalle = f"pid {row['pid']} detenido"
        except psutil.Error as exc:
            detalle = f"no se pudo matar el pid {row['pid']}: {exc}"
    storage.update_run(run_id, status="stopped", finished_at=storage.now())
    return False, f"run {run_id} marcado 'stopped' ({detalle})"


def retry(run_id: int, model: str | None = None) -> int:
    """Crea (sin lanzar) el run que reintenta run_id: mismo tipo, feature/task_id y limites.

    Compartida por 'agents retry' y el dashboard. Lanzarlo (spawn/foreground) es cosa del
    llamante: aqui solo se clona el run.
    """
    row = storage.get_run(run_id)
    return storage.create_run(
        Path(row["project"]), row["task"], kind=row["kind"] or "task",
        feature=row["feature"], task_id=row["task_id"], project_name=row["project_name"],
        task_file=row["task_file"], model=model or row["model"], limits=row["limits"],
        parent_id=run_id,
    )


def spawn(run_id: int, root: Path) -> int:
    """Lanza el run en un proceso independiente de esta sesion. Devuelve el pid."""
    log = storage.log_path(run_id).open("a", encoding="utf-8", buffering=1)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    # AGENT_ORCHESTRATOR_HOME explicito: el hijo reimporta storage.py de cero y por defecto
    # usaria ~/.agent-orchestrator real, aunque el padre este apuntando a una BD de prueba
    # (storage.DB_PATH reasignado en memoria no se hereda solo). Sin esto, un test que
    # dispara spawn() puede acabar escribiendo runs falsos sobre datos reales.
    env = {**os.environ, "AGENT_ORCHESTRATOR_HOME": str(storage.DB_PATH.parent)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "orchestrator.runner", str(run_id)],
        cwd=root, stdout=log, stderr=log, stdin=subprocess.DEVNULL, env=env,
        creationflags=flags if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    storage.update_run(run_id, pid=proc.pid)
    return proc.pid


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    execute(int(sys.argv[1]), on_event=lambda kind, text: print(text, flush=True))
