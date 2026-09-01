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
| `agents architect "<descripcion>"` | el CLI de Claude propone un plan.yaml (no lo lanza, ver abajo) |
| `agents launch <id>` / `agents discard <id>` | confirma o descarta un plan propuesto por el arquitecto |
| `agents status [id]` | runs del proyecto, o el detalle de uno |
| `agents logs <id>` | log del worker desacoplado |
| `agents stop <id>` | mata el proceso del worker |
| `agents retry <id> [--model deepseek-v4-pro]` | relanza una tarea |
| `agents dashboard` | panel en vivo: runs, logs y stop/retry/validar sin salir de la terminal (opt-in, ver abajo) |
| `agents integrate <id> [--merge] [--pr] [--dry-run]` | revalida la rama y, si se pide, la mergea y/o abre PR |
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

Loop nativo de tool calling contra `deepseek-v4-flash` (`thinking: {type: enabled}` +
`reasoning_effort` segun `workers.reasoning`). Herramientas: `read_file`, `list_directory`,
`search_code`, `edit_file`, `shell`, `git_diff`.

Seguridad aplicada antes de cada llamada:

- ninguna ruta fuera del repositorio;
- lectura y escritura denegadas en `.env`, `*.pem`, `*.key`, claves ssh;
- comandos bloqueados: `rm -rf`, `git push`, `git reset --hard`, `git clean`, `sudo`, `mkfs`, `curl | sh`;
- timeout por comando;
- opcional (`workers.sandbox: docker`, V2): `shell` corre en un contenedor con el worktree
  como unico disco visible, no solo el bloqueo por patron -- ver "Limitaciones conocidas".

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
  topologico. Si un cherry-pick choca, el estado es `integration_conflict` y **no se resuelve
  automaticamente** (decision de diseño, no falta por hacer) -- pero es asistido (V2): el
  worktree se deja a medio cherry-pick, con las marcas `<<<<<<<` reales en el fichero que
  choca, y `issues` trae la ruta exacta y como continuar (`git add` + `git cherry-pick
  --continue`, o `--abort`). El mismo cherry-pick de las dependencias de una task tambien
  puede chocar; en ese caso la task queda en `integration_conflict` (no `failed`) con el
  mismo detalle.

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

## Dashboard (V2)

Con la cola global y `agents run -d` como flujo normal, seguir varios runs en paralelo a
base de repetir `agents status`/`logs` se queda corto. `agents dashboard` es una TUI (panel
en la propia terminal, sin servidor ni navegador -- el roadmap descarto un dashboard *web*,
no esto) con la tabla de runs en vivo, detalle + log en streaming del seleccionado, y control
basico por teclado:

```bash
pip install -e ".[tui]"    # opt-in: no se instala con el paquete base
agents dashboard
```

| Tecla | Que hace |
| --- | --- |
| `s` | `agents stop` sobre el run seleccionado |
| `r` | `agents retry` sobre el seleccionado, siempre en segundo plano |
| `i` | revalida la rama (igual que `agents integrate` sin flags): PASS o FAIL, nunca mergea |
| `n` | pide una feature al arquitecto (ver abajo) |
| `l` | lanza (`agents launch`) el plan seleccionado si el arquitecto lo propuso y sigue en cola |
| `x` | descarta (`agents discard`) un plan propuesto que no se quiere lanzar |
| `a` | alterna ver solo runs activos / todo el historico |
| `q` | salir |

Deliberadamente **no** hace `--merge`/`--pr` de `agents integrate` desde una tecla -- eso
sigue exigiendo el comando explicito. `runner.stop()`/`runner.retry()`/`runner.discard()` son
la misma implementacion que usan `agents stop`/`agents retry`/`agents discard`; el dashboard
no duplica logica, solo la muestra y la dispara.

### Arquitecto: `agents architect` (V2)

Automatiza el primer paso de `hybrid-implement` -- estudiar el repo y escribir el
`plan.yaml` -- usando el **CLI de Claude Code** (`claude -p`) como subproceso, no la API de
Anthropic: reutiliza tu sesion ya autenticada, sin `ANTHROPIC_API_KEY`.

```bash
agents architect "añade notificaciones por email al confirmar un pedido"
agents launch <id>      # una vez revisado el plan propuesto, lo lanza de verdad
agents discard <id>     # o lo descarta si no convence
```

- El arquitecto **solo propone**: crea una fila `plan` hija en cola (`queued`), nunca la
  lanza el solo. Hace falta `agents launch` (o la tecla `l` del dashboard) para que los
  workers empiecen a trabajar de verdad -- coherente con "nunca push/lanzamiento sin
  pedirlo" del resto del orquestador.
- Usa `Read`/`Glob`/`Grep` solamente (`--allowedTools`, `--permission-mode dontAsk`): el
  arquitecto no edita nada, solo lee y propone.
- El esquema de salida (`--json-schema`) es literalmente `Plan.model_json_schema()` --
  el mismo modelo Pydantic que ya valida un `plan.yaml` escrito a mano, no un esquema
  duplicado a mantener aparte.
- En el dashboard (`n`), el panel de detalle se sustituye por un resumen de 4 etapas --
  Arquitecto → Workers → Tester → Hecho -- coloreado en vivo; "Tester" es la validacion
  que **ya existe** (`test`/`lint`/`typecheck`/`build`), no una etapa nueva.
- Requiere el CLI `claude` en el PATH; `agents architect` avisa claro si falta.

## Endurecimiento post-V1

Una revision externa de las 8 fases encontro varios problemas reales una vez la V1 estaba
completa. Se corrigieron:

- **Payload de DeepSeek incorrecto.** `reasoning_effort` iba anidado dentro de `thinking`;
  la API lo esperaba (y lo sigue esperando) como campo hermano. No daba error porque
  DeepSeek ignoraba el campo de mas y aplicaba su default (`high`) — asi que `reasoning: low`
  o `max` en `project.yaml` no tenian efecto real. Corregido y verificado contra la API.
- **`agents run task.md` no aislaba en worktree.** Solo los planes multiworker lo hacian; una
  tarea suelta editaba el checkout principal directamente, pudiendose mezclar con trabajo sin
  commitear. Ahora toda ejecucion (`task`, `retry`, cada task de un `plan`) corre en su propio
  worktree `.agent-worktrees/run-<id>`; el checkout principal no se toca nunca.
- **`agents retry` perdia el contexto.** No conservaba `kind`/`feature`/`task_id`, asi que
  reintentar un plan lo interpretaba como una tarea suelta (mandando el YAML crudo como texto)
  y reintentar la task de un plan se ejecutaba contra el repo principal en vez de un worktree.
  Corregido: el tipo se preserva; una task de plan reintentada suelta pierde la reintegracion
  automatica (se avisa en la salida).
- **Colision de ramas al relanzar la misma feature.** `worktree.create()` borra antes de crear:
  relanzar un plan con el mismo nombre de feature podia borrar la rama de integracion anterior
  junto con sus commits. Los nombres ahora incluyen el ID del run del plan.
- **`shell` no bloqueaba lectura de secretos.** `read_file`/`edit_file` estaban protegidos por
  `safe_path()`, pero `cat .env` o `type .env` via `shell` no. Bloqueados por patron; el
  subprocess tambien deja de heredar variables `*_API_KEY`/`*_SECRET`/`*_TOKEN`/`*_PASSWORD`
  del proceso del orquestador. El timeout que pide el modelo tiene techo (300s).
- **Sin retry ante fallos de infraestructura.** Un 429/5xx o un corte de red consumia un
  intento de escalado Flash/Pro igual que un fallo real del worker. Ahora hay un backoff corto
  (1s/3s/8s) antes de contarlo como fallo.
- **Bugs en `doctor` y `stop`.** El check de la base de datos era `... or True` (siempre
  pasaba); el codigo de salida solo miraba los primeros 6 checks, asi que un `commands.test`
  sin binario instalado no hacia fallar `agents doctor`. `stop` no comprobaba si el `pid` era
  `None` (tasks en cola) ni si habia sido reciclado por otro proceso.
- **Tests con fugas entre ficheros.** Varios monkeypatches (`deepseek.chat`, `worker.run`) no
  se restauraban; bajo `pytest` (un solo proceso) un fichero contaminaba al siguiente. Anadido
  `tests/conftest.py` con un fixture autouse que los restaura.
- **Ownership de `plan.yaml` no verificado.** El scheduler usaba `files` para decidir
  paralelismo, pero solo se lo decia al worker por prompt; nada comprobaba que lo respetara.
  Ahora se compara `files_changed` contra el scope declarado y una violacion queda como
  `SCOPE_VIOLATION` en `issues`, visible para `hybrid-review`.
- Anadidos: CI (`pytest` en cada push/PR), `pytest` como dependencia de desarrollo, deteccion
  de wrappers `mvnw`/`gradlew`.

## Limitaciones conocidas

- ~~La integracion queda en una rama; no hay merge ni PR automatico~~ **`agents integrate` (V2).**
  Bajo demanda, nunca solo: revalida la rama (recorre `commands.test/lint/typecheck/build` en
  su worktree, no confia solo en el resultado guardado) y, si se pide con `--merge` y/o `--pr`,
  la mergea local (`--no-ff`) y/o le hace push + `gh pr create`. Exige `status == completed`
  (nunca `integration_conflict`) y el checkout principal limpio (cambios trackeados sin
  commitear bloquean el merge; lo sin trackear, como un `.agent/` recien creado, no). Sin
  flags, solo valida y dice si esta listo -- ni Claude ni el CLI mergean o abren PR sin que
  se lo pidan explicitamente (ver `hybrid-implement`, paso 8).
- Cada worktree es un checkout limpio: si el proyecto necesita `node_modules` o similar, hay
  que instalar dependencias en el (el worker puede hacerlo con `commands.install`).
- El worker puede dejar algun fichero temporal y `git add -A` lo commitea: revisa
  `files_changed` antes de integrar.
- La validacion usa los comandos del `project.yaml` tal cual: si `agents init` no detecto
  `lint`/`typecheck`, no se comprueban.
- ~~`shell` no es una sandbox real~~ **aislamiento por contenedor, opt-in (V2).** Por defecto
  sigue como en V1: bloqueo de secretos por patron (heuristica), sin aislamiento real de
  disco. Con `workers.sandbox: docker` en `project.yaml`, el `shell` del worker corre en
  `docker run --rm` con el worktree montado en `/work` y nada mas del disco visible -- `.env`
  del checkout principal, `~/.ssh`, credenciales de nube, inalcanzables aunque el comando sea
  indirecto (el ceiling que tenia la heuristica). Sin bloquear red (install/test la
  necesitan): el aislamiento es de disco, no de trafico. `workers.sandbox_image` fija la
  imagen (por defecto `debian:bookworm-slim`, generica y sin stack preinstalado -- para
  Node/Python/etc. usa una imagen con el runtime ya dentro, p.ej. `node:20-slim`). Exige
  Docker instalado (`agents doctor` lo comprueba solo si `workers.sandbox: docker` esta
  activo); sin Docker, sigue funcionando sin sandbox salvo que lo actives.
- ~~`max_parallel` es por ejecucion, no global~~ **corregido (V2).** `router.run_escalated`
  adquiere un slot en SQLite (`storage.acquire_slot`, `BEGIN IMMEDIATE` para que el check y la
  marca sean atomicos entre procesos) antes de invocar al worker: task suelta o task de un plan,
  da igual cuantos `agents run` esten activos en terminales distintas, nunca hay mas de
  `workers.max_parallel` workers `running` a la vez para el mismo proyecto. El que se pasa del
  limite queda `queued` hasta que se libera un hueco; `agents status` lo refleja en vivo.
- ~~Reconciliacion de procesos parcial~~ **corregido (V2).** `storage.reconcile()` marca
  `failed` (con resumen `WORKER_HUERFANO`) los runs `queued`/`running` cuyo pid ya no
  corresponde a un worker vivo (proceso muerto sin avisar, o pid reciclado). Se ejecuta al
  ver `agents status`, y tambien dentro de `acquire_slot` antes de contar workers activos:
  si no, un proceso muerto sin avisar dejaria su fila `running` para siempre y la cola
  global del `max_parallel` se quedaria esperando un hueco que nunca se libera.
  `stop` reusa el mismo check (`storage.pid_es_worker`).
- Un solo proveedor de modelos (DeepSeek), acoplado directamente en `deepseek.py`/`router.py`.
  No se ha abstraido para un segundo proveedor porque no hay uno en uso todavia (YAGNI); si
  llega, es el momento de extraerlo, no antes.

## Tests

```bash
pytest                          # o cada fichero por separado: python tests/test_worker.py
```
