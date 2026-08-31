# ROADMAP — Orquestador híbrido Claude Code + DeepSeek

## 1. Objetivo

Construir un orquestador local reutilizable que permita usar:

* **Claude Code** como arquitecto, coordinador y reviewer.
* **DeepSeek V4 Flash** como worker principal de programación.
* **DeepSeek V4 Pro** como escalado para tareas difíciles.
* **Git + Git worktrees** para aislar workers paralelos.
* **GitHub** como repositorio remoto y destino de ramas/PR.
* **SQLite** para estado, métricas y recuperación.
* Un pequeño **daemon local** para mantener ejecuciones independientes de la sesión de Claude.

El flujo objetivo será:

```text
                        USUARIO
                           │
                           ▼
                     Claude Code
                           │
                analiza / planifica
                           │
                           ▼
                  agent-orchestrator
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
          Worker A      Worker B      Worker C
          DeepSeek      DeepSeek      DeepSeek
           Flash         Flash          Pro
              │            │            │
           worktree     worktree     worktree
              │            │            │
              └────────────┼────────────┘
                           ▼
                        Git diff
                           │
                           ▼
                    Claude Reviewer
                           │
                 ┌─────────┴─────────┐
                 │                   │
               REWORK              PASS
                 │                   │
             DeepSeek              GitHub
                                     │
                                     ▼
                                     PR
```

---

# 2. Principios de diseño

El proyecto deberá respetar desde el principio estas reglas.

### Claude toma decisiones; DeepSeek ejecuta

Claude será responsable principalmente de:

* comprender requisitos;
* estudiar arquitectura;
* decidir enfoque;
* dividir funcionalidades;
* definir contratos;
* revisar resultados;
* detectar problemas;
* aprobar/rechazar.

DeepSeek será responsable principalmente de:

* explorar la parte asignada del repositorio;
* implementar;
* editar archivos;
* ejecutar comandos;
* ejecutar tests;
* corregir errores;
* preparar cambios.

---

### El worker que programa nunca se aprueba a sí mismo

Flujo obligatorio:

```text
DeepSeek implementa
       ↓
tests automáticos
       ↓
Claude revisa
       ↓
PASS / REWORK
```

---

### El sistema debe ser agnóstico al proyecto

El orquestador NO contendrá conocimiento específico de Next.js, Java, PHP, etc.

Eso estará en:

```text
<proyecto>/.agent/project.yaml
```

y en las skills/reglas propias del repositorio.

---

### Multiagente solamente cuando aporte valor

No queremos:

```text
Cambiar botón
↓
Architect
↓
4 workers
↓
Reviewer
```

Para tareas pequeñas Claude puede trabajar solo.

El orquestador se usará principalmente cuando exista trabajo delegable.

---

# 3. Stack elegido

## Orquestador

**Python 3.12+**

Motivos:

* `sqlite3` integrado;
* excelente gestión de procesos;
* sencillo para CLI;
* multiplataforma;
* poco peso;
* excelente para automatización;
* fácil acceso HTTP a DeepSeek;
* no depende del stack de los proyectos.

Librerías iniciales:

```text
typer
pydantic
httpx
rich
psutil
```

Y poco más.

No meteremos frameworks web inicialmente.

---

## Persistencia

```text
SQLite
```

Archivo:

```text
~/.agent-orchestrator/orchestrator.db
```

No Redis.

No PostgreSQL.

---

## IA

```text
Claude Code
DeepSeek V4 Flash
DeepSeek V4 Pro
```

Política inicial:

```text
default → V4 Flash / high

fallo serio
     ↓
retry Flash

segundo fallo
     ↓
V4 Pro / high

problema excepcional
     ↓
V4 Pro / max
```

---

# 4. Estructura del repositorio

Crearemos un repositorio independiente:

```text
agent-orchestrator/
│
├── pyproject.toml
├── README.md
├── .env.example
│
├── src/
│   └── orchestrator/
│       │
│       ├── cli/
│       │   ├── init.py
│       │   ├── run.py
│       │   ├── status.py
│       │   ├── stop.py
│       │   ├── retry.py
│       │   └── doctor.py
│       │
│       ├── core/
│       │   ├── models.py
│       │   ├── config.py
│       │   ├── task.py
│       │   └── events.py
│       │
│       ├── deepseek/
│       │   ├── client.py
│       │   ├── prompts.py
│       │   ├── models.py
│       │   └── usage.py
│       │
│       ├── worker/
│       │   ├── worker.py
│       │   ├── loop.py
│       │   ├── context.py
│       │   └── tools/
│       │       ├── read.py
│       │       ├── search.py
│       │       ├── edit.py
│       │       ├── shell.py
│       │       └── git.py
│       │
│       ├── git/
│       │   ├── repository.py
│       │   ├── worktree.py
│       │   └── branch.py
│       │
│       ├── scheduler/
│       │   ├── scheduler.py
│       │   └── dependencies.py
│       │
│       ├── daemon/
│       │   ├── server.py
│       │   ├── processes.py
│       │   └── protocol.py
│       │
│       ├── storage/
│       │   ├── database.py
│       │   ├── migrations.py
│       │   └── repositories/
│       │
│       └── reporting/
│           ├── result.py
│           ├── metrics.py
│           └── diff.py
│
├── claude/
│   └── skills/
│       ├── hybrid-implement/
│       ├── hybrid-review/
│       └── hybrid-status/
│
└── tests/
```

No tenemos que construir todo esto desde el primer día.

Es la estructura objetivo.

---

# FASE 1 — Fundación del orquestador

## Objetivo

Conseguir un ejecutable local:

```bash
agents
```

que pueda reconocer y configurar cualquier proyecto Git.

## Implementar

Comandos:

```bash
agents --help
agents init
agents doctor
agents config
```

`agents init` analizará el repositorio actual.

Detectará inicialmente:

```text
Git
lenguaje
package manager
comandos disponibles
test framework
build system
estructura básica
```

Y creará:

```text
.agent/
└── project.yaml
```

Ejemplo:

```yaml
version: 1

project:
  name: nexora

repository:
  provider: github

commands:
  install: pnpm install
  test: pnpm test
  lint: pnpm lint
  build: pnpm build
  typecheck: pnpm tsc --noEmit

workers:
  max_parallel: 3
  default_model: deepseek-v4-flash
  reasoning: high

paths:
  source:
    - src

protected:
  - .env
  - .git
```

## `agents doctor`

Debe comprobar:

```text
✓ Python
✓ Git
✓ repositorio válido
✓ project.yaml
✓ DeepSeek API key
✓ acceso DeepSeek
✓ comandos del proyecto
```

## Fin de fase

Debe funcionar:

```bash
cd cualquier-proyecto
agents init
agents doctor
```

### Criterio de aceptación

Podemos inicializar como mínimo:

* un proyecto Next.js;
* un proyecto Java/Maven;

sin modificar el código del orquestador.

---

# FASE 2 — Primer DeepSeek Worker

Esta es la fase realmente crítica.

## Objetivo

Conseguir:

```text
TASK
 ↓
DeepSeek
 ↓
lee repo
 ↓
modifica código
 ↓
resultado
```

con **un único worker**.

Todavía sin paralelismo.

## Crear

```bash
agents run task.md
```

Ejemplo de `task.md`:

```text
# Objetivo

Añadir endpoint GET /api/customers/:id.

# Requisitos

- utilizar CustomerService existente;
- devolver 404 cuando no exista;
- no modificar autenticación;
- añadir tests.

# Validación

pnpm test
pnpm typecheck
```

---

## Agent loop

Éste es el corazón de todo el proyecto.

DeepSeek no debe responder simplemente con código.

Debe tener herramientas.

```text
DeepSeek
   │
   ▼
tool_call
   │
   ├── read_file
   ├── list_directory
   ├── search
   ├── edit_file
   ├── shell
   └── git_diff
```

Loop:

```text
prompt
   ↓
DeepSeek
   ↓
tool call
   ↓
ejecutamos herramienta
   ↓
resultado
   ↓
DeepSeek
   ↓
nuevo tool call
   ↓
...
   ↓
finish
```

DeepSeek soporta tool calling oficialmente, por lo que utilizaremos su mecanismo nativo.

---

## Herramientas V1

Solamente:

### `read_file`

```text
path
start_line
end_line
```

### `list_directory`

```text
path
depth
```

### `search_code`

```text
query
path
```

Usaremos preferentemente `rg`/ripgrep si está disponible.

### `edit_file`

Modificación controlada.

### `shell`

Con timeout.

### `git_diff`

Para inspeccionar cambios.

---

## Seguridad inicial

El worker NO podrá:

```text
rm -rf
git push --force
git reset --hard
git clean -fd
acceder fuera del worktree
leer .env
leer claves
```

Debe existir una capa de validación antes de ejecutar comandos.

---

## Fin de fase

Damos al worker una pequeña funcionalidad real.

Debe:

1. descubrir archivos;
2. modificarlos;
3. ejecutar tests;
4. corregir errores;
5. terminar;
6. devolver un resumen.

### Criterio de aceptación

Una tarea S real completada sin intervención humana durante el loop.

---

# FASE 3 — Worker de programación completo

Ahora hacemos que el worker sea fiable.

## Añadir gestión de contexto

No enviar siempre el repositorio entero.

El worker irá descubriéndolo mediante herramientas.

Contexto inicial:

```text
project.yaml
task
reglas del worker
git status
estructura superior del proyecto
```

Después solicita lo que necesita.

---

## Añadir límites

Por ejecución:

```yaml
limits:
  max_iterations: 60
  max_runtime_minutes: 60
  max_cost_usd: 5
  max_tool_errors: 10
```

Si supera límites:

```text
WORKER_ABORTED
```

No permitiremos loops infinitos.

---

## Tests

Al finalizar:

```text
worker
  ↓
project validation
  │
  ├── test
  ├── lint
  ├── typecheck
  └── build
```

según `project.yaml`.

---

## Resultado estructurado

Cada worker devolverá:

```json
{
  "status": "completed",
  "summary": "...",
  "files_changed": [],
  "tests": {},
  "issues": [],
  "model": "deepseek-v4-flash",
  "usage": {},
  "cost": 0.0
}
```

---

## Fin de fase

Nuestro DeepSeek Worker ya se comporta como un coding agent real.

### Criterio

Probar al menos:

```text
bug
pequeña feature
refactor
tests
```

y documentar dónde falla.

---

# FASE 4 — Daemon + estado persistente

Hasta aquí `agents run` puede ejecutarse en foreground.

Ahora convertimos el sistema en un verdadero orquestador.

## Añadir daemon

```bash
agents daemon start
agents daemon stop
agents daemon status
```

El daemon mantiene los procesos.

Claude puede cerrar su sesión y los workers continúan.

---

## SQLite

Modelo inicial:

### projects

```text
id
path
name
config
```

### features

```text
id
project_id
description
status
created_at
```

### tasks

```text
id
feature_id
name
status
dependency
worker_id
```

### runs

```text
id
task_id
model
started_at
finished_at
status
```

### usage

```text
run_id
input_tokens
cached_tokens
output_tokens
cost
```

---

## CLI

Añadimos:

```bash
agents status
agents logs
agents stop
agents retry
agents history
```

Ejemplo:

```text
FEATURE-17

Backend API
████████████████████ 100%
✓ Completed

Frontend
████████████░░░░░░░ 68%
Running · Flash · 08:32

Tests
Waiting
depends on frontend
```

---

## Fin de fase

Podemos:

1. lanzar worker;
2. cerrar Claude;
3. volver posteriormente;
4. consultar estado;
5. recuperar resultado.

---

# FASE 5 — Multiworker + Git worktrees

Aquí aparece finalmente nuestro sistema multiagente.

## Task specification

Claude proporcionará al orquestador algo estructurado:

```yaml
feature: notifications

tasks:

  - id: backend
    description: Implement notification API
    model: auto
    files:
      - src/server/**
    depends_on: []

  - id: frontend
    description: Implement notification UI
    model: auto
    files:
      - src/app/**
    depends_on: []

  - id: tests
    description: Add integration tests
    model: auto
    depends_on:
      - backend
      - frontend
```

---

## Worktrees

El orquestador crea automáticamente:

```text
.agent-worktrees/

feature-17-backend/
feature-17-frontend/
feature-17-tests/
```

Cada worker trabaja aislado.

---

## Scheduler

Debe soportar:

```text
A ────────┐
          ├──► C
B ────────┘
```

A y B en paralelo.

C cuando ambos terminan.

---

## Ownership

Siempre que sea posible:

```text
Backend Worker
src/server/**

Frontend Worker
src/app/**

Tests Worker
tests/**
```

Si dos tasks necesitan editar los mismos archivos, el scheduler puede serializarlas.

---

## Integración

Primera versión:

```text
worker branch
    ↓
commit
    ↓
orchestrator cherry-pick
    ↓
integration branch
```

Si hay conflicto:

```text
INTEGRATION_CONFLICT
```

No permitiremos que DeepSeek resuelva automáticamente un conflicto complejo sin supervisión en V1.

---

## Fin de fase

Primera feature M real:

```text
backend
frontend
tests
```

trabajando simultáneamente.

### Criterio

Demostrar reducción real del tiempo frente a Claude Code solo.

---

# FASE 6 — Integración profunda con Claude Code

Hasta aquí podemos manejar todo manualmente:

```bash
agents ...
```

Ahora hacemos que Claude entienda el sistema.

## Skill global

Crearemos:

```text
~/.claude/skills/hybrid-implement/
└── SKILL.md
```

Claude Code carga las skills bajo demanda, lo que permite incorporar este workflow sin inflar permanentemente su contexto.

Comando:

```text
/hybrid-implement
```

Claude deberá:

1. estudiar requisito;
2. estudiar repositorio;
3. decidir si delegar;
4. crear plan;
5. generar tasks;
6. invocar `agents run`;
7. esperar/consultar resultados;
8. revisar cambios;
9. solicitar correcciones si procede.

---

## Segunda skill

```text
/hybrid-review
```

Revisión obligatoria:

```text
architecture
correctness
security
tests
regressions
duplication
maintainability
acceptance criteria
```

---

## Hooks

Utilizaremos hooks solamente donde haya comportamiento determinista.

Ejemplos:

```text
tras finalizar worker
→ guardar métricas

antes de aceptar resultado
→ ejecutar tests

al terminar Claude Review
→ generar report
```

No usaremos hooks para decisiones arquitectónicas.

---

## Fin de fase

Experiencia objetivo:

```text
> /hybrid-implement

Implementa permisos granulares por módulo.
```

Y Claude se ocupa del flujo completo.

El usuario no necesita ejecutar manualmente:

```bash
agents ...
```

salvo para administración/debug.

---

# FASE 7 — Router Flash / Pro + recuperación automática

Ahora optimizamos calidad/coste.

## Política V1

### DeepSeek V4 Flash

Predeterminado.

Usar para:

```text
implementación estándar
frontend
backend
tests
CRUD
refactors locales
migrations sencillas
```

### DeepSeek V4 Pro

Usar para:

```text
cambios cross-module complejos
bugs difíciles
exploración extensa
fallos repetidos
tareas con mucha autonomía
```

DeepSeek actualmente expone tanto `deepseek-v4-flash` como `deepseek-v4-pro`, ambos con tool calls y contexto de 1M tokens.

---

## Escalado automático

```text
FLASH
  │
  ▼
 ejecución
  │
 ┌┴───────────────┐
 │                │
PASS             FAIL
 │                │
fin          analizar fallo
                  │
             retry Flash
                  │
              ┌───┴───┐
              │       │
            PASS     FAIL
                      │
                      ▼
                   V4 PRO
```

Claude puede saltarse este proceso:

```text
model: pro
```

si considera la tarea especialmente difícil.

---

## Clasificación

No intentaremos inicialmente crear un modelo de scoring complejo.

Usaremos reglas sencillas:

```text
simple
normal
hard
```

y métricas reales.

---

## Fin de fase

Debemos poder comparar:

```text
Flash first-pass rate
Pro first-pass rate
coste
duración
retries
```

por tipo de tarea.

---

# FASE 8 — Producto estable y portable

Esta fase convierte el experimento en una herramienta que usarás diariamente.

## `agents init` avanzado

Debe detectar automáticamente perfiles.

Ejemplos:

```text
nextjs
node
java-maven
java-gradle
python
php
rust
```

---

## Configuración global

```text
~/.agent-orchestrator/config.yaml
```

Ejemplo:

```yaml
deepseek:
  default_model: deepseek-v4-flash
  reasoning: high

workers:
  global_max_parallel: 4

limits:
  default_cost_usd: 5
  default_runtime_minutes: 60

git:
  provider: github
```

---

## Configuración específica

Cada proyecto:

```text
.agent/project.yaml
```

Solo almacena diferencias.

Esto hará portable el sistema.

---

## Packaging

Objetivo:

```bash
pipx install agent-orchestrator
```

o equivalente.

Después:

```bash
cd proyecto-nuevo
agents init
```

y listo.

---

## Logs

```text
~/.agent-orchestrator/
├── orchestrator.db
├── logs/
└── work/
```

---

## Limpieza

```bash
agents clean
```

Debe eliminar de forma segura:

* worktrees terminados;
* branches temporales;
* logs antiguos;
* ejecuciones temporales.

Nunca tocará ramas o archivos que no sean propiedad del orquestador.

---

# 5. Comandos finales previstos

Cuando esté terminado:

```bash
agents init

agents doctor

agents run <task>

agents run <plan.yaml>

agents status

agents status <feature>

agents logs <worker>

agents stop <worker>

agents retry <worker>

agents retry <worker> --model pro

agents history

agents metrics

agents clean

agents daemon start
agents daemon stop
agents daemon status
```

Y normalmente ni siquiera necesitarás utilizarlos directamente porque Claude tendrá:

```text
/hybrid-implement
/hybrid-review
/hybrid-status
```

---

# 6. Métricas obligatorias

Desde el primer worker guardaremos:

```text
proyecto
feature
task
modelo
reasoning
inicio
fin
duración
input tokens
cache hit tokens
output tokens
coste
tool calls
iterations
tests iniciales
tests finales
retries
escalations
files changed
Claude verdict
human verdict
```

Esto será fundamental.

Después de 30-50 features tendremos nuestros propios datos para responder:

```text
¿Flash o Pro?

¿1, 2 o 3 workers?

¿qué tareas merece delegar?

¿cuánto Claude estamos ahorrando?

¿cuánto tiempo estamos ahorrando?

¿qué stacks funcionan mejor?
```

---

# 7. Lo que NO construiremos inicialmente

Queda explícitamente fuera:

```text
❌ dashboard web
❌ VPS
❌ MCP
❌ Redis
❌ PostgreSQL
❌ Kubernetes
❌ sistema de usuarios
❌ Jira
❌ Telegram
❌ interfaz gráfica
❌ auto-merge
❌ infraestructura distribuida
```

Ninguna de esas cosas mejora el objetivo inicial:

> Claude diseña → DeepSeek implementa → Claude revisa.

Si algún día necesitamos alguna, se añade sobre el core existente.

---

# 8. Hitos principales

## HITO A — Worker vivo

Fases 1-2.

Tenemos:

```text
Claude/manual
     ↓
DeepSeek
     ↓
edita repo
     ↓
tests
```

**Primer momento en el que el proyecto demuestra que la idea funciona.**

---

## HITO B — Coding agent fiable

Fase 3.

DeepSeek:

```text
explora
implementa
testea
corrige
reporta
```

sin supervisión continua.

---

## HITO C — Orquestador real

Fase 4.

Tenemos daemon, SQLite, jobs, logs y recuperación.

---

## HITO D — Multiagente

Fase 5.

```text
              Orchestrator
             /      |      \
            /       |       \
       Backend   Frontend   Tests
```

con worktrees reales.

---

## HITO E — Claude + DeepSeek integrado

Fase 6.

Ya no manejamos dos sistemas.

Claude Code es la interfaz del conjunto.

---

## HITO F — Sistema inteligente

Fase 7.

Flash/Pro, retry, escalado y métricas.

---

## HITO G — Versión 1.0

Fase 8.

Instalable y reutilizable en cualquier repositorio.

---

# 9. Orden estricto de implementación

No saltaremos fases.

```text
01
CLI + configuración
       ↓
02
DeepSeek API
       ↓
03
tool calling
       ↓
04
worker loop
       ↓
05
tests + límites
       ↓
06
SQLite
       ↓
07
daemon
       ↓
08
worktrees
       ↓
09
multiworker
       ↓
10
scheduler
       ↓
11
Claude skill
       ↓
12
Claude reviewer
       ↓
13
Flash → Pro
       ↓
14
métricas
       ↓
15
packaging
```

---

# 10. Estimación de esfuerzo

Para una primera V1 funcional, trabajando con IA para ayudarnos a programarla:

| Parte                  | Complejidad |
| ---------------------- | ----------- |
| CLI/config             | Baja        |
| DeepSeek client        | Baja        |
| Tool calling           | Media       |
| Worker loop            | **Alta**    |
| Seguridad herramientas | Media/alta  |
| SQLite/estado          | Media       |
| Daemon                 | Media       |
| Git worktrees          | Media       |
| Scheduler              | Media       |
| Claude integration     | Baja/media  |
| Model router           | Baja        |
| Métricas               | Baja/media  |

El componente al que debemos dedicar más atención es:

## **Worker loop**

Porque si DeepSeek no puede trabajar de forma fiable sobre el repositorio, todo lo demás sobra.

Por eso no construiremos daemon, scheduler ni multiworker hasta demostrar:

```text
UNA TASK
   ↓
UN DEEPSEEK
   ↓
UN REPO REAL
   ↓
IMPLEMENTACIÓN CORRECTA
```

---

# 11. Primera versión que considero realmente útil

No necesitamos llegar a la Fase 8 para utilizarlo.

Nuestro primer producto diario aparece al terminar **Fase 6**:

```text
Claude Code
     │
     ▼
planifica feature
     │
     ▼
orchestrator
     │
 ┌───┼────┐
 ▼   ▼    ▼
DS   DS   DS
 │   │    │
 └───┼────┘
     ▼
 integración
     │
     ▼
Claude Review
     │
     ▼
resultado
```

Fases 7-8 serán principalmente optimización y madurez.

---

# 12. Primer sprint

El primer sprint queda reducido a un único objetivo:

## Crear el primer DeepSeek coding worker.

Orden:

```text
1. Crear repo agent-orchestrator

2. Crear proyecto Python

3. Crear configuración .env
   DEEPSEEK_API_KEY

4. Implementar DeepSeekClient

5. Implementar:
   read_file
   list_directory
   search_code
   edit_file
   shell
   git_diff

6. Implementar agent loop

7. Darle una tarea real pequeña

8. Verlo modificar un repo

9. Hacer que ejecute tests

10. Analizar el resultado con Claude
```

Cuando el punto 10 funcione:

**tenemos el núcleo del proyecto.**

Todo lo demás es orquestación alrededor de ese núcleo.
