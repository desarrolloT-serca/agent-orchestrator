---
name: hybrid-implement
description: Implementa una funcionalidad delegando la programacion a workers DeepSeek via agent-orchestrator (`agents`). Usala cuando el usuario pida implementar una feature, un refactor amplio o varias tareas paralelizables en un repositorio Git ya inicializado con `agents init`. Tu papel es arquitecto y revisor, no programador.
---

# hybrid-implement

Tu diseñas y revisas. DeepSeek programa. Nunca al reves.

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
      Implementa la API de notificaciones en src/server/notifications.ts...
    files: [src/server/**]
    depends_on: []
  - id: frontend
    description: |
      Implementa la UI en src/app/notifications/...
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
- Lo que dependa de un contrato compartido va en `depends_on`, no en paralelo.
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

## 8. Cierre

La rama `agents/<feature>-integration` queda lista pero **no se mergea sola**.
Enseña el diff al usuario y deja que decida. Si te lo pide:

```bash
git merge --no-ff agents/<feature>-integration
```

Nunca hagas push ni abras PR sin que el usuario lo pida.
