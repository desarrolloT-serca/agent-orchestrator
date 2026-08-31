"""Check del estado persistente y del runner (sin red)."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orchestrator import deepseek, project, runner, storage

TMP = Path(tempfile.mkdtemp())
storage.DB_PATH = TMP / "orchestrator.db"
storage.LOGS = TMP / "logs"


def _repo() -> Path:
    root = Path(tempfile.mkdtemp()).resolve()
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / ".env").write_text("DEEPSEEK_API_KEY=fake\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
                   cwd=root, check=True)
    project.save(root, project.detect(root))
    return root


def _fake_chat(*responses):
    it = iter(responses)

    def chat(messages, tools, api_key, model=None, reasoning="high", timeout=600):
        return {"message": next(it), "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    deepseek.chat = chat


def test_run_persistido_y_recuperable():
    root = _repo()
    run_id = storage.create_run(root, "Pon VALUE a 2", task_file="task.md", model="deepseek-v4-flash",
                                limits=json.dumps({"max_iterations": 5}))
    assert storage.get_run(run_id)["status"] == "queued"

    _fake_chat(
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "1", "type": "function",
            "function": {"name": "edit_file",
                         "arguments": json.dumps({"path": "app.py", "old_string": "VALUE = 1",
                                                  "new_string": "VALUE = 2"})}}]},
        {"role": "assistant", "content": "hecho"},
    )
    result = runner.execute(run_id)
    assert result["status"] == "completed"

    # el resultado sobrevive al proceso: se lee de SQLite
    row = storage.get_run(run_id)
    assert row["status"] == "completed" and row["summary"] == "hecho"
    assert json.loads(row["files_changed"]) and row["cost"] > 0
    assert row["finished_at"] and row["pid"]
    assert [r["id"] for r in storage.list_runs(root)] == [run_id]
    assert storage.list_runs(root, active_only=True) == []

    # la task suelta corrio en su propio worktree: el checkout principal no se toco
    assert (root / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert row["branch"] == f"agents/run-{run_id}"
    assert (Path(row["worktree"]) / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_fallo_persistido():
    root = _repo()
    (root / ".env").unlink()  # sin API key
    run_id = storage.create_run(root, "algo")
    assert runner.execute(run_id)["status"] == "failed"
    assert "DEEPSEEK_API_KEY" in storage.get_run(run_id)["summary"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
