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
- `--out CARPETA`: dónde dejar el proyecto (default `proyectos/`).

Sin terminal interactiva y sin `--yes`, el CLI falla con un mensaje claro en vez de colgarse. Eso es a propósito.

## El motor de IA

`agent/llm.py` habla con el modelo de dos formas y elige sola:
1. **`claude -p`** (Claude Code local) si el comando `claude` está en el PATH. No necesita clave de API.
2. **API de Anthropic** si hay `ANTHROPIC_API_KEY`.

Fuerza una con `LLM_BACKEND=cli` o `LLM_BACKEND=api`.

Ojo: si ya estás corriendo dentro de una sesión de Claude Code, el agente llamaría a `claude -p` anidado. Funciona, pero es más lento y gasta doble. Para generar desde aquí es mejor que **tú** escribas el `spec.json` (paso 2) y corras el CLI con `--spec`, que no necesita llamar al modelo salvo para los datos de ejemplo.

## Probar sin gastar nada

```bash
python cli.py --demo --platform both --yes --no-github
```

Genera la app de ejemplo "GymFlow" sin llamar al modelo. Úsalo para verificar que los generadores siguen funcionando después de tocar plantillas.

## Estructura

- `cli.py` — punto de entrada y flujo interactivo
- `agent/interview.py` — entrevistador, esquemas JSON, confirmación del plan
- `agent/spec.py` — `AppSpec`: el contrato entre la entrevista y el generador
- `agent/generator.py` — renderiza plantillas, genera datos de ejemplo
- `agent/llm.py` — backends del modelo
- `agent/github_publish.py` — git init/commit, crear repo, push
- `templates/` — Jinja2 con delimitadores `{= var =}` y `{% bloque %}` (así no chocan con JSX/CSS). Un archivo `__entity__.*.j2` se genera una vez por entidad.

Después de tocar cualquier plantilla, verifica con el comando de `--demo` de arriba.
