# Code Clone — un agente que construye apps desde tu terminal

Describe tu idea en lenguaje natural. El agente:

1. **Te pregunta si quieres web, móvil o ambas.**
2. **Te entrevista** con preguntas inteligentes (con opciones sugeridas) para mejorar la idea: usuarios, funcionalidades, datos, estilo, integraciones… y te propone mejoras que no habías pedido.
3. **Diseña la arquitectura** (modelos de datos, funcionalidades, color, login) y te la muestra para que la apruebes o pidas cambios en lenguaje natural.
4. **Genera el código completo**, listo para correr:
   - **Backend:** FastAPI + SQLAlchemy (SQLite en local, Postgres con Docker), login con JWT, CRUD por cada entidad y datos de ejemplo realistas.
   - **Web:** React + Vite + TypeScript, responsive, modo oscuro, búsqueda, formularios.
   - **Móvil:** React Native con Expo (iOS y Android), mismas funciones.
5. **Lo sube a un repositorio nuevo en GitHub** (privado por defecto).

```
┌───────────┐   ┌──────────────┐   ┌──────────────┐   ┌─────────────┐   ┌────────┐
│ Tu idea   │──▶│ Web / Móvil  │──▶│ Entrevista   │──▶│ Plan (spec) │──▶│ Código │──▶ GitHub
└───────────┘   └──────────────┘   │ con IA (1-4  │   │ aprobar o   │   └────────┘
                                   │ rondas)      │   │ editar      │
                                   └──────────────┘   └─────────────┘
```

## Instalación

Requiere Python 3.10+ y git.

```bash
cd code-clone
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

### El motor de IA: dos opciones

**Opción 1 — Claude Code local (sin clave de API).** Si ya tienes Claude Code instalado y con sesión iniciada, no configuras nada: el agente detecta el comando `claude` y lo usa con tu plan.

```bash
npm install -g @anthropic-ai/claude-code
claude      # inicia sesión y sal con /exit
```

El agente llama a `claude -p` por debajo, en una carpeta temporal vacía para no arrastrar el `CLAUDE.md` ni la configuración del proyecto donde estés parado. Cuando tu versión de Claude Code lo soporta, usa `--json-schema` para que las respuestas vengan estructuradas en vez de parsear texto.

**Opción 2 — Clave de API.** Pon `ANTHROPIC_API_KEY` en el `.env`. Se paga por uso, aparte de tu suscripción.

Si tienes las dos, gana Claude Code. Puedes forzar una con `LLM_BACKEND=cli` o `LLM_BACKEND=api` en el `.env`.

### GitHub (opcional)

Para subir el proyecto generado necesitas **una** de estas dos:
- `GITHUB_TOKEN` en el `.env` (token con permiso `repo`: https://github.com/settings/tokens), o
- GitHub CLI con sesión iniciada: `gh auth login`.

## Uso en tu terminal (interactivo)

```bash
python cli.py                              # flujo completo
python cli.py --demo                       # genera "GymFlow" de ejemplo, sin llamar al modelo
python cli.py --spec proyectos/x/spec.json # regenera desde un plan guardado
python cli.py --no-github                  # solo generar, sin subir
python cli.py --public                     # repo público
python cli.py --publish-only proyectos/x   # subir un proyecto ya generado
python cli.py --out ~/mis-apps             # otra carpeta de salida
```

Los proyectos se crean en `./proyectos/<nombre-de-la-app>/`, cada uno con su propio README explicando cómo correrlo.

## Uso sin terminal (nube, CI, u otro agente)

La entrevista necesita una terminal de verdad: `questionary` usa flechas y Enter. Donde no hay terminal, pasa `--yes` y todo lo que haya que decidir:

```bash
python cli.py --spec spec.json --yes                    # genera y sube
python cli.py --spec spec.json --yes --no-github        # solo genera
python cli.py --demo --platform web --yes --no-github   # prueba rápida
```

Con `--yes` no pregunta nada: acepta el plan, reemplaza la carpeta si ya existe y sigue. Sin terminal y sin `--yes`, el CLI **falla con un mensaje claro en vez de colgarse** (código de salida 2).

### Correrlo en una sesión de nube de Claude Code

1. Sube este repo a GitHub.
2. Abre [claude.ai/code](https://claude.ai/code) (o la pestaña **Code** de la app de Claude, o `claude --cloud` desde tu terminal) y elige el repo.
3. Pídele a Claude que te entreviste para una app nueva.

El `CLAUDE.md` de este repo le indica a Claude que haga la entrevista **en la conversación** (donde sí puede preguntar), escriba el `spec.json` y luego corra `python cli.py --spec spec.json --yes`. Así la experiencia es la misma que en la terminal, solo que las preguntas llegan por chat y puedes contestarlas desde el celular.

Dos detalles de las sesiones de nube:
- El entorno tiene una **lista blanca de red**. Si `pip install -r requirements.txt` falla, agrega los dominios en la configuración del entorno o ponlo en un *setup script*.
- Ahí el motor no necesita ser `claude -p`: Claude ya es el modelo. Por eso el `CLAUDE.md` le dice que escriba él el `spec.json` y use `--spec`, en vez de llamar a `claude -p` anidado (funciona, pero es más lento y gasta doble).

### Flags del modo no interactivo

| Flag | Qué hace |
|---|---|
| `-y`, `--yes` | No pregunta nada. Obligatorio sin terminal. |
| `--spec ARCHIVO` | Usa ese plan y salta la entrevista. |
| `--platform web\|mobile\|both` | Fuerza la plataforma. |
| `--idea "texto"` | Pasa la idea sin preguntarla. |
| `--no-github` | No sube nada. |

Variables opcionales en `.env`:
- `LLM_BACKEND`: `cli` (Claude Code) o `api` (clave de API). Por defecto elige solo.
- `ANTHROPIC_MODEL`: con `cli` acepta alias (`sonnet`, `opus`); con `api` el nombre completo.
- `CLAUDE_CLI_TIMEOUT`: segundos de espera por llamada a Claude Code (default 300).

## Estructura

```
cli.py                  Punto de entrada y flujo interactivo
agent/
  interview.py          Agente entrevistador (preguntas, plan, revisiones)
  llm.py                Backends del modelo (Claude Code local o API) + parseo de JSON
  spec.py               AppSpec: el "contrato" entre la entrevista y el generador
  generator.py          Renderiza plantillas y genera datos de ejemplo
  github_publish.py     git init/commit + crear repo + push
templates/
  web_backend/          FastAPI (un router por entidad vía __entity__.py.j2)
  web_frontend/         React + Vite + TS
  mobile/               Expo / React Native
```

Las plantillas usan Jinja2 con delimitadores `{= variable =}` y `{% bloque %}` para no chocar con las llaves de JSX/CSS. Un archivo llamado `__entity__.*.j2` se genera una vez por cada entidad.

## Cómo extenderlo

- **Cambiar el estilo de las preguntas:** edita `INTERVIEWER_SYSTEM` en `agent/interview.py`.
- **Más rondas o preguntas:** `MAX_ROUNDS` y `QUESTIONS_PER_ROUND`.
- **Nuevos tipos de campo:** agrega una fila a `FIELD_TYPES` en `agent/spec.py`.
- **Otro stack:** crea una carpeta nueva en `templates/` y llámala desde `generate_project`.

## Ideas para la siguiente versión

- **Modo iteración**: después de generar, le pides "agrega un filtro por fecha" y el agente edita el código existente con herramientas de lectura/escritura de archivos.
- **Agente de pruebas**: generar tests de API con pytest y E2E con Playwright, correrlos y auto-corregir errores.
- **Interfaz web** con chat y vista previa en vivo (el mismo núcleo `agent/` sirve; solo cambia la capa de entrada/salida).
- **Deploy automático** a Railway / Render / Fly.io.
- **Integraciones** listas: pagos (Wompi, Stripe), correo, WhatsApp, IA.
