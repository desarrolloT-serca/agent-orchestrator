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
    storage.update_run(run_id, status="running", pid=os.getpid(), started_at=storage.now())
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
        storage.save_result(run_id, result)
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


def spawn(run_id: int, root: Path) -> int:
    """Lanza el run en un proceso independiente de esta sesion. Devuelve el pid."""
    log = storage.log_path(run_id).open("a", encoding="utf-8", buffering=1)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        [sys.executable, "-m", "orchestrator.runner", str(run_id)],
        cwd=root, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
        creationflags=flags if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    storage.update_run(run_id, pid=proc.pid)
    return proc.pid


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    execute(int(sys.argv[1]), on_event=lambda kind, text: print(text, flush=True))
