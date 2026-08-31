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

- La integracion queda en una rama; no hay merge ni PR automatico (fuera de alcance en V1).
- Cada worktree es un checkout limpio: si el proyecto necesita `node_modules` o similar, hay
  que instalar dependencias en el (el worker puede hacerlo con `commands.install`).
- El worker puede dejar algun fichero temporal y `git add -A` lo commitea: revisa
  `files_changed` antes de integrar.
- La validacion usa los comandos del `project.yaml` tal cual: si `agents init` no detecto
  `lint`/`typecheck`, no se comprueban.
- **`shell` no es una sandbox real.** El bloqueo de secretos es por patron (heuristica, no
  aislamiento): un comando suficientemente indirecto podria evadirlo. Aislar de verdad
  significa un contenedor por worker, que es una pieza de infraestructura que el roadmap
  descarta explicitamente para esta V1 (`git/agent-orchestrator/ROADMAP.md`, seccion 7).
  Usalo sobre repos de confianza, no sobre codigo que no revisarias tu mismo.
- **`max_parallel` es por ejecucion, no global.** Dos `agents run plan.yaml` simultaneos (dos
  terminales) pueden sumar mas workers que el limite configurado. Un daemon con cola global lo
  resolveria; para un desarrollador trabajando solo, no ha compensado la complejidad todavia.
- **Reconciliacion de procesos parcial.** Si el proceso de un run desaparece sin avisar (PC
  apagado, kill -9), el run se queda `running` en SQLite hasta que alguien lo note; `stop`
  verifica que el pid siga siendo el worker antes de matarlo, pero nada lo detecta solo.
- Un solo proveedor de modelos (DeepSeek), acoplado directamente en `deepseek.py`/`router.py`.
  No se ha abstraido para un segundo proveedor porque no hay uno en uso todavia (YAGNI); si
  llega, es el momento de extraerlo, no antes.

## Tests

```bash
pytest                          # o cada fichero por separado: python tests/test_worker.py
```
