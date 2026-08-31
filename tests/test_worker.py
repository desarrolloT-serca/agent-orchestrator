"""Check del agent loop con un DeepSeek falso (sin red)."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orchestrator import deepseek, worker

CONFIG = {"project": {"name": "demo"}, "workers": {"default_model": "deepseek-v4-flash"}}


def _repo() -> Path:
    root = Path(tempfile.mkdtemp()).resolve()
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _call(id_, name, args):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _scripted(responses):
    it = iter(responses)

    def fake_chat(messages, tools, api_key, model=None, reasoning="high", timeout=600):
        return {"message": next(it), "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return fake_chat


def test_loop_edita_y_termina(monkeypatch=None):
    root = _repo()
    deepseek.chat = _scripted([
        {"role": "assistant", "content": None, "tool_calls": [_call("1", "read_file", {"path": "app.py"})]},
        {"role": "assistant", "content": None, "tool_calls": [
            _call("2", "edit_file", {"path": "app.py", "old_string": "VALUE = 1", "new_string": "VALUE = 2"})]},
        {"role": "assistant", "content": "Cambiado VALUE a 2."},
    ])
    result = worker.run(root, "Pon VALUE a 2", CONFIG, "fake-key")
    assert result["status"] == "completed"
    assert result["files_changed"] == ["app.py"]
    assert result["iterations"] == 3 and result["tool_calls"] == 2 and result["tool_errors"] == 0
    assert result["usage"]["prompt_tokens"] == 30
    assert (root / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_aborta_por_limite_de_iteraciones():
    root = _repo()
    loop = {"role": "assistant", "content": None, "tool_calls": [_call("1", "list_directory", {})]}
    deepseek.chat = _scripted([loop] * 5)
    result = worker.run(root, "bucle", CONFIG, "fake-key", limits={"max_iterations": 3})
    assert result["status"] == "aborted" and "3 iteraciones" in result["summary"]


def test_aborta_por_errores_de_herramienta():
    root = _repo()
    bad = {"role": "assistant", "content": None, "tool_calls": [_call("1", "read_file", {"path": "../fuera"})]}
    deepseek.chat = _scripted([bad] * 5)
    result = worker.run(root, "salir", CONFIG, "fake-key", limits={"max_tool_errors": 2})
    assert result["status"] == "aborted" and "errores de herramienta" in result["summary"]


def test_validacion_del_proyecto():
    root = _repo()
    ok = {"project": {"name": "demo"}, "commands": {"test": "python -c \"pass\""}}
    ko = {"project": {"name": "demo"}, "commands": {"test": "python -c \"raise SystemExit(1)\""}}
    fin = [{"role": "assistant", "content": "hecho"}]

    deepseek.chat = _scripted(fin)
    r = worker.run(root, "t", ok, "fake-key")
    assert r["status"] == "completed" and r["tests"]["test"]["ok"] and r["issues"] == []
    assert r["cost"] > 0

    deepseek.chat = _scripted(fin)
    r = worker.run(root, "t", ko, "fake-key")
    assert r["status"] == "validation_failed" and r["issues"] and not r["tests"]["test"]["ok"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
