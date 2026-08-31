# agent-orchestrator

Orquestador local: **Claude Code** decide y revisa, **DeepSeek** implementa.

Plan completo por fases en [ROADMAP.md](ROADMAP.md).

## Estado

**V1 completa (fases 1-8).** Claude diseña y revisa, DeepSeek implementa, el orquestador coordina.

| Comando | Estado |
| --- | --- |
| `agents init` | detecta stack y crea `.agent/project.yaml` |
| `agents doctor` | comprueba entorno, config, API key y comandos |
| `agents config` | muestra la configuracion del proyecto |
| `agents run <task.md>` | worker DeepSeek: explora, edita, ejecuta tests, valida y reporta |
| `agents run <plan.yaml>` | varias tasks en paralelo, cada una en su worktree |
| `agents run -d` | lanza en segundo plano y devuelve el ID |
| `agents status [id]` | runs del proyecto, o el detalle de uno |
| `agents logs <id>` | log del worker desacoplado |
| `agents stop <id>` | mata el proceso del worker |
| `agents retry <id> [--model deepseek-v4-pro]` | relanza una tarea |
| `agents history [--all]` | historico con coste acumulado |
| `agents metrics [--all]` | Flash vs Pro: first-pass rate, reintentos, duracion, coste |
| `agents skills` | instala las skills en `~/.claude/skills` |
| `agents clean [--branches]` | borra worktrees terminados, ramas temporales y logs viejos |

Perfiles detectados: `nextjs`, `node`, `java-maven`, `java-gradle`, `python`, `php`, `rust`.

## Instalacion (desarrollo)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
agents --help
```

Copia `.env.example` a `.env` y rellena `DEEPSEEK_API_KEY` (tambien vale la variable de entorno).

## Uso

```bash
cd cualquier-repo-git
agents init
agents doctor
agents run task.md              # ver examples/task.md
agents run task.md --model deepseek-v4-pro
```

## Worker (Fase 2)

Loop nativo de tool calling contra `deepseek-v4-flash` (`thinking.reasoning_effort` segun
`workers.reasoning`). Herramientas: `read_file`, `list_directory`, `search_code`,
`edit_file`, `shell`, `git_diff`.

Seguridad aplicada antes de cada llamada:

- ninguna ruta fuera del repositorio;
- lectura y escritura denegadas en `.env`, `*.pem`, `*.key`, claves ssh;
- comandos bloqueados: `rm -rf`, `git push`, `git reset --hard`, `git clean`, `sudo`, `mkfs`, `curl | sh`;
- timeout por comando.

## Limites y validacion (Fase 3)

`.agent/project.yaml` define los limites por ejecucion; superarlos corta el loop con
`WORKER_ABORTED`:

```yaml
limits:
  max_iterations: 60
  max_runtime_minutes: 60
  max_cost_usd: 5
  max_tool_errors: 10
```

Sobrescribibles: `agents run task.md --max-iterations 20 --max-cost 0.5 --max-minutes 10`.

Al terminar el worker se ejecutan los comandos `test`, `lint`, `typecheck` y `build` del
proyecto; si alguno falla el estado pasa a `validation_failed` y los fallos van en `issues`.
El coste se calcula con la tarifa real de DeepSeek (cache hit/miss, horario peak/off-peak).

Resultado devuelto:

```json
{
  "status": "completed | validation_failed | aborted",
  "summary": "...", "files_changed": [], "tests": {}, "issues": [],
  "model": "deepseek-v4-flash", "reasoning": "high",
  "iterations": 7, "tool_calls": 10, "tool_errors": 0,
  "duration_seconds": 27.2, "usage": {}, "cost": 0.004934
}
```

## Configuracion y limpieza (Fase 8)

Tres capas, cada una sobrescribe a la anterior:

```text
defaults del orquestador
  → ~/.agent-orchestrator/config.yaml     (tus preferencias, todos los proyectos)
    → <proyecto>/.agent/project.yaml      (solo las diferencias)
```

```bash
agents config --global     # crea la plantilla global si falta y la muestra
agents config              # configuracion efectiva de este proyecto
agents config --raw        # solo el project.yaml
```

`agents init` no escribe en el proyecto nada que ya venga de arriba: el `project.yaml`
guarda el stack detectado (perfil, comandos, rutas) y poco mas. Perfiles reconocidos:
`nextjs`, `node`, `java-maven`, `java-gradle`, `python`, `php`, `rust`.

```bash
agents clean --dry-run     # que borraria
agents clean               # worktrees terminados + ramas de task + logs > 30 dias
agents clean --branches    # tambien las ramas de integracion
```

`clean` solo toca `.agent-worktrees/` y ramas `agents/*`, nunca nada mas, y **conserva las
ramas de integracion** por defecto: el worktree se puede recrear, los commits no.

## Claude Code (Fase 6)

`agents skills` copia tres skills a `~/.claude/skills`:

| Skill | Para que |
| --- | --- |
| `/hybrid-implement` | Claude estudia el repo, decide si delegar, escribe el `task.md` o el `plan.yaml`, lanza los workers y revisa el resultado |
| `/hybrid-review` | revision obligatoria antes de aceptar: criterios, correccion, arquitectura, duplicacion, seguridad, tests, regresiones |
| `/hybrid-status` | resume que estan haciendo los workers, que termino y cuanto se ha gastado |

El flujo diario pasa a ser:

```text
> /hybrid-implement
Implementa permisos granulares por modulo.
```

**Sin hooks.** El roadmap los contemplaba para guardar metricas y ejecutar tests, pero
el runner ya persiste las metricas en SQLite y el worker ya ejecuta la validacion del
proyecto: un hook solo duplicaria lo que el orquestador hace por dentro.

## Multiworker (Fase 5)

```yaml
feature: informes
tasks:
  - id: modelo
    description: Crea src/informe.py con informe_ventas(carritos)...
    files: [src/informe.py]
    depends_on: []
  - id: formato
    description: Crea src/formato.py con formatear_euros(valor)...
    files: [src/formato.py]
    depends_on: []
  - id: tests
    description: Anade tests/test_informe.py...
    files: [tests/**]
    depends_on: [modelo, formato]
```

```bash
agents run plan.yaml        # modelo y formato en paralelo; tests cuando ambos acaban
agents status <id>          # el plan y sus tasks hijas
```

Como funciona:

- cada task corre en su propio worktree `.agent-worktrees/<feature>-<task>` sobre la rama
  `agents/<feature>-<task>`, creada desde el HEAD actual; el directorio se auto-ignora, asi
  que el checkout principal del proyecto no se toca;
- una task dependiente arranca con los commits de sus dependencias ya aplicados (cherry-pick);
- el scheduler respeta `depends_on`, lanza hasta `workers.max_parallel` en paralelo y serializa
  las tasks cuyo `files` se solapa;
- si una task falla, sus dependientes quedan `skipped`;
- al final se construye `agents/<feature>-integration` aplicando los commits en orden
  topologico. Si un cherry-pick choca, el estado es `integration_conflict` y no se resuelve
  automaticamente.

## Router Flash/Pro (Fase 7)

```text
flash/high  →  fallo  →  retry flash/high  →  fallo  →  pro/high
```

Si la tarea arranca ya en `deepseek-v4-pro`, la ultima escalada es `pro/max`.

- Reintentan solo `validation_failed` y `aborted`; un plan invalido o una API caida no.
- Cada intento es un run propio (`kind: retry`, encadenado por `parent_id`), asi que el
  historico y las metricas distinguen primera pasada de reintento.
- El intento siguiente recibe el estado y los `issues` del anterior, y el codigo parcial
  sigue en el repositorio.
- `agents run task.md --no-escalate` para un unico intento.
- Con `workers.default_model: auto`, una clasificacion por reglas (`simple`/`normal`/`hard`)
  arranca en Pro las tareas duras: refactors, migraciones, arquitectura, concurrencia,
  rendimiento y seguridad.

```bash
agents metrics --all
```

```text
MODELO  RUNS  1a PASADA  COMPLETADOS  REINTENTOS  MEDIA SEG  USD
flash   24    79%        21/24        3           27         0.0912
pro     4     50%        3/4          2           58         0.0740
```

## Estado persistente (Fase 4)

```text
~/.agent-orchestrator/
├── orchestrator.db     tabla runs: estado, metricas, tokens y coste
└── logs/run-<id>.log   salida del worker desacoplado
```

Cada `agents run` queda registrado antes de arrancar, asi que el resultado sobrevive
al cierre de la sesion de Claude:

```bash
agents run task.md -d      # devuelve el ID y libera la terminal
agents status              # queued | running | completed | validation_failed | aborted | stopped | failed
agents logs 7 --tail 30
agents retry 7 --model deepseek-v4-pro
```

**Sin daemon residente.** El roadmap preveia `agents daemon start/stop`, pero cada run ya
es un proceso desacoplado y SQLite hace de estado compartido: un servidor con protocolo
propio no aporta nada hasta que exista cola y `max_parallel` (Fase 5).

## Limitaciones conocidas

- La integracion queda en una rama; no hay merge ni PR automatico (fuera de alcance en V1).
- Cada worktree es un checkout limpio: si el proyecto necesita `node_modules` o similar, hay
  que instalar dependencias en el (el worker puede hacerlo con `commands.install`).
- El worker puede dejar algun fichero temporal y `git add -A` lo commitea: revisa
  `files_changed` antes de integrar.
- La validacion usa los comandos del `project.yaml` tal cual: si `agents init` no detecto
  `lint`/`typecheck`, no se comprueban.

## Tests

```bash
pytest                          # o cada fichero por separado: python tests/test_worker.py
```
