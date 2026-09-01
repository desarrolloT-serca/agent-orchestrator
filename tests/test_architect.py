"""Checks de architect.propose: subprocess.run mockeado -- nunca gasta tokens reales de
Claude en los tests. Ver tests/test_cli_integrate.py para el estilo de fixtures similar."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orchestrator import architect, storage

TMP = Path(tempfile.mkdtemp())
storage.DB_PATH = TMP / "orchestrator.db"
storage.LOGS = TMP / "logs"


def _repo() -> Path:
    return Path(tempfile.mkdtemp()).resolve()


def _fake_run(payload: dict, returncode: int = 0):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout=json.dumps(payload), stderr="")
    return run


def test_falla_claro_si_no_hay_claude_en_el_path(monkeypatch):
    monkeypatch.setattr(architect.shutil, "which", lambda _: None)
    root = _repo()
    result = architect.propose(root, "algo", run_id=1)
    assert result["status"] == "failed" and "PATH" in result["summary"]


def test_plan_valido_crea_la_fila_hija_en_cola(monkeypatch):
    monkeypatch.setattr(architect.shutil, "which", lambda _: "/usr/bin/claude")
    payload = {
        "is_error": False,
        "total_cost_usd": 0.05,
        "structured_output": {
            "feature": "notificaciones",
            "tasks": [{"id": "backend", "description": "API", "files": ["src/server/**"]}],
        },
    }
    monkeypatch.setattr(architect.subprocess, "run", _fake_run(payload))
    root = _repo()
    parent_id = storage.create_run(root, "añade notificaciones", kind="architect")

    result = architect.propose(root, "añade notificaciones", parent_id)

    assert result["status"] == "completed" and result["cost"] == 0.05
    hija = storage.get_run(result["plan_run_id"])
    assert hija["kind"] == "plan" and hija["status"] == "queued"
    assert hija["feature"] == "notificaciones" and hija["parent_id"] == parent_id
    assert "backend" in hija["task"]  # el YAML del plan quedo guardado


def test_json_invalido_falla_sin_reventar(monkeypatch):
    monkeypatch.setattr(architect.shutil, "which", lambda _: "/usr/bin/claude")

    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="esto no es json", stderr="")
    monkeypatch.setattr(architect.subprocess, "run", run)
    root = _repo()
    result = architect.propose(root, "algo", run_id=1)
    assert result["status"] == "failed" and "JSON" in result["summary"]


def test_plan_que_no_valida_contra_el_schema_falla(monkeypatch):
    monkeypatch.setattr(architect.shutil, "which", lambda _: "/usr/bin/claude")
    payload = {"is_error": False, "structured_output": {"feature": "x"}}  # falta 'tasks'
    monkeypatch.setattr(architect.subprocess, "run", _fake_run(payload))
    root = _repo()
    result = architect.propose(root, "algo", run_id=1)
    assert result["status"] == "failed" and "no es valido" in result["summary"]


def test_is_error_del_cli_se_reporta_como_fallo(monkeypatch):
    monkeypatch.setattr(architect.shutil, "which", lambda _: "/usr/bin/claude")
    payload = {"is_error": True, "result": "no pude leer el repo"}
    monkeypatch.setattr(architect.subprocess, "run", _fake_run(payload))
    root = _repo()
    result = architect.propose(root, "algo", run_id=1)
    assert result["status"] == "failed" and "no pude leer el repo" in result["summary"]


def test_returncode_no_cero_falla(monkeypatch):
    monkeypatch.setattr(architect.shutil, "which", lambda _: "/usr/bin/claude")

    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="algo exploto")
    monkeypatch.setattr(architect.subprocess, "run", run)
    root = _repo()
    result = architect.propose(root, "algo", run_id=1)
    assert result["status"] == "failed" and "algo exploto" in result["summary"]


def test_timeout_se_reporta_como_aborted(monkeypatch):
    monkeypatch.setattr(architect.shutil, "which", lambda _: "/usr/bin/claude")

    def run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 0))
    monkeypatch.setattr(architect.subprocess, "run", run)
    root = _repo()
    result = architect.propose(root, "algo", run_id=1)
    assert result["status"] == "aborted" and "ARCHITECT_ABORTED" in result["summary"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
