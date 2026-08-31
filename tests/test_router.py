"""Checks del router Flash/Pro y del escalado automatico (sin red)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orchestrator import router, storage, worker

TMP = Path(tempfile.mkdtemp())
storage.DB_PATH = TMP / "orchestrator.db"
storage.LOGS = TMP / "logs"

FLASH, PRO = router.FLASH, router.PRO
CONFIG = {"workers": {"default_model": FLASH, "reasoning": "high"}}


def test_clasificacion():
    assert router.classify("Arregla el typo del boton") == "simple"
    assert router.classify("Refactoriza el modulo de pagos") == "hard"
    assert router.classify("Cambia el color del header. " + "detalle " * 80) == "normal"


def test_secuencia_de_intentos():
    assert router.attempts(CONFIG, None, "algo") == router.ESCALATION
    assert router.attempts(CONFIG, PRO, "algo") == router.PRO_ESCALATION
    # con default_model auto, una tarea dura arranca ya en pro
    auto = {"workers": {"default_model": "auto"}}
    assert router.attempts(auto, None, "migracion de la base de datos")[0][0] == PRO
    assert router.attempts(auto, None, "cambia el texto")[0][0] == FLASH


def _worker_que_falla(veces: int):
    usados = []

    def run(root, task, config, api_key, model=None, reasoning=None, limits=None,
            on_event=lambda k, t: None):
        usados.append((model, reasoning))
        fallo = len(usados) <= veces
        return {"status": "validation_failed" if fallo else "completed",
                "summary": "tests en rojo" if fallo else "hecho",
                "issues": ["test fallo: pytest"] if fallo else [],
                "usage": {}, "cost": 0.001, "model": model}

    worker.run = run
    return usados


def test_escala_a_pro_tras_dos_fallos():
    usados = _worker_que_falla(2)
    run_id = storage.create_run(Path.cwd(), "tarea", model=FLASH)
    result = router.run_escalated(Path.cwd(), "tarea", CONFIG, "fake", run_id)

    assert usados == [(FLASH, "high"), (FLASH, "high"), (PRO, "high")]
    assert result["status"] == "completed" and result["attempt"] == 3

    # cada intento es un run encadenado por parent_id
    final = storage.get_run(result["run_id"])
    assert final["model"] == PRO and final["status"] == "completed"
    assert final["parent_id"] is not None and final["id"] != run_id
    assert final["kind"] == "retry"  # los reintentos no cuentan como primera pasada
    assert storage.get_run(run_id)["status"] == "validation_failed"


def test_sin_escalado_un_solo_intento():
    usados = _worker_que_falla(5)
    run_id = storage.create_run(Path.cwd(), "tarea", model=FLASH)
    result = router.run_escalated(Path.cwd(), "tarea", CONFIG, "fake", run_id, escalate=False)
    assert usados == [(FLASH, "high")] and result["status"] == "validation_failed"


def test_no_reintenta_lo_que_no_toca():
    usados = []

    def run(root, task, config, api_key, model=None, reasoning=None, limits=None,
            on_event=lambda k, t: None):
        usados.append(model)
        return {"status": "completed", "summary": "ok", "usage": {}, "cost": 0.0}

    worker.run = run
    run_id = storage.create_run(Path.cwd(), "tarea", model=FLASH)
    router.run_escalated(Path.cwd(), "tarea", CONFIG, "fake", run_id)
    assert usados == [FLASH]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
