"""CLI del orquestador: init | doctor | config | run | status | logs | stop | retry |
integrate | history."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from orchestrator import __version__, plan as plan_module
from orchestrator import project, runner, storage, worker, worktree

# la consola de Windows suele ser cp1252: el texto del modelo trae flechas y acentos
for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(help="Orquestador hibrido Claude Code + DeepSeek", no_args_is_help=True)
console = Console()

DEEPSEEK_URL = "https://api.deepseek.com/models"
JSON_COLUMNS = ("files_changed", "tests", "issues", "limits")
COLORS = {"completed": "green", "running": "cyan", "queued": "yellow"}


def _root() -> Path:
    root = project.git_root(Path.cwd())
    if root is None:
        console.print("[red]No estamos dentro de un repositorio Git.[/]")
        raise typer.Exit(1)
    return root


def _row(run_id: int):
    row = storage.get_run(run_id)
    if row is None:
        console.print(f"[red]No existe el run {run_id}.[/]")
        raise typer.Exit(1)
    return row


def _detail(row) -> dict:
    data = dict(row)
    for column in JSON_COLUMNS:
        if data.get(column):
            data[column] = json.loads(data[column])
    return data


def _table(rows) -> Table:
    table = Table(box=None, pad_edge=False)
    for column in ("ID", "ESTADO", "MODELO", "INICIO", "SEG", "USD", "TAREA"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            str(row["id"]),
            f"[{COLORS.get(row['status'], 'red')}]{row['status']}[/]",
            (row["model"] or "-").replace("deepseek-v4-", ""),
            (row["started_at"] or row["created_at"] or "")[5:16].replace("T", " "),
            f"{row['duration_seconds'] or 0:.0f}",
            f"{row['cost'] or 0:.4f}",
            f"{row['feature']}/{row['task_id']}" if row["task_id"]
            else (row["task_file"] or (row["task"] or "").splitlines()[0][:40]),
        )
    return table


@app.command()
def version() -> None:
    """Muestra la version del orquestador."""
    console.print(__version__)


@app.command()
def init(force: bool = typer.Option(False, "--force", help="Sobrescribe .agent/project.yaml")) -> None:
    """Analiza el repositorio actual y crea .agent/project.yaml."""
    root = _root()
    if project.load(root) and not force:
        console.print(f"[yellow]Ya existe {project.CONFIG_PATH}. Usa --force para sobrescribir.[/]")
        raise typer.Exit(1)
    config = project.detect(root)
    path = project.save(root, config)
    console.print(f"[green]Creado[/] {path}")
    console.print(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))


@app.command()
def config(
    raw: bool = typer.Option(False, "--raw", help="Solo .agent/project.yaml, sin defaults ni config global"),
    global_: bool = typer.Option(False, "--global", help="Crea si falta y muestra el config global"),
) -> None:
    """Muestra la configuracion efectiva (defaults + config global + proyecto)."""
    if global_:
        if not project.GLOBAL_CONFIG.exists():
            project.GLOBAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            project.GLOBAL_CONFIG.write_text(
                yaml.safe_dump(project.DEFAULTS, sort_keys=False, allow_unicode=True), encoding="utf-8")
            console.print(f"[green]Creado[/] {project.GLOBAL_CONFIG}")
        console.print(project.GLOBAL_CONFIG.read_text(encoding="utf-8"))
        return
    cfg = (project.load if raw else project.resolved)(_root())
    if cfg is None:
        console.print("[red]Falta .agent/project.yaml. Ejecuta 'agents init'.[/]")
        raise typer.Exit(1)
    console.print(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))


def _check_api(key: str | None) -> tuple[bool, str]:
    if not key:
        return False, "sin DEEPSEEK_API_KEY"
    import httpx

    try:
        r = httpx.get(DEEPSEEK_URL, headers={"Authorization": f"Bearer {key}"}, timeout=15)
    except httpx.HTTPError as exc:
        return False, f"error de red: {exc.__class__.__name__}"
    return r.status_code == 200, f"HTTP {r.status_code}"


@app.command()
def doctor() -> None:
    """Comprueba que el entorno y el proyecto estan listos."""
    cwd = Path.cwd()
    root = project.git_root(cwd)
    cfg = project.resolved(root) if root else None
    key = project.api_key(root or cwd)

    py = sys.version_info
    checks: list[tuple[str, bool, str]] = [
        ("Python >= 3.11", py >= (3, 11), f"{py.major}.{py.minor}.{py.micro}"),
        ("Git", bool(shutil.which("git")), shutil.which("git") or "no encontrado"),
        ("Repositorio Git", root is not None, str(root) if root else "no encontrado"),
        (".agent/project.yaml", cfg is not None, "ok" if cfg else "ejecuta 'agents init'"),
        ("DEEPSEEK_API_KEY", bool(key), "presente" if key else "no definida (.env o entorno)"),
        ("Acceso DeepSeek", *_check_api(key)),
        ("ripgrep (opcional)", bool(shutil.which("rg")), shutil.which("rg") or "no instalado"),
    ]
    sandbox_docker = (cfg or {}).get("workers", {}).get("sandbox") == "docker"
    docker_bin = shutil.which("docker")
    if sandbox_docker:
        checks.append(("Docker (workers.sandbox=docker)", bool(docker_bin),
                       docker_bin or "falta: 'shell' del worker lo necesita para aislar"))
    else:
        checks.append(("Docker (opcional)", bool(docker_bin),
                       docker_bin or "no instalado; actívalo con workers.sandbox: docker"))
    try:
        with storage.connect():
            pass
        checks.append(("Base de datos", True, str(storage.DB_PATH)))
    except Exception as exc:  # noqa: BLE001 - queremos el motivo exacto en el reporte
        checks.append(("Base de datos", False, f"{exc.__class__.__name__}: {exc}"))

    optional = {"ripgrep (opcional)"} | (set() if sandbox_docker else {"Docker (opcional)"})
    for name, cmd in (cfg or {}).get("commands", {}).items():
        binary = cmd.split()[0]
        found = shutil.which(binary)
        checks.append((f"commands.{name}", bool(found), cmd if found else f"falta '{binary}'"))

    for name, ok, detail in checks:
        mark = "[green]OK  [/]" if ok else "[red]FAIL[/]"
        console.print(f"{mark} {name:<24} {detail}")

    if not all(ok for name, ok, _ in checks if name not in optional):
        raise typer.Exit(1)


@app.command()
def run(
    task: Path = typer.Argument(..., help="Tarea (.md) o plan de feature (.yaml)"),
    model: str = typer.Option(None, "--model", help="Sobrescribe workers.default_model"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Ejecuta en segundo plano y devuelve el ID"),
    max_iterations: int = typer.Option(None, "--max-iterations", help="Sobrescribe limits.max_iterations"),
    max_cost: float = typer.Option(None, "--max-cost", help="Sobrescribe limits.max_cost_usd"),
    max_minutes: int = typer.Option(None, "--max-minutes", help="Sobrescribe limits.max_runtime_minutes"),
    no_escalate: bool = typer.Option(False, "--no-escalate", help="Un solo intento, sin escalar a pro"),
) -> None:
    """Lanza un worker sobre una tarea (.md) o un plan multiworker (.yaml)."""
    root = _root()
    cfg = project.resolved(root)
    if cfg is None:
        console.print("[red]Falta .agent/project.yaml. Ejecuta 'agents init'.[/]")
        raise typer.Exit(1)
    if not project.api_key(root):
        console.print("[red]Falta DEEPSEEK_API_KEY (.env o entorno).[/]")
        raise typer.Exit(1)
    if not task.is_file():
        console.print(f"[red]No existe la tarea: {task}[/]")
        raise typer.Exit(1)

    overrides = {
        "max_iterations": max_iterations,
        "max_cost_usd": max_cost,
        "max_runtime_minutes": max_minutes,
        "escalate": False if no_escalate else None,
    }
    kind = "plan" if task.suffix in (".yaml", ".yml") else "task"
    feature = None
    if kind == "plan":
        try:
            feature = plan_module.load(task).feature
        except Exception as exc:  # noqa: BLE001 - plan invalido: mejor fallar antes de gastar tokens
            console.print(f"[red]Plan invalido: {exc}[/]")
            raise typer.Exit(1)

    run_id = storage.create_run(
        root,
        task.read_text(encoding="utf-8"),
        kind=kind,
        feature=feature,
        project_name=cfg.get("project", {}).get("name"),
        task_file=str(task),
        model=model or cfg.get("workers", {}).get("default_model"),
        limits=json.dumps({k: v for k, v in overrides.items() if v is not None}),
    )
    _launch(run_id, root, detach)


def _launch(run_id: int, root: Path, detach: bool) -> None:
    if detach:
        pid = runner.spawn(run_id, root)
        console.print(f"[green]Run {run_id} lanzado[/] (pid {pid}). Sigue con: agents status {run_id}")
        return

    def on_event(kind: str, text: str) -> None:
        # markup=False: el texto viene del modelo y puede contener corchetes
        console.print(text, style="cyan" if kind == "tool" else "green", markup=False, highlight=False)

    result = runner.execute(run_id, on_event=on_event)
    console.print_json(data=result)
    console.print(f"run {result.get('run_id', run_id)}")
    if result["status"] != "completed":
        raise typer.Exit(1)


@app.command()
def status(
    run_id: int = typer.Argument(None, help="ID del run; sin ID muestra los del proyecto actual"),
    limit: int = typer.Option(10, "--limit"),
) -> None:
    """Muestra el estado de los runs."""
    storage.reconcile(_root())  # detecta runs cuyo proceso desaparecio sin avisar
    if run_id is not None:
        row = _row(run_id)
        console.print_json(data=_detail(row))
        hijos = storage.children(run_id)
        if hijos:
            console.print(_table(hijos))
        return
    rows = storage.list_runs(_root(), limit=limit)
    if not rows:
        console.print("[yellow]Sin runs para este proyecto.[/]")
        return
    console.print(_table(rows))


@app.command()
def logs(run_id: int, tail: int = typer.Option(50, "--tail", help="Ultimas lineas; 0 para todo")) -> None:
    """Muestra el log de un run lanzado con --detach."""
    _row(run_id)
    path = storage.log_path(run_id)
    if not path.exists():
        console.print(f"[yellow]Sin log para el run {run_id} (se ejecuto en foreground).[/]")
        raise typer.Exit(1)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    console.print("\n".join(lines[-tail:] if tail else lines), markup=False, highlight=False)


@app.command()
def stop(run_id: int) -> None:
    """Detiene un run en ejecucion."""
    import psutil

    row = _row(run_id)
    if row["status"] not in storage.ACTIVE:
        console.print(f"[yellow]El run {run_id} ya no esta activo ({row['status']}).[/]")
        raise typer.Exit(1)
    if row["kind"] == "plan" or (row["status"] == "running" and not row["pid"]):
        # un plan, o una de sus tasks: corren como hilos dentro del proceso que lanzo el plan,
        # no como proceso propio. No hay nada que matar aqui, y marcarlo "stopped" mentiria:
        # el worker seguiria trabajando (y gastando DeepSeek) mientras el estado dice detenido.
        console.print("[yellow]Es un plan (o una de sus tasks): corren como hilos del proceso que lo "
                      "lanzo. Mata ese proceso (o su terminal) para detenerlas de verdad.[/]")
        raise typer.Exit(1)
    if not row["pid"]:
        console.print(f"[yellow]El run {run_id} no tiene un proceso propio todavia (sigue en cola).[/]")
    elif not storage.pid_es_worker(row["pid"]):
        console.print(f"[yellow]El pid {row['pid']} ya no es el worker (terminado, reutilizado por otro "
                      "proceso, o desaparecio); no se toca.[/]")
    else:
        try:
            proc = psutil.Process(row["pid"])
            for child in proc.children(recursive=True):
                child.terminate()
            proc.terminate()
        except psutil.Error as exc:
            console.print(f"[yellow]No se pudo matar el pid {row['pid']}: {exc}[/]")
    storage.update_run(run_id, status="stopped", finished_at=storage.now())
    console.print(f"[green]Run {run_id} detenido.[/]")


@app.command()
def retry(
    run_id: int,
    model: str = typer.Option(None, "--model", help="Modelo para el reintento, p.ej. deepseek-v4-pro"),
    detach: bool = typer.Option(False, "--detach", "-d"),
) -> None:
    """Relanza un run anterior, opcionalmente con otro modelo."""
    row = _row(run_id)
    root = Path(row["project"])
    new_id = storage.create_run(
        root,
        row["task"],
        kind=row["kind"] or "task",
        feature=row["feature"],
        task_id=row["task_id"],
        project_name=row["project_name"],
        task_file=row["task_file"],
        model=model or row["model"],
        limits=row["limits"],
        parent_id=run_id,
    )
    console.print(f"[green]Reintento del run {run_id} -> run {new_id}[/]")
    if row["task_id"] and row["kind"] != "plan":
        console.print("[yellow]Nota:[/] era una task de un plan; el reintento va suelto en su propio "
                      "worktree, sin los commits de sus dependencias ni reintegracion automatica.")
    _launch(new_id, root, detach)


@app.command()
def integrate(
    run_id: int,
    merge: bool = typer.Option(False, "--merge", help="git merge --no-ff a la rama actual del checkout principal"),
    pr: bool = typer.Option(False, "--pr", help="hace push de la rama y abre un PR con gh"),
    dry_run: bool = typer.Option(False, "--dry-run", help="solo valida y ensena que haria; no toca nada"),
) -> None:
    """Cierra un run ya revisado (PASS en hybrid-review): valida la rama y, si se pide,
    la mergea localmente y/o abre un PR. Nunca automatico: sin --merge ni --pr solo valida.
    """
    row = _row(run_id)
    root = Path(row["project"])
    branch = row["branch"]

    if row["status"] != "completed":
        console.print(f"[red]El run {run_id} esta en '{row['status']}', no 'completed'. No se integra.[/]")
        raise typer.Exit(1)
    if not branch:
        console.print(f"[red]El run {run_id} no dejo rama (no llego a producir commits).[/]")
        raise typer.Exit(1)
    if not worktree.git(root, "branch", "--list", branch):
        console.print(f"[red]La rama {branch} ya no existe (borrada, o worktree limpiado con "
                      "'agents clean --branches').[/]")
        raise typer.Exit(1)

    wt = Path(row["worktree"]) if row["worktree"] else None
    if wt and wt.is_dir():
        tests = worker.validate(wt, project.resolved(root) or {})
        for nombre, r in tests.items():
            mark = "[green]OK  [/]" if r["ok"] else "[red]FAIL[/]"
            console.print(f"{mark} {nombre:<12} {r['command']}")
        if any(not r["ok"] for r in tests.values()):
            console.print(f"[red]La rama {branch} ya no pasa la validacion del proyecto. No se "
                          "integra.[/]")
            raise typer.Exit(1)
    else:
        console.print(f"[yellow]El worktree del run {run_id} ya no existe; no se puede revalidar. "
                      "Se confia en el resultado guardado de cuando termino el worker.[/]")

    base = worktree.git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if dry_run or not (merge or pr):
        console.print(f"[green]Listo para integrar[/]: {branch} -> {base}")
        if not (merge or pr):
            console.print("Pasa --merge y/o --pr para hacerlo de verdad. No se ha tocado nada.")
        return

    # --untracked-files=no: un fichero sin trackear (p.ej. .agent/project.yaml sin commitear)
    # no interfiere con el merge; lo que de verdad lo complica son cambios sobre lo trackeado
    if worktree.git(root, "status", "--porcelain", "--untracked-files=no"):
        console.print("[red]El checkout principal tiene cambios sin commitear. Guardalos o hazles "
                      "stash antes de integrar.[/]")
        raise typer.Exit(1)

    if merge:
        try:
            worktree.git(root, "merge", "--no-ff", branch, "-m", f"Merge {branch}")
        except worktree.GitError as exc:
            console.print(f"[red]Merge fallido: {exc}[/]")
            raise typer.Exit(1)
        console.print(f"[green]Mergeado[/] {branch} -> {base}")

    if pr:
        if not shutil.which("gh"):
            console.print("[red]Falta 'gh' (GitHub CLI); instalalo o abre el PR a mano.[/]")
            raise typer.Exit(1)
        push = subprocess.run(["git", "push", "-u", "origin", branch], cwd=root,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if push.returncode != 0:
            console.print(f"[red]git push fallo: {(push.stderr or push.stdout).strip()}[/]")
            raise typer.Exit(1)
        r = subprocess.run(["gh", "pr", "create", "--base", base, "--head", branch, "--fill"],
                           cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            console.print(f"[red]gh pr create fallo: {(r.stderr or r.stdout).strip()}[/]")
            raise typer.Exit(1)
        console.print(r.stdout.strip())


@app.command()
def history(
    limit: int = typer.Option(20, "--limit"),
    all_projects: bool = typer.Option(False, "--all", help="Todos los proyectos, no solo el actual"),
) -> None:
    """Historico de runs con su coste."""
    rows = storage.list_runs(None if all_projects else _root(), limit=limit)
    if not rows:
        console.print("[yellow]Sin runs registrados.[/]")
        return
    console.print(_table(rows))
    console.print(f"total: {sum(r['cost'] or 0 for r in rows):.4f} USD en {len(rows)} runs")


@app.command()
def metrics(
    all_projects: bool = typer.Option(False, "--all", help="Todos los proyectos, no solo el actual"),
) -> None:
    """Comparativa Flash vs Pro: first-pass rate, reintentos, duracion y coste."""
    filas = storage.metrics(None if all_projects else _root())
    if not filas:
        console.print("[yellow]Sin datos todavia.[/]")
        return
    tabla = Table(box=None, pad_edge=False)
    for columna in ("MODELO", "RUNS", "1a PASADA", "COMPLETADOS", "REINTENTOS", "MEDIA SEG", "USD"):
        tabla.add_column(columna)
    for fila in filas:
        primeros = fila["primeros"] or 0
        tabla.add_row(
            (fila["model"] or "-").replace("deepseek-v4-", ""),
            str(fila["runs"]),
            f"{100 * (fila['primeros_ok'] or 0) / primeros:.0f}%" if primeros else "-",
            f"{fila['completados'] or 0}/{fila['runs']}",
            str(fila["reintentos"] or 0),
            f"{fila['media_seg'] or 0:.0f}",
            f"{fila['coste'] or 0:.4f}",
        )
    console.print(tabla)


@app.command()
def clean(
    days: int = typer.Option(30, "--days", help="Borra logs con mas de N dias"),
    branches: bool = typer.Option(False, "--branches", help="Borra tambien las ramas de integracion"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Solo enseña que haria"),
) -> None:
    """Limpia worktrees terminados, ramas temporales y logs antiguos del orquestador."""
    root = _root()
    activos = {r["worktree"] for r in storage.list_runs(root, limit=500, active_only=True) if r["worktree"]}
    base = root / worktree.WORKTREES
    for path in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
        if str(path) in activos:
            console.print(f"[yellow]en uso[/]   {path.name}")
            continue
        integracion = path.name.endswith("-integration")
        que = "worktree" if integracion and not branches else "worktree + rama"
        console.print(f"[green]{'borraria' if dry_run else 'borrado'}[/] {que:<16} {path.name}")
        if not dry_run:
            worktree.remove(root, path.name, drop_branch=branches or not integracion)

    limite = time.time() - days * 86400
    for log in sorted(storage.LOGS.glob("run-*.log")) if storage.LOGS.is_dir() else []:
        if log.stat().st_mtime < limite:
            console.print(f"[green]{'borraria' if dry_run else 'borrado'}[/] log              {log.name}")
            if not dry_run:
                log.unlink()

    if not dry_run:
        console.print("[green]Listo.[/] Las ramas de integracion se conservan salvo --branches.")


@app.command()
def skills(
    to: Path = typer.Option(Path.home() / ".claude" / "skills", "--to", help="Directorio de skills de Claude Code"),
) -> None:
    """Instala las skills hybrid-implement, hybrid-review y hybrid-status."""
    # instalacion editable: claude/skills en la raiz del repo; wheel: dentro del paquete
    origen = Path(__file__).resolve().parents[2] / "claude" / "skills"
    if not origen.is_dir():
        origen = Path(__file__).resolve().parent / "skills"
    if not origen.is_dir():
        console.print(f"[red]No encuentro las skills en {origen}[/]")
        raise typer.Exit(1)
    for skill in sorted(origen.iterdir()):
        if (skill / "SKILL.md").is_file():
            shutil.copytree(skill, to / skill.name, dirs_exist_ok=True)
            console.print(f"[green]instalada[/] {skill.name} -> {to / skill.name}")


# ponytail: sin daemon residente; cada run es un proceso desacoplado y SQLite es el estado
# compartido. Un daemon solo hace falta cuando haya cola y max_parallel (Fase 5).

if __name__ == "__main__":
    app()
