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

from orchestrator import runner, storage, tui  # noqa: E402

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


def test_flechas_navegan_y_el_refresco_periodico_no_te_devuelve_arriba():
    """Bug real reportado: cada refresco (clear()+add_row) reseteaba el cursor a la fila 0,
    asi que bajar con las flechas 'no dejaba avanzar' -- el siguiente tick te devolvia arriba."""
    root = _repo()
    for i in range(3):
        storage.create_run(root, f"tarea {i}", status="completed", duration_seconds=i)

    async def cuerpo():
        app = tui.Dashboard(root)
        async with app.run_test() as pilot:
            await pilot.pause()
            primero = app.selected_id
            await pilot.press("down")
            await pilot.pause()
            assert app.selected_id != primero, "la flecha abajo deberia cambiar la seleccion"
            tras_bajar = app.selected_id

            app.refresh_runs()  # simula el tick del set_interval
            await pilot.pause()
            assert app.selected_id == tras_bajar, "el refresco periodico no deberia resetear el cursor"
            assert app.query_one("#runs").cursor_row != 0

    asyncio.run(cuerpo())


def test_barra_de_totales_suma_coste_y_tokens():
    root = _repo()
    storage.create_run(root, "a", status="completed", cost=0.01, prompt_tokens=100,
                       completion_tokens=20)
    storage.create_run(root, "b", status="completed", cost=0.02, prompt_tokens=200,
                       completion_tokens=50)

    async def cuerpo():
        app = tui.Dashboard(root)
        async with app.run_test() as pilot:
            await pilot.pause()
            texto = str(app.query_one("#totales").content)
            assert "2 runs" in texto and "0.0300" in texto and "370" in texto  # 100+20+200+50

    asyncio.run(cuerpo())


def test_detalle_muestra_tokens_cuando_hay():
    root = _repo()
    run_id = storage.create_run(root, "a", status="completed", prompt_tokens=100,
                                cached_tokens=40, completion_tokens=20)

    async def cuerpo():
        app = tui.Dashboard(root)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.selected_id == run_id
            texto = str(app.query_one("#detail").content)
            assert "prompt 100" in texto and "cache_hit 40" in texto and "completion 20" in texto

    asyncio.run(cuerpo())


def test_tecla_n_abre_el_input_y_al_enviar_crea_un_run_architect(monkeypatch):
    root = _repo()
    lanzados = []
    monkeypatch.setattr(tui.runner, "spawn", lambda run_id, root_: lanzados.append(run_id) or 999)

    async def cuerpo():
        app = tui.Dashboard(root)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            entrada = app.query_one("#nueva", tui.Input)
            assert entrada.display is True
            entrada.value = "añade login"
            await pilot.press("enter")
            await pilot.pause()
            assert entrada.display is False
            assert len(lanzados) == 1
            fila = storage.get_run(lanzados[0])
            assert fila["kind"] == "architect" and "login" in fila["task"]

    asyncio.run(cuerpo())


def test_escape_cancela_el_input_sin_crear_nada():
    root = _repo()

    async def cuerpo():
        app = tui.Dashboard(root)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            entrada = app.query_one("#nueva", tui.Input)
            entrada.value = "algo"
            await pilot.press("escape")
            await pilot.pause()
            assert entrada.display is False
            assert storage.list_runs(root) == []

    asyncio.run(cuerpo())


def test_launch_y_discard_sobre_un_plan_propuesto(monkeypatch):
    root = _repo()
    arquitecto_id = storage.create_run(root, "algo", kind="architect", status="completed")
    plan_id = storage.create_run(root, "feature: x\ntasks: []", kind="plan",
                                 status="queued", parent_id=arquitecto_id)
    lanzados = []
    monkeypatch.setattr(tui.runner, "spawn", lambda run_id, root_: lanzados.append(run_id) or 999)

    async def cuerpo():
        app = tui.Dashboard(root)
        async with app.run_test() as pilot:
            await pilot.pause()
            # orden por id DESC: el plan (creado despues del arquitecto) es la fila 0
            assert app.selected_id == plan_id
            # panel de pipeline visible para esta fila
            assert "Arquitecto" in str(app.query_one("#pipeline").content)

            await pilot.press("l")
            await pilot.pause()
            assert lanzados == [plan_id]

    asyncio.run(cuerpo())

    # discard sobre otro plan pendiente, sin pasar por la TUI (misma funcion que usa 'x')
    otro_id = storage.create_run(root, "feature: y\ntasks: []", kind="plan", status="queued")
    rechazado, _ = runner.discard(otro_id)
    assert not rechazado and storage.get_run(otro_id)["status"] == "stopped"


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
