"""Deteccion de stack y persistencia de .agent/project.yaml."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

CONFIG_PATH = Path(".agent") / "project.yaml"
GLOBAL_CONFIG = Path.home() / ".agent-orchestrator" / "config.yaml"

# valores por defecto; el config global los sobrescribe y el del proyecto manda sobre todo
DEFAULTS = {
    "workers": {"max_parallel": 3, "default_model": "deepseek-v4-flash", "reasoning": "high"},
    "limits": {"max_iterations": 60, "max_runtime_minutes": 60, "max_cost_usd": 5, "max_tool_errors": 10},
    "git": {"provider": "github"},
}

LOCKFILES = {
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "bun.lockb": "bun",
    "package-lock.json": "npm",
}

# marcador -> (perfil, comandos)
PROFILES = {
    "pom.xml": ("java-maven", {"install": "mvn -B dependency:resolve", "test": "mvn -B test", "build": "mvn -B package"}),
    "build.gradle": ("java-gradle", {"test": "gradle test", "build": "gradle build"}),
    "build.gradle.kts": ("java-gradle", {"test": "gradle test", "build": "gradle build"}),
    "Cargo.toml": ("rust", {"test": "cargo test", "lint": "cargo clippy", "build": "cargo build"}),
    "composer.json": ("php", {"install": "composer install", "test": "vendor/bin/phpunit"}),
    "pyproject.toml": ("python", {"install": "pip install -e .", "test": "pytest"}),
    "requirements.txt": ("python", {"install": "pip install -r requirements.txt", "test": "pytest"}),
}

SOURCE_DIRS = ("src", "app", "lib", "packages")


def git(cwd: Path, *args: str) -> str:
    """git ... en cwd; cadena vacia si falla."""
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def git_root(start: Path) -> Path | None:
    out = git(start, "rev-parse", "--show-toplevel")
    return Path(out) if out else None


def _node(root: Path) -> tuple[str, dict, str | None]:
    pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
    pm = next((v for lock, v in LOCKFILES.items() if (root / lock).exists()), "npm")
    scripts = pkg.get("scripts", {})
    prefix = "npm run " if pm == "npm" else f"{pm} "
    commands = {"install": f"{pm} install"}
    for name in ("test", "lint", "build", "typecheck"):
        if name in scripts:
            commands[name] = prefix + name
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    return ("nextjs" if "next" in deps else "node"), commands, pkg.get("name")


def detect(root: Path) -> dict:
    """Construye el project.yaml a partir del repositorio."""
    name = None
    if (root / "package.json").exists():
        profile, commands, name = _node(root)
    else:
        profile, commands = "unknown", {}
        for marker, (p, c) in PROFILES.items():
            if (root / marker).exists():
                profile, commands = p, dict(c)
                break
        # el wrapper fija la version del build tool que espera el proyecto: preferirlo
        wrapper = "mvnw" if profile == "java-maven" else "gradlew" if profile == "java-gradle" else None
        if wrapper and (root / wrapper).exists():
            invocar = f"./{wrapper}" if os.name != "nt" else f"{wrapper}.cmd"
            commands = {k: v.replace("mvn", invocar).replace("gradle", invocar) for k, v in commands.items()}

    origin = git(root, "remote", "get-url", "origin")
    sources = [d for d in SOURCE_DIRS if (root / d).is_dir()] or ["."]

    return {
        "version": 1,
        "project": {"name": name or root.resolve().name, "profile": profile},
        "repository": {"provider": "github" if "github.com" in origin else "git"},
        "commands": commands,
        "paths": {"source": sources},
        "protected": [".env", ".git"],
    }


def merge(base: dict, encima: dict) -> dict:
    """Mezcla recursiva: lo de encima gana."""
    salida = dict(base)
    for clave, valor in encima.items():
        if isinstance(valor, dict) and isinstance(salida.get(clave), dict):
            salida[clave] = merge(salida[clave], valor)
        else:
            salida[clave] = valor
    return salida


def load_global() -> dict:
    if not GLOBAL_CONFIG.exists():
        return {}
    return yaml.safe_load(GLOBAL_CONFIG.read_text(encoding="utf-8")) or {}


def defaults() -> dict:
    """Defaults del orquestador con el config global aplicado encima."""
    return merge(DEFAULTS, load_global())


def resolved(root: Path) -> dict | None:
    """Configuracion efectiva: defaults + global + proyecto."""
    propia = load(root)
    return None if propia is None else merge(defaults(), propia)


def load(root: Path) -> dict | None:
    path = root / CONFIG_PATH
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def save(root: Path, config: dict) -> Path:
    path = root / CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def api_key(root: Path) -> str | None:
    """DEEPSEEK_API_KEY del entorno o del .env del repo."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    env = root / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "DEEPSEEK_API_KEY":
            return v.strip().strip("\"'") or None
    return None
