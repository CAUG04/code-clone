# Instrucciones para Claude en este repositorio

Este repo es **Code Clone**, un agente que genera aplicaciones full-stack a partir de una entrevista en lenguaje natural. Lee el `README.md` para el detalle completo.

## Si el usuario te pide construir una app

El flujo normal de `python cli.py` es **interactivo**: usa `questionary`, que necesita una terminal de verdad con flechas y Enter. En una sesión de nube eso no existe, así que **no corras `python cli.py` sin argumentos**: se cuelga o falla.

En su lugar, **haz tú la entrevista** en la conversación y luego genera el proyecto sin interacción:

1. **Pregúntale al usuario**, en español, en 1 o 2 rondas de máximo 3 preguntas cada una, con opciones concretas sugeridas. Cubre: usuarios objetivo, funcionalidades clave, qué datos se guardan, roles y permisos, estilo visual, integraciones. Propón al menos una mejora que el usuario no pidió. Pregúntale también si quiere **web, móvil o ambas**.

2. **Escribe un `spec.json`** con el plan. El formato exacto está en `agent/interview.py` (constante `SPEC_SCHEMA`), y hay un ejemplo real en cualquier proyecto ya generado bajo `proyectos/<app>/spec.json`. Reglas importantes:
   - Tipos de campo válidos: `string`, `text`, `integer`, `float`, `boolean`, `date`, `datetime`, `email`, `url`.
   - Nombres de entidad en inglés, PascalCase singular (`Product`). Campos en inglés, snake_case (`unit_price`).
   - No incluyas el campo `id` ni una entidad de usuarios: se generan solos.
   - `platform` debe ser `"web"`, `"mobile"` o `"both"`.

3. **Muéstrale el plan al usuario** y espera su visto bueno antes de generar.

4. **Genera:**
   ```bash
   pip install -r requirements.txt     # solo la primera vez
   python cli.py --spec spec.json --yes --no-github
   ```
   Quita `--no-github` si el usuario quiere que se suba a un repo nuevo.

5. **Enséñale el resultado**: qué quedó en `proyectos/<app>/`, y el `README.md` que se generó adentro con los pasos para correrlo.

## Flags que importan

- `--yes` / `-y`: no pregunta nada. **Siempre úsalo** en sesiones sin terminal.
- `--spec archivo.json`: salta la entrevista y usa ese plan.
- `--platform web|mobile|both`: fuerza la plataforma.
- `--idea "texto"`: pasa la idea sin preguntarla.
- `--no-github`: no sube nada.
- `--no-auth` / `--auth`: sobrescribe `needs_auth` del spec. Quitar el login es un cambio de plan (toca modelo, esquemas, frontend y pruebas), así que se regenera con este flag en vez de editar a mano con el modo iteración.
- `--out CARPETA`: dónde dejar el proyecto (default `proyectos/`).

Sin terminal interactiva y sin `--yes`, el CLI falla con un mensaje claro en vez de colgarse. Eso es a propósito.

## El motor de IA

`agent/llm.py` usa **`claude -p`** (el Claude Code local del usuario). No hay clave de API: si el comando `claude` no está en el PATH, el agente falla con instrucciones para instalarlo.

Existe un backend de API como escape hatch, pero solo se activa con `LLM_BACKEND=api` explícito. No lo sugieras.

Ojo: si ya estás corriendo dentro de una sesión de Claude Code, el agente llamaría a `claude -p` anidado. Funciona, pero es más lento y gasta doble. Para generar desde aquí es mejor que **tú** escribas el `spec.json` (paso 2) y corras el CLI con `--spec`, que no necesita llamar al modelo salvo para los datos de ejemplo.

## Probar sin gastar nada

```bash
python cli.py --demo --platform both --yes --no-github
```

Genera la app de ejemplo "GymFlow" sin llamar al modelo. Úsalo para verificar que los generadores siguen funcionando después de tocar plantillas.

Las apps generadas traen su propia suite de pruebas. Después de cambiar plantillas del backend, verifica que sigan pasando:

```bash
cd proyectos/gym-flow/backend && pip install -r requirements.txt && pytest
```

## Pruebas en las apps generadas

Cada proyecto incluye `backend/tests/`:
- `test_api.py` — CRUD por entidad, validación, 404s, orden de la lista.
- `test_security.py` — autenticación obligatoria, tokens manipulados y expirados, **aislamiento entre usuarios**, contraseñas con hash y sal, inyección SQL.

Están parametrizadas por entidad, así que se adaptan solas al `spec.json`. Si agregas un tipo de campo nuevo en `agent/spec.py`, dale también un `test_literal` o los datos de prueba saldrán mal.

Los tests ponen `SKIP_SEED=1` para arrancar con la base vacía; `app/main.py` respeta esa variable.

## Pruebas E2E en las apps generadas

Las apps con web llevan Playwright en `frontend/tests/e2e/`, generado desde `templates/web_frontend/tests/e2e/`.

Lo crítico: las pruebas localizan elementos por `data-testid`, y esos atributos viven en las plantillas del frontend (`App.tsx.j2`, `EntityPage.tsx.j2`, `Login.tsx.j2`, `Home.tsx.j2`). **Si quitas o renombras un `data-testid`, rompes las E2E.** `tests/test_e2e_generado.py` verifica que cada testid usado exista; córrelo después de tocar cualquier plantilla del frontend.

Los prefijos interpolados (`nav-${e.plural}`, `field-${f.name}`, `stat-${e.plural}`) también están verificados.

`_e2e_entities()` en `generator.py` da un valor válido por tipo de campo para llenar formularios. Si agregas un tipo en `spec.py`, agrégalo ahí también.

Aislamiento: con login cada prueba registra un usuario nuevo y eso la aísla. Sin login todas comparten la base, así que `limpiar()` borra todo por la API en cada `beforeEach`, y un `globalSetup` borra el archivo SQLite antes de la corrida.

## Modo iteración

`agent/iterate.py` cambia una app ya generada. El bucle: `ensure_git` → `llm.code_agent` (que es `claude -p` con herramientas, corriendo DENTRO del proyecto) → correr sus pruebas → si fallan, devolverle la salida y pedir arreglo (máx. 2) → commit.

- Cada app generada lleva su `CLAUDE.md` (de `templates/project_claude.md.j2`). Ahí vive el conocimiento de qué capas tocar; si agregas una capa al generador, actualiza también esa plantilla.
- `llm.CODE_TOOLS` limita lo que puede hacer: leer, editar y correr pytest. Nada de red ni instalaciones. Hay pruebas que lo verifican.
- `changes()` hace `git add -A` antes de mirar el diff, porque `git diff HEAD` ignora los archivos nuevos y el modelo los crea a menudo.
- Aunque las pruebas queden en rojo se commitea, marcado con `[pruebas en rojo]`, para que `--revert` siempre tenga a dónde volver.

## La web para celular

`python serve.py` levanta un servidor con **solo librería estándar** (sin FastAPI) y lo abres desde el celular. Vive en `web/`:
- `web/server.py` — rutas, trabajos en segundo plano con sondeo (las llamadas al modelo tardan hasta un minuto y el navegador del celular no puede esperar con la conexión abierta).
- `web/static/index.html` — una sola página, sin build.

Si agregas un endpoint, recuerda que todo lo lento va como trabajo: `start_job(...)` devuelve un id y el navegador consulta `/api/job/<id>`.

## Estructura

- `cli.py` — punto de entrada y flujo interactivo
- `agent/interview.py` — entrevistador, esquemas JSON, confirmación del plan
- `agent/spec.py` — `AppSpec`: el contrato entre la entrevista y el generador
- `agent/generator.py` — renderiza plantillas, genera datos de ejemplo
- `agent/llm.py` — backends del modelo
- `agent/github_publish.py` — git init/commit, crear repo, push
- `templates/` — Jinja2 con delimitadores `{= var =}` y `{% bloque %}` (así no chocan con JSX/CSS). Un archivo `__entity__.*.j2` se genera una vez por entidad.

Después de tocar cualquier plantilla, verifica con el comando de `--demo` de arriba.
