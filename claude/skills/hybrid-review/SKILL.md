---
name: hybrid-review
description: Revision obligatoria del trabajo de un worker DeepSeek antes de aceptarlo o integrarlo. Usala tras un `agents run`, sobre la rama `agents/*` o el diff de un worktree, y siempre que haya que dar un veredicto PASS o REWORK sobre codigo generado por el orquestador.
---

# hybrid-review

El worker que programa nunca se aprueba a si mismo. Aqui decides tu.

## Que revisar

```bash
agents status <id>                                # resultado, tests, coste, issues
git diff <base>..agents/<feature>-integration     # el cambio completo
```

Lee el diff entero. Si es grande, leelo por ficheros, pero completo: los fallos
de un worker suelen estar en lo que anadio de mas, no en lo que le pediste.

## Checklist

Recorrela en este orden y no la abrevies:

1. **Criterios de aceptacion** — ¿hace exactamente lo que pedia la tarea? ¿Todo?
2. **Correccion** — casos limite, nulos, errores, concurrencia, tipos.
3. **Arquitectura** — ¿respeta las capas y contratos del proyecto o mete logica donde no toca?
4. **Duplicacion** — ¿reimplemento algo que ya existia en el repo? Es el fallo mas
   frecuente: busca el helper equivalente antes de aceptar.
5. **Seguridad** — entradas sin validar, secretos, permisos, SQL, rutas.
6. **Tests** — ¿prueban comportamiento real o solo que el codigo se ejecuta?
   ¿Cubren los casos de error?
7. **Regresiones** — ¿toco algo fuera de su ambito? Compara `files_changed` con
   el `files` que tenia asignado.
8. **Mantenibilidad** — nombres, tamaño de funciones, comentarios inutiles,
   ficheros temporales colados en el commit (`out.txt`, logs, artefactos).

## Veredicto

Da uno explicito:

- **PASS** — cumple. Resume en 3 lineas que hace y cual es el riesgo residual.
- **REWORK** — lista de problemas concretos, cada uno con fichero y linea, y que
  se espera en su lugar.

Con REWORK, decide como sigue:

| Situacion | Accion |
| --- | --- |
| Error puntual y obvio | Arreglalo tu; es mas barato que otra vuelta |
| Varios fallos del mismo tipo | Reescribe el task.md y relanza |
| El worker no entendio el problema | `agents retry <id> --model deepseek-v4-pro` |
| Conflicto de integracion | Resuelvelo tu, nunca lo automatices |

## Reglas

- Los tests en verde no son un aprobado; son el minimo para empezar a revisar.
- Un diff mas grande de lo que pedia la tarea es sospechoso por defecto.
- No apruebes codigo que no has leido.
