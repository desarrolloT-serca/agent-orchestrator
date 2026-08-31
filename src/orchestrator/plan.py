"""Plan de feature: varias tasks con dependencias (Fase 5)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class Task(BaseModel):
    id: str
    description: str
    model: str | None = None
    files: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    feature: str
    tasks: list[Task]

    @model_validator(mode="after")
    def _coherente(self) -> "Plan":
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("hay ids de task repetidos")
        for task in self.tasks:
            desconocidas = set(task.depends_on) - set(ids)
            if desconocidas:
                raise ValueError(f"{task.id} depende de tasks inexistentes: {sorted(desconocidas)}")
        self.order()  # detecta ciclos
        return self

    def order(self) -> list[Task]:
        """Tasks en orden topologico (para integrar en el orden correcto)."""
        pendientes = {t.id: set(t.depends_on) for t in self.tasks}
        por_id = {t.id: t for t in self.tasks}
        ordenadas: list[Task] = []
        while pendientes:
            listas = sorted(tid for tid, deps in pendientes.items() if not deps)
            if not listas:
                raise ValueError(f"dependencias circulares entre {sorted(pendientes)}")
            for tid in listas:
                ordenadas.append(por_id[tid])
                del pendientes[tid]
            for deps in pendientes.values():
                deps.difference_update(listas)
        return ordenadas


def load(path: Path) -> Plan:
    return Plan(**yaml.safe_load(path.read_text(encoding="utf-8")))


def overlap(a: list[str], b: list[str]) -> bool:
    """True si dos tasks declaran ficheros solapados (el scheduler las serializa)."""
    if not a or not b:
        return False  # sin ownership declarado no asumimos conflicto
    prefijos_a = [p.split("*")[0].rstrip("/") for p in a]
    prefijos_b = [p.split("*")[0].rstrip("/") for p in b]
    return any(x.startswith(y) or y.startswith(x) for x in prefijos_a for y in prefijos_b)
