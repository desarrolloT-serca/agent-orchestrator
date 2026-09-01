---
name: hybrid-implement
description: Implementa una funcionalidad delegando la programacion a workers DeepSeek via agent-orchestrator (`agents`). Usala cuando el usuario pida implementar una feature, un refactor amplio o varias tareas paralelizables en un repositorio Git ya inicializado con `agents init`. Tu papel es arquitecto y revisor, no programador.
---

# hybrid-implement

Tu diseñas y revisas. DeepSeek programa. Nunca al reves.

Esta skill es para cuando el usuario ya esta hablando contigo (el caso normal): tu eres
el arquitecto de esta sesion. Si en vez de eso te pide automatizar ese primer paso sin una
conversacion en vivo (p.ej. "que el propio orquestador escriba el plan"), existe
`agents architect "<descripcion>"` (V2, ver README seccion "Arquitecto"): invoca el CLI de
Claude en modo no interactivo para proponer el plan. Son dos caminos al mismo sitio, no
dos herramientas distintas — pero **el arquitecto automatizado tampoco se aprueba a si
mismo**: antes de `agents launch`, lee el plan.yaml que propuso (`agents status <id>`) con
el mismo criterio que si lo hubieras escrito tu. No hubo conversacion contigo de por medio,
asi que puede haber trasladado mal un requisito o partido mal las tasks.

```bash
agents architect "añade notificaciones por email al confirmar un pedido"
agents status <id>      # lee el plan.yaml propuesto antes de nada
agents launch <id>      # confirmado -> lanza a los workers de verdad
agents discard <id>     # o descartalo si no convence y reformula la descripcion
```

## 1. Antes de nada: ¿merece delegar?

Hazlo tu mismo, sin orquestador, si la tarea es:

- un cambio en uno o dos ficheros que ya conoces;
- un ajuste de texto, estilo o configuracion;
- algo que requiere hablar con el usuario a mitad de camino;
- una decision de arquitectura (eso es tuyo siempre).

Delega cuando haya trabajo mecanico y acotado: implementar contra un contrato ya
definido, cubrir tests, aplicar un patron repetitivo en varios sitios, o varias
partes independientes que pueden ir en paralelo.

Si dudas, delega solo la parte mecanica y quedate el diseño.

## 2. Comprueba el entorno

```bash
agents doctor
```

Si falta `.agent/project.yaml`, ejecuta `agents init` y revisa que los comandos
detectados (`test`, `lint`, `typecheck`, `build`) son los correctos: el worker se
valida con ellos. Corrigelos a mano en el YAML si hace falta.

## 3. Estudia el repositorio de verdad

Lee el codigo que la tarea va a tocar antes de escribir el plan: contratos
existentes, convenciones, servicios reutilizables. El worker es literal — lo que
no le digas, se lo inventara a su manera.

## 4. Escribe la tarea

### Una sola tarea: `task.md`

```markdown
# Objetivo
Una frase.

# Requisitos
- reutiliza `CustomerService` (src/services/customer.ts);
- devuelve 404 cuando no exista;
- no toques la autenticacion;
- anade tests.

# Validacion
pnpm test
pnpm typecheck
```

Reglas que marcan la diferencia:

- nombra ficheros y simbolos concretos, con su ruta;
- di explicitamente que NO debe tocar;
- pon los comandos de validacion;
- un objetivo por tarea.

### Varias tareas: `plan.yaml`

```yaml
feature: notifications
tasks:
  - id: backend
    description: |
      Implementa la API de notificaciones en src/server/notifications.ts.

      Contrato con el frontend (task "frontend", en paralelo, no lo vera):
      GET /api/notifications responde
      { notifications: [{ id, title, read, createdAt }], total }.
      No cambies estos nombres de campo sin avisar.
    files: [src/server/**]
    depends_on: []
  - id: frontend
    description: |
      Implementa la UI en src/app/notifications/...

      Contrato con el backend (task "backend", en paralelo, no lo veras):
      GET /api/notifications responde
      { notifications: [{ id, title, read, createdAt }], total }.
      Usa exactamente esos nombres de campo, no los inventes.
    files: [src/app/**]
    depends_on: []
  - id: tests
    description: |
      Anade tests de integracion...
    files: [tests/**]
    depends_on: [backend, frontend]
```

- `files` es propiedad exclusiva: si dos tasks se solapan, el scheduler las
  serializa y pierdes el paralelismo. Reparte por directorios disjuntos.
- **Backend y frontend en paralelo (sin `depends_on` entre ellos) comparten
  un contrato — la forma exacta de la API — que ninguno de los dos ve del
  otro mientras trabaja.** Si no fijas ese contrato palabra por palabra en
  AMBAS descripciones (mismos nombres de campo, mismo shape de respuesta),
  cada worker se lo inventa por su cuenta, de forma coherente consigo mismo
  pero no con el otro. Los tests de cada lado pasan igual porque cada uno
  mockea al otro — el desajuste no se ve hasta produccion. Si el contrato es
  complejo o no puedes fijarlo de antemano con precision, no lo pongas en
  paralelo: usa `depends_on` (backend primero, frontend leyendo el codigo
  real ya hecho).
- `model: deepseek-v4-pro` en una task concreta si es la dificil.

## 5. Lanza y espera

```bash
agents run task.md -d          # o: agents run plan.yaml -d
agents status                  # tabla de runs del proyecto
agents status <id>             # detalle + tasks hijas de un plan
agents logs <id> --tail 40     # que esta haciendo el worker
agents stop <id>               # cortarlo
```

Con `-d` el worker sobrevive al cierre de la sesion. No te quedes bloqueado
esperando: informa al usuario del ID y consulta el estado despues.

Si el usuario quiere seguir varios runs en paralelo el mismo, sin que tu repitas
`agents status`/`logs`, sugierele `agents dashboard` (opt-in, `pip install -e ".[tui]"`):
tabla en vivo, log en streaming, y stop/retry/validar por teclado.

Estados: `completed`, `validation_failed` (los tests del proyecto fallaron),
`aborted` (limite de iteraciones/coste/tiempo), `failed`, `integration_conflict`.

## 6. Revisa SIEMPRE

El worker no se aprueba a si mismo. Cuando termine:

```bash
git diff <base>..agents/<feature>-integration     # plan multiworker
git -C .agent-worktrees/run-<id> diff HEAD~1      # task suelta
```

Aplica la skill `hybrid-review`. No des por bueno un resultado porque los tests
pasen: puede haber duplicado logica, saltado convenciones o resuelto otro problema.

## 7. Rework

- Correccion pequeña y evidente: arreglala tu, es mas rapido.
- Falta de fondo: `agents retry <id> --model deepseek-v4-pro`.
- Requisito mal explicado: reescribe el task.md y vuelve a lanzarlo.

### `integration_conflict`: dos tasks tocaron lo mismo al fusionar

El orquestador no resuelve conflictos solo (decision de diseño, no falta por hacer). El
`result["issues"]` del plan trae el worktree exacto y la rama; el cherry-pick se dejo a
medio camino ahi (sin abortar), con las marcas `<<<<<<<` reales en el fichero que choca:

```bash
cd .agent-worktrees/<feature>-<id>-integration
git status                    # que fichero(s), en que task
```

Tu decides, no lo deduzcas de memoria:

- conflicto trivial (import duplicado, orden de rutas): resuelvelo tu ahi mismo,
  `git add <fichero>` y `git cherry-pick --continue`;
- conflicto de fondo (dos tasks reimplementaron lo mismo, contrato no fijado — ver la
  nota de backend/frontend en paralelo del paso 4): `git cherry-pick --abort`, decide que
  version se queda y relanza la otra task con el requisito corregido;
- si dudas, enseña el diff en conflicto al usuario antes de decidir.

## 8. Cierre

La rama `agents/<feature>-integration` queda lista pero **no se mergea sola**.
Enseña el diff al usuario y deja que decida.

Si te lo pide, usa `agents integrate` en vez de `git merge` a mano: revalida la rama
(vuelve a correr `test`/`lint`/`typecheck`/`build` en su worktree, no se fia solo del
resultado que guardo el worker) antes de tocar nada.

```bash
agents integrate <id>            # solo valida, ensena si esta listo; no toca nada
agents integrate <id> --merge    # + git merge --no-ff a la rama que tengas activa
agents integrate <id> --pr       # + git push y gh pr create
```

`--pr` hace push de la rama al remoto: es una accion visible fuera de tu maquina, igual de
"pedir permiso primero" que un `git push` cualquiera. Nunca lances `--pr` (ni `--merge`) porque
si, solo cuando el usuario ha dicho explicitamente que quiere mergear o abrir el PR.
