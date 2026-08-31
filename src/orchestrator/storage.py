"""Estado persistente en SQLite (~/.agent-orchestrator/orchestrator.db)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

HOME = Path.home() / ".agent-orchestrator"
DB_PATH = HOME / "orchestrator.db"
LOGS = HOME / "logs"

# ponytail: una sola tabla; features/tasks llegan en Fase 5, cuando haya planes con dependencias
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    project_name TEXT,
    task_file TEXT,
    task TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'task',
    feature TEXT,
    task_id TEXT,
    branch TEXT,
    worktree TEXT,
    model TEXT,
    reasoning TEXT,
    limits TEXT,
    status TEXT NOT NULL,
    pid INTEGER,
    parent_id INTEGER,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    summary TEXT,
    files_changed TEXT,
    tests TEXT,
    issues TEXT,
    iterations INTEGER,
    tool_calls INTEGER,
    tool_errors INTEGER,
    prompt_tokens INTEGER,
    cached_tokens INTEGER,
    completion_tokens INTEGER,
    cost REAL,
    duration_seconds REAL
);
CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project);
"""

ACTIVE = ("queued", "running")

# columnas anadidas despues de la primera version de la base de datos
MIGRATIONS = {"kind": "TEXT", "feature": "TEXT", "task_id": "TEXT", "branch": "TEXT",
              "worktree": "TEXT", "limits": "TEXT"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect():
    """Conexion con esquema garantizado; hace commit y cierra al salir."""
    LOGS.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        columnas = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        for columna, tipo in MIGRATIONS.items():
            if columna not in columnas:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {columna} {tipo}")
        yield conn
        conn.commit()
    finally:
        conn.close()


def log_path(run_id: int) -> Path:
    return LOGS / f"run-{run_id}.log"


def create_run(project: Path, task: str, **fields) -> int:
    data = {"project": str(project), "task": task, "kind": "task", "status": "queued",
            "created_at": now(), **fields}
    columns = ", ".join(data)
    with connect() as conn:
        cur = conn.execute(
            f"INSERT INTO runs ({columns}) VALUES ({', '.join('?' * len(data))})", list(data.values())
        )
        return cur.lastrowid


def update_run(run_id: int, **fields) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE runs SET {assignments} WHERE id = ?", [*fields.values(), run_id])


def save_result(run_id: int, result: dict) -> None:
    usage = result.get("usage", {})
    update_run(
        run_id,
        status=result["status"],
        finished_at=now(),
        summary=result.get("summary"),
        files_changed=json.dumps(result.get("files_changed", []), ensure_ascii=False),
        tests=json.dumps(result.get("tests", {}), ensure_ascii=False),
        issues=json.dumps(result.get("issues", []), ensure_ascii=False),
        model=result.get("model"),
        reasoning=result.get("reasoning"),
        iterations=result.get("iterations"),
        tool_calls=result.get("tool_calls"),
        tool_errors=result.get("tool_errors"),
        prompt_tokens=usage.get("prompt_tokens"),
        cached_tokens=usage.get("prompt_cache_hit_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        cost=result.get("cost"),
        duration_seconds=result.get("duration_seconds"),
        **({"branch": result["integration_branch"]} if result.get("integration_branch") else {}),
    )


def get_run(run_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


def children(run_id: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM runs WHERE parent_id = ? ORDER BY id", (run_id,)).fetchall()


METRICS_SQL = """
SELECT model,
       COUNT(*) AS runs,
       SUM(COALESCE(kind, 'task') = 'task') AS primeros,
       SUM(COALESCE(kind, 'task') = 'task' AND status = 'completed') AS primeros_ok,
       SUM(kind = 'retry') AS reintentos,
       SUM(status = 'completed') AS completados,
       AVG(duration_seconds) AS media_seg,
       SUM(cost) AS coste
FROM runs
WHERE COALESCE(kind, 'task') IN ('task', 'retry') AND model IS NOT NULL {filtro}
GROUP BY model ORDER BY runs DESC
"""


def metrics(project: Path | None = None) -> list[sqlite3.Row]:
    """Comparativa por modelo: first-pass rate, reintentos, duracion y coste."""
    filtro, params = ("AND project = ?", [str(project)]) if project else ("", [])
    with connect() as conn:
        return conn.execute(METRICS_SQL.format(filtro=filtro), params).fetchall()


def list_runs(project: Path | None = None, limit: int = 20, active_only: bool = False) -> list[sqlite3.Row]:
    where, params = [], []
    if project is not None:
        where.append("project = ?")
        params.append(str(project))
    if active_only:
        where.append(f"status IN ({', '.join('?' * len(ACTIVE))})")
        params.extend(ACTIVE)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with connect() as conn:
        return conn.execute(
            f"SELECT * FROM runs {clause} ORDER BY id DESC LIMIT ?", [*params, limit]
        ).fetchall()
