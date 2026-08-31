"""Check de agents stop: no debe marcar 'stopped' lo que no ha detenido de verdad."""

import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner

from orchestrator import cli, storage

TMP = Path(tempfile.mkdtemp())
storage.DB_PATH = TMP / "orchestrator.db"
storage.LOGS = TMP / "logs"

runner = CliRunner()


def _repo() -> Path:
    root = Path(tempfile.mkdtemp()).resolve()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


@contextmanager
def _en(root: Path):
    previo = Path.cwd()
    os.chdir(root)
    try:
        yield
    finally:
        os.chdir(previo)


def test_stop_no_miente_sobre_un_plan_en_marcha():
    root = _repo()
    run_id = storage.create_run(root, "feature: x\ntasks: []", kind="plan", status="running", pid=None)

    with _en(root):
        result = runner.invoke(cli.app, ["stop", str(run_id)])

    assert result.exit_code != 0
    assert storage.get_run(run_id)["status"] == "running"  # sigue corriendo de verdad


def test_stop_no_miente_sobre_una_task_de_plan_en_marcha():
    root = _repo()
    # las tasks de un plan corren como hilos: nunca tienen pid propio
    run_id = storage.create_run(root, "algo", kind="task", feature="x", task_id="backend",
                                status="running", pid=None)

    with _en(root):
        result = runner.invoke(cli.app, ["stop", str(run_id)])

    assert result.exit_code != 0
    assert storage.get_run(run_id)["status"] == "running"


def test_stop_marca_detenido_lo_que_solo_estaba_en_cola():
    root = _repo()
    run_id = storage.create_run(root, "algo", kind="task", status="queued", pid=None)

    with _en(root):
        result = runner.invoke(cli.app, ["stop", str(run_id)])

    assert result.exit_code == 0
    assert storage.get_run(run_id)["status"] == "stopped"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
