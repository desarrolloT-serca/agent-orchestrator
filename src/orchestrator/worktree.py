"""Git worktrees y ramas propiedad del orquestador (Fase 5)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

WORKTREES = ".agent-worktrees"
PREFIX = "agents/"  # todas las ramas del orquestador viven bajo este prefijo


class GitError(RuntimeError):
    pass


class IntegrationConflict(GitError):
    """Cherry-pick que no aplica limpio. Lleva bastante estructura para resolverlo asistido:
    que task/sha lo causo y que ficheros chocan, no solo un texto de error para el log."""

    def __init__(self, task_id: str, sha: str, files: list[str], git_output: str):
        self.task_id = task_id
        self.sha = sha
        self.files = files
        super().__init__(
            f"INTEGRATION_CONFLICT: la task '{task_id}' (sha {sha[:8]}) no aplica limpio -- "
            f"{len(files)} fichero(s) en conflicto: {', '.join(files) or '(sin detectar)'}. "
            f"{git_output[:300]}"
        )


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-") or "task"


def git(cwd: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD")


def create(root: Path, name: str, base: str) -> tuple[Path, str]:
    """Crea (o recrea) el worktree name a partir del commit base."""
    path = root / WORKTREES / name
    branch = PREFIX + name
    remove(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    # el directorio se ignora a si mismo: no ensuciamos el .gitignore del proyecto
    (path.parent / ".gitignore").write_text("*\n", encoding="utf-8")
    git(root, "worktree", "add", "--quiet", "-b", branch, str(path), base)
    return path, branch


def remove(root: Path, name: str, drop_branch: bool = True) -> None:
    """Elimina el worktree y (opcionalmente) su rama. Solo toca lo que es del orquestador."""
    path = root / WORKTREES / name
    git(root, "worktree", "remove", "--force", str(path), check=False)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    if drop_branch:
        git(root, "branch", "-D", PREFIX + name, check=False)
    git(root, "worktree", "prune", check=False)


def commit_all(path: Path, message: str) -> str | None:
    """Commitea todo lo que haya cambiado en el worktree. Devuelve el sha o None."""
    git(path, "add", "-A")
    if not git(path, "status", "--porcelain"):
        return None
    git(path, "-c", "user.name=agent-orchestrator", "-c", "user.email=agents@local",
        "commit", "--quiet", "-m", message)
    return git(path, "rev-parse", "HEAD")


def cherry_pick(path: Path, commits: list[tuple[str, str]]) -> None:
    """Aplica commits (task_id, sha) en orden.

    Si uno choca, NO aborta: deja el worktree a medio cherry-pick, con las marcas <<<<<<<
    en los ficheros que chocan, tal cual lo dejaria un `git cherry-pick` a mano. Resolucion
    asistida en vez de perder el contexto del conflicto: cd al worktree, edita, `git add`,
    `git cherry-pick --continue` (o `--abort` si se prefiere descartar).
    """
    for task_id, sha in commits:
        r = subprocess.run(["git", "cherry-pick", sha], cwd=path, capture_output=True, text=True,
                           timeout=120, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            ficheros = git(path, "diff", "--name-only", "--diff-filter=U", check=False).splitlines()
            raise IntegrationConflict(task_id, sha, ficheros, (r.stdout + r.stderr).strip())
