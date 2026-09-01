"""Smoke test del dashboard (tui.py): 'textual' es dependencia opcional (extra 'tui'), asi
que este fichero se salta entero si no esta instalada -- ver README, seccion Dashboard.

No usa pytest-asyncio/anyio: envuelve cada test en asyncio.run() para no anadir esa
dependencia solo por esto (Textual.App.run_test() es async por su cuenta)."""

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

textual = pytest.importorskip("textual")

from orchestrator import storage, tui  # noqa: E402

TMP = Path(tempfile.mkdtemp())
storage.DB_PATH = TMP / "orchestrator.db"
storage.LOGS = TMP / "logs"


def _repo() -> Path:
    root = Path(tempfile.mkdtemp()).resolve()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def test_arranca_pinta_la_tabla_y_selecciona_el_primero():
    root = _repo()
    run_id = storage.create_run(root, "una tarea", status="completed", model="deepseek-v4-flash",
                                cost=0.01, duration_seconds=12)

    async def cuerpo():
        app = tui.Dashboard(root)
        async with app.run_test() as pilot:
            await pilot.pause()
            tabla = app.query_one("#runs")
            assert tabla.row_count == 1
            assert app.selected_id == run_id

    asyncio.run(cuerpo())


def test_binding_a_alterna_solo_activos():
    root = _repo()
    storage.create_run(root, "terminada", status="completed")

    async def cuerpo():
        app = tui.Dashboard(root)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#runs").row_count == 1
            await pilot.press("a")
            await pilot.pause()
            assert app.active_only is True
            assert app.query_one("#runs").row_count == 0  # 'completed' no es activo
            await pilot.press("a")
            await pilot.pause()
            assert app.query_one("#runs").row_count == 1

    asyncio.run(cuerpo())


def test_stop_selected_no_revienta_sin_seleccion():
    root = _repo()

    async def cuerpo():
        app = tui.Dashboard(root)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.selected_id is None
            app.action_stop_selected()  # no debe lanzar con la tabla vacia

    asyncio.run(cuerpo())


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
