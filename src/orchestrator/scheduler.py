"""Scheduler: tasks de un plan en paralelo, cada worker en su worktree (Fase 5)."""

from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

from orchestrator import plan as plan_module
from orchestrator import router, storage, worktree

TASK_PROMPT = """# Feature: {feature}
# Task: {task_id}

{description}

# Ficheros que te pertenecen
{scope}

Trabajas en un worktree aislado: otros workers estan modificando en paralelo otras
partes del repositorio. Limitate a tus ficheros; no toques los de otras tasks.
"""


def _prompt(feature: str, task: plan_module.Task) -> str:
    return TASK_PROMPT.format(
        feature=feature,
        task_id=task.id,
        description=task.description,
        scope="\n".join(f"- {p}" for p in task.files) or "- (sin ownership declarado)",
    )


def _run_task(root: Path, feature: str, task: plan_module.Task, run_id: int, plan_id: int, config: dict,
              api_key: str, base: str, dep_shas: list[str],
              on_event: Callable[[str, str], None]) -> dict:
    # plan_id en el nombre: dos ejecuciones de la misma feature no se pisan ni se borran ramas
    name = f"{worktree.slug(feature)}-{plan_id}-{worktree.slug(task.id)}"
    try:
        storage.update_run(run_id, status="running", started_at=storage.now())
        path, branch = worktree.create(root, name, base)
        storage.update_run(run_id, worktree=str(path), branch=branch)
        if dep_shas:
            worktree.cherry_pick(path, dep_shas)
        on_event("task", f"[{task.id}] arrancando en {path.name}")
        result = router.run_escalated(
            path, _prompt(feature, task), config, api_key, run_id,
            model=None if task.model in (None, "auto") else task.model,
            on_event=lambda kind, text: on_event(kind, f"[{task.id}] {text}"),
            extra_fields={"feature": feature, "task_id": task.id,
                          "project_name": config.get("project", {}).get("name")},
        )
        run_id = result.get("run_id", run_id)
        storage.update_run(run_id, worktree=str(path), branch=branch)
        # el worker solo recibe el scope por prompt; esto lo verifica de verdad para hybrid-review
        fuera = [f for f in result.get("files_changed", []) if not plan_module.in_scope(f, task.files)]
        if fuera:
            result.setdefault("issues", []).append(f"SCOPE_VIOLATION: fuera de {task.files}: {fuera}")
        if result["status"] in ("completed", "validation_failed"):
            result["sha"] = worktree.commit_all(path, f"agents({task.id}): {feature}")
        result["branch"] = branch
        result["worktree"] = str(path)
    except Exception as exc:  # noqa: BLE001 - el fallo de una task no tumba el plan
        result = {"status": "failed", "summary": f"{exc.__class__.__name__}: {exc}"}
    on_event("task", f"[{task.id}] {result['status']}")
    storage.save_result(run_id, result)
    return result


def execute_plan(root: Path, feature_plan: plan_module.Plan, config: dict, api_key: str,
                 parent_id: int | None = None,
                 on_event: Callable[[str, str], None] = lambda kind, text: None) -> dict:
    """Ejecuta el plan completo y deja una rama de integracion."""
    started = time.time()
    # identifica esta ejecucion del plan en los nombres de rama/worktree: sin esto, relanzar
    # la misma feature borraria (via worktree.create) los worktrees y ramas de la vez anterior
    plan_id = parent_id if parent_id is not None else int(started)
    base = worktree.head(root)
    max_parallel = config.get("workers", {}).get("max_parallel", 3)
    default_model = config.get("workers", {}).get("default_model")
    feature = feature_plan.feature

    runs = {
        task.id: storage.create_run(
            root, task.description, feature=feature, task_id=task.id, parent_id=parent_id,
            project_name=config.get("project", {}).get("name"),
            model=task.model if task.model not in (None, "auto") else default_model,
        )
        for task in feature_plan.tasks
    }
    pending = {task.id: task for task in feature_plan.tasks}
    results: dict[str, dict] = {}
    shas: dict[str, str] = {}
    futures: dict = {}

    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        while pending or futures:
            corriendo = list(futures.values())
            for task_id, task in sorted(pending.items()):
                if len(futures) >= max_parallel:
                    break
                if any(dep not in results for dep in task.depends_on):
                    continue
                fallidas = [d for d in task.depends_on if results[d]["status"] != "completed"]
                if fallidas:
                    results[task_id] = {"status": "skipped", "summary": f"dependencias no completadas: {fallidas}"}
                    storage.save_result(runs[task_id], results[task_id])
                    on_event("task", f"[{task_id}] skipped")
                    del pending[task_id]
                    continue
                # ownership: no dos tasks tocando los mismos ficheros a la vez
                if any(plan_module.overlap(task.files, otra.files) for otra in corriendo):
                    continue
                dep_shas = [shas[d] for d in task.depends_on if d in shas]
                future = pool.submit(_run_task, root, feature, task, runs[task_id], plan_id, config,
                                     api_key, base, dep_shas, on_event)
                futures[future] = task
                corriendo.append(task)
                del pending[task_id]

            if not futures:
                break  # nada ejecutable: el resto quedo saltado
            for future in wait(futures, return_when=FIRST_COMPLETED).done:
                task = futures.pop(future)
                results[task.id] = future.result()
                if results[task.id].get("sha"):
                    shas[task.id] = results[task.id]["sha"]

    completadas = all(r["status"] == "completed" for r in results.values())
    status = "completed" if completadas and len(results) == len(feature_plan.tasks) else "failed"
    branch = summary = None
    if status == "completed" and shas:
        name = f"{worktree.slug(feature)}-{plan_id}-integration"
        path, branch = worktree.create(root, name, base)
        on_event("task", f"[integracion] cherry-pick en {path.name}")
        try:
            worktree.cherry_pick(path, [shas[t.id] for t in feature_plan.order() if t.id in shas])
        except worktree.IntegrationConflict as exc:
            status, summary = "integration_conflict", str(exc)

    return {
        "status": status,
        "summary": summary or f"{sum(r['status'] == 'completed' for r in results.values())}"
                              f"/{len(feature_plan.tasks)} tasks completadas",
        "feature": feature,
        "integration_branch": branch,
        "tasks": {tid: {k: v for k, v in r.items() if k != "summary"} for tid, r in results.items()},
        "files_changed": sorted({f for r in results.values() for f in r.get("files_changed", [])}),
        "issues": [i for r in results.values() for i in r.get("issues", [])],
        "iterations": sum(r.get("iterations", 0) for r in results.values()),
        "tool_calls": sum(r.get("tool_calls", 0) for r in results.values()),
        "tool_errors": sum(r.get("tool_errors", 0) for r in results.values()),
        "duration_seconds": round(time.time() - started, 1),
        "duration_secuencial": round(sum(r.get("duration_seconds", 0) for r in results.values()), 1),
        "usage": {
            key: sum(r.get("usage", {}).get(key, 0) for r in results.values())
            for key in ("prompt_tokens", "prompt_cache_hit_tokens", "completion_tokens")
        },
        "cost": round(sum(r.get("cost", 0) for r in results.values()), 6),
    }
