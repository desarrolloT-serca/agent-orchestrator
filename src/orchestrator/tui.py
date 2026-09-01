"""Dashboard en terminal (V2, opt-in): monitoriza y controla runs sin repetir 'agents
status'/'logs' a mano. Requiere 'textual' (pip install 'agent-orchestrator[tui]').

Solo vista + control basico (stop/retry/validar/lanzar/descartar) sobre lo que ya existe
en storage.py, runner.py y architect.py -- cero logica de negocio nueva aqui. La tecla 'n'
pide una feature al arquitecto (CLI de Claude, ver architect.py) pero deliberadamente no
hay chat: una linea de texto, se lanza como cualquier otro run, se ve pasar en la tabla.
Tampoco hace --merge/--pr de 'agents integrate': eso sigue siendo CLI explicito.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, Log, Static

from orchestrator import project, runner, storage, worker

REFRESH_RUNS_SECONDS = 1.5
REFRESH_LOG_SECONDS = 1.0
COLUMNS = ("ID", "ESTADO", "MODELO", "INICIO", "SEG", "USD", "TAREA")
COLORS = {"completed": "green", "running": "cyan", "queued": "yellow",
         "failed": "red", "validation_failed": "red", "aborted": "red",
         "integration_conflict": "red", "stopped": "yellow"}


def _pipeline(row: sqlite3.Row) -> str | None:
    """Si la fila seleccionada es un run 'architect' o un plan propuesto por uno, resume
    las 4 etapas (Arquitecto -> Workers -> Tester -> Hecho). None si no aplica: se sigue
    mostrando el detalle normal para cualquier otra fila."""
    if row["kind"] == "architect":
        arquitecto = row
        hijos_arq = storage.children(row["id"])
        plan = hijos_arq[0] if hijos_arq else None
    elif row["kind"] == "plan" and row["parent_id"]:
        padre = storage.get_run(row["parent_id"])
        if padre is None or padre["kind"] != "architect":
            return None
        arquitecto, plan = padre, row
    else:
        return None

    def etapa(nombre: str, estado: str) -> str:
        return f"[{COLORS.get(estado, 'white')}]● {nombre}: {estado}[/]"

    partes = [etapa("Arquitecto", arquitecto["status"])]
    if plan is None:
        return " -> ".join([*partes, etapa("Workers", "-"), etapa("Tester", "-"), etapa("Hecho", "-")])

    tasks = storage.children(plan["id"])
    if not tasks:
        workers = plan["status"]
    elif any(t["status"] == "running" for t in tasks):
        workers = "running"
    elif all(t["status"] == "completed" for t in tasks):
        workers = "completed"
    else:
        workers = "failed"
    partes.append(etapa("Workers", workers))

    validaciones = [json.loads(t["tests"]) for t in tasks if t["tests"]]
    validaciones = [v for v in validaciones if v]
    if not validaciones:
        tester = "-"
    elif all(all(r["ok"] for r in v.values()) for v in validaciones):
        tester = "completed"
    else:
        tester = "failed"
    partes.append(etapa("Tester", tester))
    partes.append(etapa("Hecho", plan["status"]))
    return " -> ".join(partes)


def _fila(row: sqlite3.Row) -> tuple[str, ...]:
    return (
        str(row["id"]),
        f"[{COLORS.get(row['status'], 'white')}]{row['status']}[/]",
        (row["model"] or "-").replace("deepseek-v4-", ""),
        (row["started_at"] or row["created_at"] or "")[5:16].replace("T", " "),
        f"{row['duration_seconds'] or 0:.0f}",
        f"{row['cost'] or 0:.4f}",
        f"{row['feature']}/{row['task_id']}" if row["task_id"]
        else (row["task_file"] or (row["task"] or "").splitlines()[0][:40]),
    )


def _detalle(row: sqlite3.Row) -> str:
    campos = ("id", "status", "kind", "feature", "task_id", "model", "reasoning",
             "branch", "worktree", "cost", "duration_seconds", "summary")
    texto = "\n".join(f"[bold]{c}[/]: {row[c]}" for c in campos if row[c] not in (None, ""))
    tokens = [f"prompt {row['prompt_tokens']}", f"cache_hit {row['cached_tokens']}",
             f"completion {row['completion_tokens']}"] if row["prompt_tokens"] is not None else []
    if tokens:
        texto += f"\n[bold]tokens[/]: {', '.join(tokens)}"
    for campo in ("issues", "tests"):
        if row[campo]:
            valor = json.loads(row[campo])
            if valor:
                texto += f"\n[bold]{campo}[/]: {json.dumps(valor, ensure_ascii=False, indent=2)}"
    return texto or "(sin datos)"


def _totales(rows: list[sqlite3.Row]) -> str:
    coste = sum(r["cost"] or 0 for r in rows)
    tokens = sum((r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0) for r in rows)
    return f"{len(rows)} runs · ${coste:.4f} · {tokens:_} tokens".replace("_", ".")


class Dashboard(App):
    CSS = """
    #totales { height: 1; padding: 0 1; color: $text-muted; }
    #nueva { display: none; border: solid $accent; }
    #runs { width: 65%; }
    #panel { width: 35%; }
    #pipeline { height: 3; border: solid $accent; padding: 0 1; }
    #detail { height: 37%; border: solid $accent; padding: 0 1; }
    #logtail { height: 60%; border: solid $accent; }
    """
    BINDINGS = [
        ("q", "quit", "Salir"),
        ("s", "stop_selected", "Stop"),
        ("r", "retry_selected", "Retry"),
        ("i", "validate_selected", "Validar"),
        ("n", "new_feature", "Nueva (arquitecto)"),
        ("l", "launch_selected", "Launch"),
        ("x", "discard_selected", "Discard"),
        ("a", "toggle_active", "Solo activos"),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.active_only = False
        self.selected_id: int | None = None
        self._log_offset = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="totales")
        yield Input(id="nueva", placeholder="Describe la feature (Enter envia, Esc cancela)...")
        with Horizontal():
            yield DataTable(id="runs")
            with Vertical(id="panel"):
                yield Static(id="pipeline")
                yield Static(id="detail")
                yield Log(id="logtail")
        yield Footer()

    def on_mount(self) -> None:
        tabla = self.query_one("#runs", DataTable)
        tabla.add_columns(*COLUMNS)
        tabla.cursor_type = "row"
        self.refresh_runs()
        # el Input oculto (#nueva) es focusable aunque display=False y se queda con el
        # foco por defecto al montar; sin esto ninguna tecla (flechas, s/r/i/a...) llegaba
        # a la tabla
        tabla.focus()
        self.set_interval(REFRESH_RUNS_SECONDS, self.refresh_runs)
        self.set_interval(REFRESH_LOG_SECONDS, self.refresh_log)

    def refresh_runs(self) -> None:
        # clear() resetea el cursor a la fila 0: sin restaurarlo, cada refresco periodico
        # (cada REFRESH_RUNS_SECONDS) te devolvia arriba en mitad de navegar con las flechas
        tabla = self.query_one("#runs", DataTable)
        previo = self.selected_id
        rows = storage.list_runs(self.root, limit=200, active_only=self.active_only)
        tabla.clear()
        for row in rows:
            tabla.add_row(*_fila(row), key=str(row["id"]))
        if not rows:
            self.selected_id = None
        elif previo is not None and previo in {r["id"] for r in rows}:
            tabla.move_cursor(row=tabla.get_row_index(str(previo)), animate=False)
        else:
            self.selected_id = rows[0]["id"]
            tabla.move_cursor(row=0, animate=False)
        self.query_one("#totales", Static).update(_totales(rows))
        self.refresh_detail()

    def refresh_detail(self) -> None:
        if self.selected_id is None:
            return
        row = storage.get_run(self.selected_id)
        if row is None:
            return
        self.query_one("#detail", Static).update(_detalle(row))
        self.query_one("#pipeline", Static).update(_pipeline(row) or "")

    def refresh_log(self) -> None:
        if self.selected_id is None:
            return
        path = storage.log_path(self.selected_id)
        if not path.exists():
            return
        contenido = path.read_text(encoding="utf-8", errors="replace")
        nuevo = contenido[self._log_offset:]
        if nuevo:
            self.query_one("#logtail", Log).write(nuevo)
            self._log_offset = len(contenido)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # RowHighlighted: dispara con las flechas, no hace falta pulsar Enter para "elegir"
        if event.row_key.value is None:
            return
        nuevo_id = int(event.row_key.value)
        if nuevo_id == self.selected_id:
            return
        self.selected_id = nuevo_id
        self._log_offset = 0
        self.query_one("#logtail", Log).clear()
        self.refresh_detail()

    def action_stop_selected(self) -> None:
        if self.selected_id is None:
            return
        _, mensaje = runner.stop(self.selected_id)
        self.notify(mensaje)
        self.refresh_runs()

    def action_retry_selected(self) -> None:
        if self.selected_id is None:
            return
        row = storage.get_run(self.selected_id)
        if row is None:
            return
        new_id = runner.retry(self.selected_id)
        runner.spawn(new_id, Path(row["project"]))
        self.notify(f"reintento lanzado: run {new_id}")
        self.refresh_runs()

    def action_validate_selected(self) -> None:
        if self.selected_id is None:
            return
        row = storage.get_run(self.selected_id)
        if row is None:
            return
        if row["status"] != "completed" or not row["worktree"]:
            self.notify("solo se puede validar un run 'completed' con worktree", severity="warning")
            return
        wt = Path(row["worktree"])
        if not wt.is_dir():
            self.notify("el worktree ya no existe", severity="warning")
            return
        cfg = project.resolved(Path(row["project"])) or {}
        tests = worker.validate(wt, cfg)
        if all(r["ok"] for r in tests.values()):
            self.notify("validacion OK -- listo para 'agents integrate --merge'/--pr'")
        else:
            self.notify("validacion con fallos: revisa el detalle", severity="error")
        self.refresh_detail()

    def action_toggle_active(self) -> None:
        self.active_only = not self.active_only
        self.refresh_runs()

    def action_new_feature(self) -> None:
        entrada = self.query_one("#nueva", Input)
        entrada.display = True
        entrada.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "nueva":
            return
        descripcion = event.value.strip()
        event.input.value = ""
        event.input.display = False
        self.query_one("#runs", DataTable).focus()
        if not descripcion:
            return
        run_id = storage.create_run(self.root, descripcion, kind="architect")
        runner.spawn(run_id, self.root)
        self.selected_id = run_id
        self.notify(f"arquitecto trabajando: run {run_id}")
        self.refresh_runs()

    def on_key(self, event) -> None:
        entrada = self.query_one("#nueva", Input)
        if event.key == "escape" and entrada.display:
            entrada.value = ""
            entrada.display = False
            self.query_one("#runs", DataTable).focus()
            event.stop()

    def action_launch_selected(self) -> None:
        if self.selected_id is None:
            return
        row = storage.get_run(self.selected_id)
        if row is None or not runner.plan_pendiente(row):
            self.notify("no es un plan pendiente de lanzar", severity="warning")
            return
        runner.spawn(self.selected_id, Path(row["project"]))
        self.notify(f"lanzado: run {self.selected_id}")
        self.refresh_runs()

    def action_discard_selected(self) -> None:
        if self.selected_id is None:
            return
        _, mensaje = runner.discard(self.selected_id)
        self.notify(mensaje)
        self.refresh_runs()
