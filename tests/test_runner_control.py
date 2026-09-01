"""Checks de runner.stop/runner.retry: la logica compartida entre 'agents stop/retry' y
el dashboard (tui.py). Ver tests/test_cli_stop.py para el comportamiento end-to-end del CLI."""

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orchestrator import runner, storage

TMP = Path(tempfile.mkdtemp())
storage.DB_PATH = TMP / "orchestrator.db"
storage.LOGS = TMP / "logs"


def _repo() -> Path:
    root = Path(tempfile.mkdtemp()).resolve()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def test_stop_run_inexistente():
    rechazado, mensaje = runner.stop(999999)
    assert rechazado and "no existe" in mensaje


def test_stop_run_ya_inactivo():
    root = _repo()
    run_id = storage.create_run(root, "algo", status="completed")
    rechazado, mensaje = runner.stop(run_id)
    assert rechazado and "ya no esta activo" in mensaje
    assert storage.get_run(run_id)["status"] == "completed"  # no lo toca


def test_stop_plan_o_task_de_plan_no_se_puede_matar():
    root = _repo()
    run_id = storage.create_run(root, "feature: x\ntasks: []", kind="plan", status="running", pid=None)
    rechazado, mensaje = runner.stop(run_id)
    assert rechazado and "corren como hilos" in mensaje
    assert storage.get_run(run_id)["status"] == "running"  # sigue corriendo de verdad

    run_id2 = storage.create_run(root, "algo", kind="task", feature="x", task_id="backend",
                                 status="running", pid=None)
    rechazado2, _ = runner.stop(run_id2)
    assert rechazado2
    assert storage.get_run(run_id2)["status"] == "running"


def test_stop_en_cola_se_marca_stopped_sin_matar_nada():
    root = _repo()
    run_id = storage.create_run(root, "algo", kind="task", status="queued", pid=None)
    rechazado, mensaje = runner.stop(run_id)
    assert not rechazado
    assert "cola" in mensaje
    assert storage.get_run(run_id)["status"] == "stopped"


def test_stop_pid_reciclado_se_marca_stopped_sin_matar():
    root = _repo()
    # un pid que existio y ya no: no deberia intentar matarlo, pero si marcar 'stopped'
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    run_id = storage.create_run(root, "algo", kind="task", status="running", pid=proc.pid)
    rechazado, mensaje = runner.stop(run_id)
    assert not rechazado
    assert "ya no es el worker" in mensaje
    assert storage.get_run(run_id)["status"] == "stopped"


def test_retry_clona_feature_task_id_y_modelo():
    root = _repo()
    run_id = storage.create_run(root, "haz X", kind="task", feature="notif", task_id="backend",
                                model="deepseek-v4-flash", project_name="demo",
                                task_file="task.md", status="validation_failed")
    new_id = runner.retry(run_id)
    nuevo = storage.get_run(new_id)
    assert nuevo["task"] == "haz X"
    assert nuevo["kind"] == "task" and nuevo["feature"] == "notif" and nuevo["task_id"] == "backend"
    assert nuevo["model"] == "deepseek-v4-flash"
    assert nuevo["parent_id"] == run_id
    assert nuevo["status"] == "queued"  # no lo lanza, solo lo clona


def test_retry_permite_forzar_otro_modelo():
    root = _repo()
    run_id = storage.create_run(root, "haz X", model="deepseek-v4-flash", status="aborted")
    new_id = runner.retry(run_id, model="deepseek-v4-pro")
    assert storage.get_run(new_id)["model"] == "deepseek-v4-pro"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
