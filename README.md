# Code Clone — un agente que construye apps desde tu terminal

Describe tu idea en lenguaje natural. El agente:

1. **Te pregunta si quieres web, móvil o ambas.**
2. **Te entrevista** con preguntas inteligentes (con opciones sugeridas) para mejorar la idea: usuarios, funcionalidades, datos, estilo, integraciones… y te propone mejoras que no habías pedido.
3. **Diseña la arquitectura** (modelos de datos, funcionalidades, color, login) y te la muestra para que la apruebes o pidas cambios en lenguaje natural.
4. **Genera el código completo**, listo para correr:
   - **Backend:** FastAPI + SQLAlchemy (SQLite en local, Postgres con Docker), login con JWT, CRUD por cada entidad y datos de ejemplo realistas.
   - **Web:** React + Vite + TypeScript, responsive, modo oscuro, búsqueda, formularios.
   - **Móvil:** React Native con Expo (iOS y Android), mismas funciones.
   - **Pruebas:** suite de pytest con pruebas funcionales y de seguridad, más CI en GitHub Actions.
5. **Lo sube a un repositorio nuevo en GitHub** (privado por defecto).

Puedes usarlo de tres formas: en tu terminal, desde el celular con la web que corre en tu PC, o desde una sesión de nube en [claude.ai/code](https://claude.ai/code).

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
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

En macOS y en muchas distribuciones de Linux el comando es `python3`, no `python`. Dentro del entorno virtual ya existen `python` y `pip` a secas, así que los comandos de más abajo funcionan tal cual. Activar el entorno también evita el error `externally-managed-environment` que da macOS al instalar paquetes fuera de uno.

### El motor de IA: tu Claude Code local

**No necesitas clave de API.** El agente detecta el comando `claude` en tu PATH y usa la sesión con la que ya iniciaste sesión, contra tu propio plan.

```bash
npm install -g @anthropic-ai/claude-code
claude      # inicia sesión y sal con /exit
```

Llama a `claude -p` por debajo, en una carpeta temporal vacía para no arrastrar el `CLAUDE.md` ni la configuración del proyecto donde estés parado. Cuando tu versión de Claude Code lo soporta, usa `--json-schema` para que las respuestas vengan estructuradas en vez de parsear texto suelto.

Ten en cuenta que estas llamadas consumen tu límite de uso del plan, igual que conversar con Claude Code, y que cada una tarda entre 15 y 40 segundos.

> Queda un backend de API como escape hatch para entornos sin Claude Code (un servidor, por ejemplo). No se usa nunca salvo que lo pidas con `LLM_BACKEND=api` y `ANTHROPIC_API_KEY`, y requiere `pip install anthropic`.

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

## Cambiar el login de una app ya generada

Quitar el login no es una edición de código: toca el modelo, los esquemas, el frontend y las pruebas. Es un cambio de plan, así que se regenera desde el `spec.json` con un flag:

```bash
python cli.py --spec proyectos/mi-app/spec.json --no-auth --yes --no-github
```

Al revés funciona igual con `--auth`. Ojo: esto **regenera el proyecto desde cero**, así que pierdes cualquier cambio manual que le hayas hecho al código. Para cambios que no están en el plan, usa el modo iteración.

## Modo iteración: cambiar una app ya generada

No hay que regenerar nada. Le dices el cambio en lenguaje natural y edita el código existente:

```bash
python cli.py --iterate proyectos/gym-flow "agrega un campo de notas a los planes"
python cli.py --iterate proyectos/gym-flow "ordena las asistencias por fecha, más reciente primero"
python cli.py --history proyectos/gym-flow      # ver las iteraciones
python cli.py --revert proyectos/gym-flow       # deshacer la última
```

Desde el celular es igual: en la web aparecen tus apps y tocas una para cambiarla.

### Cómo funciona

El trabajo de editar archivos lo hace **Claude Code**, que ya tiene herramientas de lectura y edición y su propio bucle de agente. Lo que aporta code-clone es lo que le falta:

1. **Contexto.** Cada app generada lleva su propio `CLAUDE.md` con dónde está cada cosa y qué capas hay que tocar al agregar un campo (modelo, esquema, datos de prueba, metadatos del frontend, y `spec.json` como fuente de verdad).
2. **Las pruebas como red de seguridad.** Tras el cambio se corre la suite del proyecto. Si se rompió, se le devuelve la salida de pytest y se le pide que lo arregle, hasta 2 intentos. Sin este paso el agente *cree* que funcionó; con él hay una señal objetiva.
3. **Un commit por iteración**, así que `--revert` siempre funciona. Si las pruebas quedan en rojo, el commit se marca con `[pruebas en rojo]` en vez de ocultarlo.
4. **Límites.** Claude solo recibe herramientas de lectura, edición y correr pytest. No puede instalar paquetes, tocar la red ni borrar archivos.

Las instrucciones dicen explícitamente que no debilite ni borre pruebas para que pasen: si una de seguridad falla, el bug está en el cambio.

Para que las pruebas corran hace falta tener instaladas las dependencias del proyecto (`cd backend && pip install -r requirements.txt`). Si no están, el cambio se hace igual y se avisa que no se pudo verificar.

## Uso desde el celular (web en tu PC)

```bash
python serve.py
```

Imprime un enlace con la IP de tu PC y una clave. Lo abres en el celular (misma WiFi) y tienes la misma entrevista, con botones en vez de flechas. El trabajo lo hace tu PC, así que **usa tu sesión de Claude Code sin clave de API**, y el proyecto generado queda en tu disco donde sí lo puedes correr.

```bash
python serve.py --port 9000      # otro puerto
python serve.py --out ~/mis-apps # otra carpeta de salida
```

Para entrar desde fuera de tu casa, levanta un túnel:

```bash
cloudflared tunnel --url http://localhost:8777
```

El servidor **no usa dependencias adicionales** — solo la librería estándar de Python. El enlace lleva una clave aleatoria que cambia en cada arranque; sin ella el servidor no responde.

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
| `--no-auth` / `--auth` | Quita o pone el login, sobrescribiendo el plan. |

Variables opcionales en `.env`:
- `ANTHROPIC_MODEL`: alias del modelo (`sonnet`, `opus`, `haiku`).
- `CLAUDE_CLI_TIMEOUT`: segundos de espera por llamada a Claude Code (default 300).
- `GITHUB_TOKEN`: para subir los proyectos generados a repos nuevos.

## Pruebas del propio agente

```bash
pip install pytest
pytest
```

149 pruebas, en `tests/`:

- **`test_spec.py`** — normalización de nombres (tildes, ñ, símbolos, plurales), palabras reservadas de Python, nombres que chocan con los internos, tipos de campo completos, colores inválidos, ida y vuelta por JSON.
- **`test_generator.py`** — que solo se generen las carpetas de la plataforma pedida, que **todo el Python generado compile**, que no queden restos de plantilla Jinja, que los JSON sean válidos, que con y sin login se genere lo correcto, y que los datos de ejemplo respeten cada tipo de campo.
- **`test_iterate.py`** — la máquina de estados del modo iteración con el modelo simulado: el bucle de reparación (que reintente, que le pase la salida de pytest, que se rinda tras el máximo), que el commit quede marcado cuando las pruebas fallan, el revert, y que las herramientas permitidas no incluyan Bash libre ni instalación de paquetes.
- **`test_e2e_generado.py`** — la consistencia de las pruebas E2E generadas. La clave: que **cada `data-testid` que usan exista de verdad** en el frontend, porque un selector mal escrito produce pruebas que fallan siempre o, peor, que no prueban nada. También que los valores de prueba encajen con el tipo de cada campo, y que una app sin login no use selectores de autenticación.
- **`test_web.py`** y **`test_web_iterate.py`** — levantan el servidor de verdad en un puerto libre: flujo completo de la entrevista con el modelo simulado, y **seguridad del servidor**: sin token no responde nada, tokens equivocados, intentos de leer archivos del servidor (`../../etc/passwd`, `/.env`, `/agent/llm.py`), JSON roto, que las credenciales del entorno no se filtren a la página, y que el nombre de proyecto que llega del navegador no pueda apuntar fuera de la carpeta de salida.

Las pruebas nunca llaman al modelo de verdad: el fixture `fake_llm` lo reemplaza, así que son rápidas, gratis y deterministas.

El CI (`.github/workflows/ci.yml`) corre en cada push:

| Trabajo | Qué verifica |
|---|---|
| `pruebas` | Las 149 pruebas, en Python 3.10 y 3.13 |
| `sin_dependencias_de_interfaz` | Que el núcleo funcione instalando solo Jinja2 |
| `iteracion` | Que las apps generadas queden listas para iterar (git limpio, `CLAUDE.md`) |
| `app_generada` | Genera una app, instala sus dependencias y **corre su suite de pytest** |
| `app_generada_sin_login` | Lo mismo para la variante sin autenticación |
| `e2e` | Instala Chromium y **corre las pruebas de Playwright** de verdad, con y sin login |

Los dos últimos grupos son los que importan: no basta con que el agente genere pruebas, tienen que pasar.

## Qué pruebas traen las apps generadas

Cada proyecto incluye su propia suite, que corres con `cd backend && pytest`:

- **`tests/test_api.py`** — el ciclo completo de cada entidad: crear, leer, actualizar, eliminar, orden de la lista, 404 en ids inexistentes, 422 cuando falta un campo obligatorio.
- **`tests/test_security.py`** — endpoints sin token, tokens inválidos, expirados, con algoritmo `none` y firmados con otra clave; **aislamiento entre usuarios** (que A no pueda ver ni modificar lo de B); intento de suplantar al dueño mandando `owner_id`; contraseñas con hash y sal que nunca aparecen en las respuestas; mensajes de login que no revelan si un correo existe; inyección SQL y entradas maliciosas.

Y para la web, pruebas **E2E con Playwright** en `frontend/tests/e2e/`:

- **`humo.spec.ts`** — la app carga, el menú lleva a todas las secciones, y no hay errores de JavaScript al recorrerla.
- **`crud.spec.ts`** — por cada entidad: crear, editar, eliminar (aceptando el `confirm()`), cancelar, buscar y filtrar, y que los datos sobrevivan a recargar.
- **`seguridad.spec.ts`** — que el HTML guardado **no se ejecute** al pintarse, que un usuario **no vea los datos de otro** (con dos usuarios reales en el navegador), que cerrar sesión borre el token, que un token inventado en `localStorage` no dé acceso, y que la contraseña no quede escrita en la página.

```bash
cd frontend
npm install
npx playwright install chromium   # solo la primera vez
npm run test:e2e
```

Playwright levanta el backend y el frontend por sí solo, con una base de datos aparte, así que no hay que arrancar nada a mano. Para verlas correr en vivo: `npm run test:e2e:ui`.

Todas las pruebas están parametrizadas por entidad, así que crecen con la app: una app de 4 entidades genera unos 50 casos de pytest y unos 30 de Playwright. El workflow `.github/workflows/tests.yml` corre las de pytest en cada push y compila el frontend para validar los tipos.

## Estructura

```
cli.py                  Punto de entrada de la terminal
serve.py                Arranca la web para el celular
agent/
  interview.py          Agente entrevistador (preguntas, plan, revisiones)
  llm.py                Backends del modelo (Claude Code local o API) + parseo de JSON
  spec.py               AppSpec: el "contrato" entre la entrevista y el generador
  generator.py          Renderiza plantillas, datos de ejemplo, CI
  iterate.py            Modo iteración: editar, probar, arreglar, commitear
  ui.py                 Consola con rich si está, sin él si no
  github_publish.py     git init/commit + crear repo + push
web/
  server.py             Servidor HTTP (solo librería estándar)
  static/index.html     La interfaz de chat, pensada para celular
templates/
  project_claude.md.j2  El CLAUDE.md que va DENTRO de cada app generada
  web_backend/          FastAPI + pruebas (un router por entidad vía __entity__.py.j2)
  web_frontend/         React + Vite + TS
  mobile/               Expo / React Native
```

La lógica de la entrevista vive en `agent/`, y tanto la terminal como la web la usan: `Interview.next_questions()` pide las preguntas al modelo y cada interfaz decide cómo mostrarlas.

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
