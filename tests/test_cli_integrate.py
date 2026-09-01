"""Check de agents integrate: valida antes de mergear/abrir PR, nunca automatico."""

import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from typer.testing import CliRunner

from orchestrator import cli, project, storage, worktree

TMP = Path(tempfile.mkdtemp())
storage.DB_PATH = TMP / "orchestrator.db"
storage.LOGS = TMP / "logs"

runner = CliRunner()


def _repo() -> Path:
    root = Path(tempfile.mkdtemp()).resolve()
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
                   cwd=root, check=True)
    project.save(root, project.detect(root))
    return root


@contextmanager
def _en(root: Path):
    previo = Path.cwd()
    os.chdir(root)
    try:
        yield
    finally:
        os.chdir(previo)


def _run_completado(root: Path) -> int:
    """Simula un run terminado con commit propio, tal como lo dejaria un worker real."""
    base = worktree.head(root)
    path, branch = worktree.create(root, "feat-1", base)
    (path / "nuevo.txt").write_text("nuevo\n", encoding="utf-8")
    worktree.commit_all(path, "agents(feat): nuevo")
    return storage.create_run(root, "algo", status="completed", branch=branch, worktree=str(path))


def test_no_integra_si_no_esta_completed():
    root = _repo()
    run_id = storage.create_run(root, "algo", status="validation_failed", branch="agents/x")
    with _en(root):
        result = runner.invoke(cli.app, ["integrate", str(run_id)])
    assert result.exit_code != 0


def test_dry_run_no_toca_nada():
    root = _repo()
    run_id = _run_completado(root)
    with _en(root):
        result = runner.invoke(cli.app, ["integrate", str(run_id), "--dry-run"])
    assert result.exit_code == 0
    assert not (root / "nuevo.txt").exists()


def test_sin_flags_tampoco_toca_nada():
    root = _repo()
    run_id = _run_completado(root)
    with _en(root):
        result = runner.invoke(cli.app, ["integrate", str(run_id)])
    assert result.exit_code == 0
    assert not (root / "nuevo.txt").exists()


def test_merge_de_verdad():
    root = _repo()
    run_id = _run_completado(root)
    with _en(root):
        result = runner.invoke(cli.app, ["integrate", str(run_id), "--merge"])
    assert result.exit_code == 0
    assert (root / "nuevo.txt").read_text(encoding="utf-8") == "nuevo\n"


def test_no_mergea_con_working_tree_sucio():
    root = _repo()
    run_id = _run_completado(root)
    (root / "base.txt").write_text("cambio sin commitear\n", encoding="utf-8")  # trackeado y modificado
    with _en(root):
        result = runner.invoke(cli.app, ["integrate", str(run_id), "--merge"])
    assert result.exit_code != 0
    assert not (root / "nuevo.txt").exists()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
