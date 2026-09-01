"""Checks de seguridad y edicion de las herramientas del worker."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orchestrator import tools


def _root() -> Path:
    root = Path(tempfile.mkdtemp()).resolve()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def hola():\n    return 1\n", encoding="utf-8")
    (root / ".env").write_text("DEEPSEEK_API_KEY=secreto\n", encoding="utf-8")
    return root


def test_no_escapa_del_worktree():
    assert "fuera del worktree" in tools.execute(_root(), "read_file", {"path": "../../etc/hosts"})


def test_no_lee_secretos():
    root = _root()
    assert "sensible" in tools.execute(root, "read_file", {"path": ".env"})
    assert "sensible" in tools.execute(root, "edit_file", {"path": ".env", "new_string": "x"})


def test_comandos_bloqueados():
    root = _root()
    for cmd in ("rm -rf /", "git push --force", "git reset --hard HEAD~1", "git clean -fd", "sudo ls"):
        assert "bloqueado" in tools.execute(root, "shell", {"command": cmd}), cmd
    assert "exit=0" in tools.execute(root, "shell", {"command": "git --version"})


def test_edit_file():
    root = _root()
    assert "creado" in tools.execute(root, "edit_file", {"path": "src/new.py", "new_string": "x = 1\n"})
    # existe y sin old_string -> no sobrescribe
    assert "ERROR" in tools.execute(root, "edit_file", {"path": "src/new.py", "new_string": "y = 2\n"})
    out = tools.execute(root, "edit_file", {"path": "src/app.py", "old_string": "return 1", "new_string": "return 2"})
    assert out.startswith("editado")
    assert "return 2" in (root / "src" / "app.py").read_text(encoding="utf-8")
    # fragmento no unico
    (root / "src" / "dup.py").write_text("a\na\n", encoding="utf-8")
    assert "unico" in tools.execute(root, "edit_file", {"path": "src/dup.py", "old_string": "a", "new_string": "b"})


def test_read_y_search():
    root = _root()
    assert "1\tdef hola():" in tools.execute(root, "read_file", {"path": "src/app.py"})
    assert "app.py" in tools.execute(root, "search_code", {"query": "hola"})
    assert "src/" in tools.execute(root, "list_directory", {"path": "."})


def test_read_file_acepta_start_end_como_alias():
    root = _root()
    con_alias = tools.execute(root, "read_file", {"path": "src/app.py", "start": 2, "end": 2})
    con_nombre_real = tools.execute(root, "read_file", {"path": "src/app.py", "start_line": 2, "end_line": 2})
    assert con_alias == con_nombre_real == "2\t    return 1"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")


def test_shell_no_hereda_secretos_ni_timeout_absurdo():
    root = _root()
    salida = tools.execute(root, "shell", {"command": "cat .env"})
    assert "protegido" in salida
    salida = tools.execute(root, "shell", {"command": "type .env"})
    assert "protegido" in salida

    import os
    os.environ["DEEPSEEK_API_KEY"] = "no-deberia-verse"
    try:
        salida = tools.execute(root, "shell", {"command": "python -c \"import os; "
                                                "print(os.environ.get('DEEPSEEK_API_KEY', 'ausente'))\""})
    finally:
        del os.environ["DEEPSEEK_API_KEY"]
    assert "ausente" in salida and "no-deberia-verse" not in salida

    assert tools.MAX_SHELL_TIMEOUT < 999999  # el modelo no puede pedir un timeout sin techo


def test_sandbox_docker_sigue_bloqueando_antes_de_intentarlo():
    """Los bloqueos (comando peligroso, fichero protegido) valen tambien con sandbox activo."""
    root = _root()
    config = {"workers": {"sandbox": "docker"}}
    assert "bloqueado" in tools.execute(root, "shell", {"command": "git push --force"}, config=config)
    assert "protegido" in tools.execute(root, "shell", {"command": "cat .env"}, config=config)


def test_sandbox_docker_sin_binario_da_error_claro():
    """Entorno sin Docker instalado (el de CI/este mismo): mensaje explicito, no un traceback."""
    root = _root()
    config = {"workers": {"sandbox": "docker"}}
    salida = tools.execute(root, "shell", {"command": "echo hola"}, config=config)
    assert "docker" in salida.lower()


def test_sandbox_docker_usa_la_imagen_configurada():
    """Sin poder correr Docker de verdad aqui: verifica el comando que se construiria."""
    root = _root()
    vistos = {}

    def _fake_run(args, **kwargs):
        vistos["args"] = args
        raise FileNotFoundError("docker")

    import subprocess as sp
    original = sp.run
    sp.run = _fake_run
    try:
        tools._shell_docker(root, "npm test", 60, {"workers": {"sandbox_image": "node:20-slim"}})
    except tools.ToolError:
        pass
    finally:
        sp.run = original

    assert vistos["args"][:3] == ["docker", "run", "--rm"]
    assert f"{root.resolve()}:/work" in vistos["args"]
    assert "node:20-slim" in vistos["args"]
    assert "npm test" in " ".join(vistos["args"])
