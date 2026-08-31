"""Checks del plan multiworker: dependencias, paralelismo, worktrees e integracion (sin red)."""

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orchestrator import plan as plan_module
from orchestrator import project, scheduler, storage, worker, worktree

TMP = Path(tempfile.mkdtemp())
storage.DB_PATH = TMP / "orchestrator.db"
storage.LOGS = TMP / "logs"

PLAN = {
    "feature": "notifications",
    "tasks": [
        {"id": "backend", "description": "API", "files": ["src/server/**"]},
        {"id": "frontend", "description": "UI", "files": ["src/app/**"]},
        {"id": "tests", "description": "e2e", "depends_on": ["backend", "frontend"]},
    ],
}

visto: dict[str, list[str]] = {}


def _repo() -> Path:
    root = Path(tempfile.mkdtemp()).resolve()
    (root / "README.md").write_text("lab\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
                   cwd=root, check=True)
    project.save(root, project.detect(root))
    return root


def _fake_worker(root, task, config, api_key, model=None, reasoning=None, limits=None,
                 on_event=lambda k, t: None):
    """Escribe un fichero con el id de la task y anota que vio en su worktree."""
    task_id = [l for l in task.splitlines() if l.startswith("# Task:")][0].split(":")[1].strip()
    visto[task_id] = sorted(p.name for p in root.glob("*.txt"))
    (root / f"{task_id}.txt").write_text(task_id, encoding="utf-8")
    return {"status": "completed", "summary": f"{task_id} hecho", "files_changed": [f"{task_id}.txt"],
            "tests": {}, "issues": [], "model": model or "fake", "iterations": 1, "tool_calls": 1,
            "tool_errors": 0, "duration_seconds": 0.1, "usage": {"prompt_tokens": 10}, "cost": 0.001}


def test_orden_topologico_y_ciclos():
    p = plan_module.Plan(**PLAN)
    assert [t.id for t in p.order()][-1] == "tests"
    try:
        plan_module.Plan(feature="x", tasks=[{"id": "a", "description": "a", "depends_on": ["b"]},
                                             {"id": "b", "description": "b", "depends_on": ["a"]}])
    except Exception as exc:
        assert "circulares" in str(exc)
    else:
        raise AssertionError("deberia detectar el ciclo")


def test_ownership_solapado():
    assert plan_module.overlap(["src/server/**"], ["src/server/api.ts"])
    assert not plan_module.overlap(["src/server/**"], ["src/app/**"])
    assert not plan_module.overlap([], ["src/app/**"])


def test_plan_completo_con_worktrees_e_integracion():
    root = _repo()
    worker.run = _fake_worker
    result = scheduler.execute_plan(root, plan_module.Plan(**PLAN), project.load(root), "fake-key")

    assert result["status"] == "completed"
    assert result["integration_branch"] == "agents/notifications-integration"
    assert set(result["tasks"]) == {"backend", "frontend", "tests"}

    # la task dependiente vio el trabajo de sus dos dependencias (cherry-pick previo)
    assert visto["tests"] == ["backend.txt", "frontend.txt"]
    assert visto["backend"] == [] and visto["frontend"] == []

    # la rama de integracion tiene los tres commits
    integracion = root / worktree.WORKTREES / "notifications-integration"
    assert sorted(p.name for p in integracion.glob("*.txt")) == ["backend.txt", "frontend.txt", "tests.txt"]

    # el repositorio principal sigue intacto
    assert not (root / "backend.txt").exists()
    sucio = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                           text=True).stdout.split()
    assert sucio == ["??", ".agent/"]  # solo la config; los worktrees se auto-ignoran


def test_dependencia_fallida_salta_la_task():
    root = _repo()

    def falla(root_, task, config, api_key, model=None, reasoning=None, limits=None,
              on_event=lambda k, t: None):
        if "# Task: backend" in task:
            return {"status": "aborted", "summary": "WORKER_ABORTED", "usage": {}, "cost": 0.0}
        return _fake_worker(root_, task, config, api_key, model, reasoning, limits, on_event)

    worker.run = falla
    result = scheduler.execute_plan(root, plan_module.Plan(**PLAN), project.load(root), "fake-key")
    assert result["status"] == "failed"
    assert result["tasks"]["tests"]["status"] == "skipped"
    assert result["integration_branch"] is None


def test_clean_conserva_la_rama_de_integracion():
    root = _repo()
    base = worktree.head(root)
    worktree.create(root, "x-modelo", base)
    path, _ = worktree.create(root, "x-integration", base)

    worktree.remove(root, "x-modelo")                      # task: worktree y rama
    worktree.remove(root, "x-integration", drop_branch=False)  # integracion: solo el worktree

    ramas = subprocess.run(["git", "branch", "--list", "agents/*"], cwd=root,
                           capture_output=True, text=True).stdout
    assert "agents/x-integration" in ramas and "agents/x-modelo" not in ramas
    assert not path.exists()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
