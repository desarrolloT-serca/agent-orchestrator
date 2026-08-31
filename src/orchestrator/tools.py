"""Herramientas del worker DeepSeek, con validacion de rutas y comandos."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

MAX_OUTPUT = 20_000  # caracteres devueltos al modelo
MAX_SHELL_TIMEOUT = 300  # el modelo elige el timeout del tool call; esto le pone techo

# ficheros que el worker nunca puede leer ni escribir
SECRET_FILES = re.compile(r"(^|/)\.env|\.(pem|key|p12|pfx)$|(^|/)id_(rsa|ed25519)$", re.I)

# lo mismo pero para detectarlo dentro de un comando de shell (cat .env, type id_rsa...):
# read_file/edit_file pasan por safe_path(), pero shell() es texto libre y esto no lo cubre
SECRET_IN_SHELL = re.compile(
    r"(?:^|[\s/\\'\"])\.env(?:[\s/\\'\";]|$)|\.(pem|key|p12|pfx)(?:[\s'\";]|$)|"
    r"id_(rsa|ed25519)(?:[\s'\";]|$)", re.I)

# variables de entorno que el subprocess de shell() no debe heredar del propio orquestador
ENV_SECRETO = re.compile(r"(API_KEY|SECRET|TOKEN|PASSWORD)$", re.I)

# comandos prohibidos (roadmap Fase 2: seguridad inicial)
BLOCKED_COMMANDS = (
    r"rm\s+(-\w+\s+)*-\w*[rf]",
    r"\bdel\s+/[sqf]",
    r"Remove-Item\b.*-Recurse",
    r"git\s+push",
    r"git\s+reset\s+--hard",
    r"git\s+clean",
    r"git\s+checkout\s+--\s",
    r"\bsudo\b",
    r"\b(shutdown|mkfs|diskpart)\b",
    r"\bformat\s+[a-z]:",
    r"(curl|wget)[^|]*\|\s*(ba|z)?sh",
)


class ToolError(Exception):
    """Error controlado: se devuelve al modelo como resultado de la herramienta."""


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n... [truncado, {len(text) - MAX_OUTPUT} caracteres mas]"


def safe_path(root: Path, path: str) -> Path:
    """Resuelve path dentro de root; bloquea escapes y ficheros con secretos."""
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ToolError(f"ruta fuera del worktree: {path}")
    rel = target.relative_to(root.resolve()).as_posix()
    if SECRET_FILES.search("/" + rel):
        raise ToolError(f"acceso denegado a fichero sensible: {rel}")
    return target


def read_file(root: Path, path: str, start_line: int | None = None, end_line: int | None = None,
             start: int | None = None, end: int | None = None) -> str:
    # el modelo confunde start/end con start_line/end_line con cierta frecuencia
    # (~5 de cada 20 llamadas reales medidas); aceptar el alias evita gastar una
    # iteracion entera en el error y el reintento
    start_line = start_line if start_line is not None else start
    end_line = end_line if end_line is not None else end
    target = safe_path(root, path)
    if not target.is_file():
        raise ToolError(f"no existe el fichero: {path}")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    desde = max(1, start_line or 1)
    hasta = min(len(lines), end_line or len(lines))
    numbered = [f"{i}\t{lines[i - 1]}" for i in range(desde, hasta + 1)]
    return _truncate("\n".join(numbered)) or "(fichero vacio)"


def list_directory(root: Path, path: str = ".", depth: int = 1) -> str:
    target = safe_path(root, path)
    if not target.is_dir():
        raise ToolError(f"no es un directorio: {path}")
    base = len(target.resolve().parts)
    out = []
    for item in sorted(target.rglob("*")):
        if any(p in {".git", "node_modules", ".venv", "__pycache__", "dist", "build", "target"} for p in item.parts):
            continue
        if len(item.resolve().parts) - base > depth:
            continue
        out.append(item.relative_to(root).as_posix() + ("/" if item.is_dir() else ""))
    return _truncate("\n".join(out)) or "(vacio)"


def search_code(root: Path, query: str, path: str = ".") -> str:
    target = safe_path(root, path)
    if shutil.which("rg"):
        r = subprocess.run(
            ["rg", "--line-number", "--no-heading", "--max-count", "50", query, str(target)],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace",
        )
        return _truncate(r.stdout) or "(sin resultados)"
    pattern = re.compile(query)
    out = []
    for f in target.rglob("*"):
        if not f.is_file() or any(p in {".git", "node_modules", ".venv", "__pycache__"} for p in f.parts):
            continue
        try:
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pattern.search(line):
                    out.append(f"{f.relative_to(root).as_posix()}:{n}:{line.strip()}")
        except OSError:
            continue
    return _truncate("\n".join(out)) or "(sin resultados)"


def edit_file(root: Path, path: str, new_string: str, old_string: str = "") -> str:
    """Crea el fichero (sin old_string) o sustituye una unica ocurrencia exacta."""
    target = safe_path(root, path)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_string, encoding="utf-8")
        return f"creado {path} ({len(new_string)} caracteres)"
    if not old_string:
        raise ToolError(f"{path} ya existe: usa old_string para sustituir un fragmento exacto")
    content = target.read_text(encoding="utf-8")
    hits = content.count(old_string)
    if hits == 0:
        raise ToolError(f"old_string no encontrado en {path}")
    if hits > 1:
        raise ToolError(f"old_string aparece {hits} veces en {path}: usa un fragmento unico")
    target.write_text(content.replace(old_string, new_string, 1), encoding="utf-8")
    return f"editado {path}"


def shell(root: Path, command: str, timeout: int = 180) -> str:
    for blocked in BLOCKED_COMMANDS:
        if re.search(blocked, command, re.I):
            raise ToolError(f"comando bloqueado por politica de seguridad: {command}")
    if SECRET_IN_SHELL.search(command):
        raise ToolError("comando bloqueado: hace referencia a un fichero protegido (.env, claves...)")
    timeout = min(timeout, MAX_SHELL_TIMEOUT)
    env = {k: v for k, v in os.environ.items() if not ENV_SECRETO.search(k)}
    try:
        r = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True,
                           timeout=timeout, env=env, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise ToolError(f"timeout de {timeout}s: {command}")
    return _truncate(f"exit={r.returncode}\n{r.stdout}\n{r.stderr}".strip())


def git_diff(root: Path, path: str = "") -> str:
    args = ["git", "diff"] + ([path] if path else [])
    r = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace")
    return _truncate(r.stdout) or "(sin cambios)"


DISPATCH = {
    "read_file": read_file,
    "list_directory": list_directory,
    "search_code": search_code,
    "edit_file": edit_file,
    "shell": shell,
    "git_diff": git_diff,
}


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


STR = {"type": "string"}
INT = {"type": "integer"}

SCHEMAS = [
    _schema("read_file", "Lee un fichero del repositorio (numerado por lineas).",
            {"path": STR, "start_line": INT, "end_line": INT}, ["path"]),
    _schema("list_directory", "Lista el contenido de un directorio.",
            {"path": STR, "depth": INT}, []),
    _schema("search_code", "Busca una expresion regular en el codigo.",
            {"query": STR, "path": STR}, ["query"]),
    _schema("edit_file", "Crea un fichero (sin old_string) o sustituye un fragmento exacto y unico.",
            {"path": STR, "new_string": STR, "old_string": STR}, ["path", "new_string"]),
    _schema("shell", "Ejecuta un comando en la raiz del repositorio.",
            {"command": STR, "timeout": INT}, ["command"]),
    _schema("git_diff", "Muestra los cambios sin commitear.", {"path": STR}, []),
]


def execute(root: Path, name: str, arguments: dict) -> str:
    fn = DISPATCH.get(name)
    if fn is None:
        return f"ERROR: herramienta desconocida '{name}'"
    try:
        return fn(root, **arguments)
    except ToolError as exc:
        return f"ERROR: {exc}"
    except TypeError as exc:
        return f"ERROR: argumentos invalidos para {name}: {exc}"
    except Exception as exc:  # noqa: BLE001 - el modelo debe poder reaccionar al fallo
        return f"ERROR: {exc.__class__.__name__}: {exc}"
