---
name: hybrid-status
description: Consulta y resume el estado de los workers DeepSeek del orquestador (`agents status`, `logs`, `history`). Usala cuando el usuario pregunte que estan haciendo los agentes, si termino algo, cuanto se ha gastado o quiera retomar trabajo lanzado en una sesion anterior.
---

# hybrid-status

## Comandos

```bash
agents status                  # runs del proyecto actual
agents status <id>             # detalle de un run; si es un plan, sus tasks
agents logs <id> --tail 40     # salida del worker desacoplado
agents history --all           # historico y coste acumulado
agents metrics --all           # Flash vs Pro: first-pass rate, reintentos, coste
agents stop <id>               # detener uno vivo
```

El estado vive en `~/.agent-orchestrator/orchestrator.db`, asi que sobrevive al
cierre de la sesion: puedes retomar cualquier run de dias anteriores.

Si el usuario tiene varios runs en paralelo y quiere seguirlos en vivo en vez de que tu
repitas `agents status`/`logs` cada poco, sugierele `agents dashboard` (requiere
`pip install -e ".[tui]"`): tabla en vivo, log en streaming y stop/retry/validar por teclado.

## Como resumirselo al usuario

Una tabla corta y un veredicto, no un volcado de JSON:

- que se esta ejecutando y desde cuando;
- que termino, con que estado y donde esta el codigo (rama `agents/*`);
- que fallo y por que (mira `issues` y `tests` del detalle);
- coste acumulado si lo pregunta.

Si algo esta `running` desde hace mucho, mira el log antes de opinar: puede estar
en un bucle de correccion de tests.

Si algo quedo `completed` pero sin revisar, dilo y ofrece `hybrid-review`.

Si hay un run `kind: plan` en `queued` con `parent_id` apuntando a un `kind: architect`
(`agents status <id>` lo muestra), es un plan propuesto que sigue esperando confirmacion
-- dilo explicitamente y ofrece `agents launch <id>` (revisandolo tu primero, ver
`hybrid-implement`) o `agents discard <id>`. No lo cuentes como "en cola" sin mas: a
diferencia de una task esperando hueco en la cola global, este no arrancara solo nunca.
