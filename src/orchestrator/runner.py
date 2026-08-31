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
from orchestrator import project, router, scheduler, storage, worker


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
        else:
            limits = json.loads(row["limits"]) if row["limits"] else {}
            escalate = limits.pop("escalate", True)
            return router.run_escalated(root, row["task"], config, key, run_id,
                                        model=row["model"], limits=limits, escalate=escalate,
                                        on_event=on_event,
                                        extra_fields={"task_file": row["task_file"],
                                                      "project_name": row["project_name"]})
    except Exception as exc:  # noqa: BLE001 - el fallo se persiste, no se pierde
        traceback.print_exc()
        result = {"status": "failed", "summary": f"{exc.__class__.__name__}: {exc}"}
    storage.save_result(run_id, result)
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
