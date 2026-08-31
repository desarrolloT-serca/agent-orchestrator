"""Aisla los monkeypatches entre ficheros de test cuando se ejecutan juntos con pytest.

Varios tests sustituyen deepseek.chat y worker.run por dobles falsos sin restaurarlos:
en pytest todos los ficheros comparten el mismo proceso, asi que un patch de
test_scheduler.py sobrevivia a la siguiente suite y hacia fallar test_worker.py.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orchestrator import deepseek, worker


@pytest.fixture(autouse=True)
def _restaura_monkeypatches():
    chat_original, run_original = deepseek.chat, worker.run
    yield
    deepseek.chat, worker.run = chat_original, run_original
