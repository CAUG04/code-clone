"""Generador de código.

Toma un AppSpec y produce un proyecto completo en disco:
  <slug>/
    backend/   FastAPI + SQLAlchemy (SQLite en local, Postgres en Docker)
    frontend/  React + Vite + TypeScript        (si es web)
    mobile/    React Native con Expo            (si es móvil)
    docker-compose.yml, README.md, spec.json
"""
from __future__ import annotations

import json
import random
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, StrictUndefined
from . import llm
from .spec import AppSpec, Entity
from .ui import console



TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
ENTITY_MARKER = "__entity__"


def _env() -> Environment:
    # Delimitadores propios para no chocar con las llaves de JSX/CSS.
    return Environment(
        variable_start_string="{=",
        variable_end_string="=}",
        block_start_string="{%",
        block_end_string="%}",
        comment_start_string="{#-",
        comment_end_string="-#}",
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
        autoescape=False,
    )


# ---------------------------------------------------------------------------
# Render de una carpeta de plantillas
# ---------------------------------------------------------------------------

def render_tree(template_dir: Path, out_dir: Path, context: dict) -> list[Path]:
    env = _env()
    spec: AppSpec = context["spec"]
    written: list[Path] = []

    for src in sorted(template_dir.rglob("*.j2")):
        rel = src.relative_to(template_dir)
        template = env.from_string(src.read_text(encoding="utf-8"))
        rel_out = str(rel)[: -len(".j2")]

        if ENTITY_MARKER in rel_out:
            targets = [
                (rel_out.replace(ENTITY_MARKER, _entity_filename(rel_out, e)), {"e": e})
                for e in spec.entities
            ]
        else:
            targets = [(rel_out, {})]

        for target_rel, extra in targets:
            content = template.render(**context, **extra)
            is_init = target_rel.endswith("__init__.py")
            if not content.strip() and not is_init:
                continue  # plantilla condicional que no aplica (ej. auth desactivado)
            dest = out_dir / target_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content.lstrip("\n"), encoding="utf-8")
            written.append(dest)
    return written


def _entity_filename(path: str, e: Entity) -> str:
    # Archivos .tsx usan PascalCase (componentes); el resto snake_case.
    return e.name if path.endswith((".tsx", ".jsx")) else e.snake


# ---------------------------------------------------------------------------
# Metadatos compartidos por frontend web y móvil
# ---------------------------------------------------------------------------

def _entities_meta(spec: AppSpec) -> list[dict]:
    return [
        {
            "name": e.name,
            "plural": e.plural,
            "title": e.title,
            "titlePlural": e.title_plural,
            "description": e.description,
            "displayField": e.display_field,
            "fields": [
                {
                    "name": f.name,
                    "label": f.label,
                    "type": f.type,
                    "required": f.required,
                    "description": f.description,
                }
                for f in e.fields
            ],
        }
        for e in spec.entities
    ]


def _context(spec: AppSpec) -> dict:
    app_meta = {
        "name": spec.app_name,
        "tagline": spec.tagline,
        "description": spec.description,
        "features": spec.features,
        "needsAuth": spec.needs_auth,
        "colorPrimary": spec.color_primary,
    }
    return {
        "spec": spec,
        "entities_json": json.dumps(_entities_meta(spec), indent=2, ensure_ascii=False),
        "app_meta_json": json.dumps(app_meta, indent=2, ensure_ascii=False),
    }


# ---------------------------------------------------------------------------
# Datos de ejemplo
# ---------------------------------------------------------------------------

SEED_SYSTEM = """Generas datos de ejemplo realistas para una app recién creada,
para que al abrirla no se vea vacía. Contenido en español, con nombres,
lugares y precios creíbles para Latinoamérica (Colombia si aplica).
Fechas en formato ISO: date = "YYYY-MM-DD", datetime = "YYYY-MM-DDTHH:MM:SS".
Respeta exactamente los nombres de los campos y sus tipos."""


def generate_seed(spec: AppSpec, use_llm: bool) -> dict:
    if use_llm:
        try:
            schema = {
                e.name: {f.name: f.type for f in e.fields} for e in spec.entities
            }
            data = llm.ask_json(
                SEED_SYSTEM,
                f"App: {spec.app_name} — {spec.description}\n"
                f"Esquema (entidad -> campo: tipo):\n{json.dumps(schema, indent=2)}\n\n"
                'Devuelve {"NombreEntidad": [ {..fila..}, ... ]} con 5 filas por entidad.',
                max_tokens=4000,
            )
            if isinstance(data, dict):
                return data
        except Exception as exc:  # si falla la IA, usamos datos sintéticos
            console.print(f"[yellow]No pude generar datos con IA ({exc}); uso datos sintéticos.[/yellow]")
    return _synthetic_seed(spec)


def _synthetic_seed(spec: AppSpec) -> dict:
    rnd = random.Random(42)
    words = ["Alfa", "Brisa", "Cóndor", "Delta", "Esmeralda", "Faro", "Guadua", "Horizonte"]
    out: dict[str, list[dict]] = {}
    for e in spec.entities:
        rows = []
        for i in range(5):
            row = {}
            for f in e.fields:
                t = f.type
                if t == "boolean":
                    row[f.name] = i % 2 == 0
                elif t == "integer":
                    row[f.name] = rnd.randint(1, 100)
                elif t == "float":
                    row[f.name] = round(rnd.uniform(10_000, 250_000), 2)
                elif t == "date":
                    row[f.name] = (date.today() - timedelta(days=i * 3)).isoformat()
                elif t == "datetime":
                    row[f.name] = (datetime.now() - timedelta(hours=i * 7)).replace(microsecond=0).isoformat()
                elif t == "email":
                    row[f.name] = f"usuario{i + 1}@ejemplo.com"
                elif t == "url":
                    row[f.name] = f"https://ejemplo.com/{e.snake}/{i + 1}"
                elif t == "text":
                    row[f.name] = f"Descripción de ejemplo para {e.name.lower()} {i + 1}."
                else:
                    row[f.name] = f"{f.label} {words[i % len(words)]}"
            rows.append(row)
        out[e.name] = rows
    return out


# ---------------------------------------------------------------------------
# Archivos de raíz del proyecto
# ---------------------------------------------------------------------------

def _gitignore() -> str:
    return "\n".join([
        "# Python", "__pycache__/", "*.pyc", ".venv/", "venv/", "*.db", ".env",
        "", "# Node", "node_modules/", "dist/", ".expo/", "web-build/",
        "", "# SO", ".DS_Store", "",
    ])


def _docker_compose(spec: AppSpec) -> str:
    db = spec.slug.replace("-", "_")
    parts = [
        "services:",
        "  db:",
        "    image: postgres:16-alpine",
        "    environment:",
        "      POSTGRES_USER: postgres",
        "      POSTGRES_PASSWORD: postgres",
        f"      POSTGRES_DB: {db}",
        "    ports: [\"5432:5432\"]",
        "    volumes: [\"pgdata:/var/lib/postgresql/data\"]",
        "    healthcheck:",
        "      test: [\"CMD-SHELL\", \"pg_isready -U postgres\"]",
        "      interval: 3s",
        "      retries: 20",
        "",
        "  backend:",
        "    build: ./backend",
        "    environment:",
        f"      DATABASE_URL: postgresql+psycopg://postgres:postgres@db:5432/{db}",
        "      SECRET_KEY: cambia-esto-en-produccion",
        "    ports: [\"8000:8000\"]",
        "    depends_on:",
        "      db: { condition: service_healthy }",
    ]
    if spec.wants_web:
        parts += [
            "",
            "  frontend:",
            "    build:",
            "      context: ./frontend",
            "      args:",
            "        VITE_API_URL: http://localhost:8000",
            "    ports: [\"5173:80\"]",
            "    depends_on: [backend]",
        ]
    parts += ["", "volumes:", "  pgdata:", ""]
    return "\n".join(parts)


def _ci_workflow(spec: AppSpec) -> str:
    """GitHub Actions: corre las pruebas en cada push."""
    lines = [
        "name: Pruebas",
        "",
        "on:",
        "  push:",
        "    branches: [main]",
        "  pull_request:",
        "",
        "jobs:",
        "  backend:",
        "    name: API y seguridad",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "      - uses: actions/setup-python@v5",
        "        with:",
        "          python-version: '3.11'",
        "          cache: pip",
        "      - name: Instalar dependencias",
        "        working-directory: backend",
        "        run: pip install -r requirements.txt",
        "      - name: Correr pruebas",
        "        working-directory: backend",
        "        run: pytest",
    ]
    if spec.wants_web:
        lines += [
            "",
            "  frontend:",
            "    name: Compilar web",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@v4",
            "      - uses: actions/setup-node@v4",
            "        with:",
            "          node-version: '20'",
            "      - name: Instalar dependencias",
            "        working-directory: frontend",
            "        run: npm install",
            "      - name: Compilar (valida tipos de TypeScript)",
            "        working-directory: frontend",
            "        run: npm run build",
        ]
    return "\n".join(lines) + "\n"


def _readme(spec: AppSpec) -> str:
    platform = {"web": "Web", "mobile": "Móvil", "both": "Web + Móvil"}[spec.platform]
    lines = [
        f"# {spec.app_name}",
        "",
        f"> {spec.tagline}",
        "",
        spec.description,
        "",
        f"**Plataforma:** {platform}  ",
        f"**Para:** {spec.target_users}",
        "",
        "## Funcionalidades",
        "",
        *[f"- {f}" for f in spec.features],
        "",
        "## Modelo de datos",
        "",
    ]
    for e in spec.entities:
        lines.append(f"### {e.name}")
        lines.append(f"{e.description}")
        lines.append("")
        lines.append("| Campo | Tipo | Obligatorio |")
        lines.append("|---|---|---|")
        for f in e.fields:
            lines.append(f"| `{f.name}` | {f.type} | {'sí' if f.required else 'no'} |")
        lines.append("")

    lines += [
        "## Pruebas",
        "",
        "El proyecto viene con pruebas de API y de seguridad, y un workflow de",
        "GitHub Actions que las corre en cada push.",
        "",
        "```bash",
        "cd backend",
        "pip install -r requirements.txt",
        "pytest",
        "```",
        "",
        "Qué cubren:",
        "",
        "- **`tests/test_api.py`** — el ciclo completo de cada entidad: crear, leer,",
        "  actualizar, eliminar, orden de la lista, 404 en ids inexistentes y 422",
        "  cuando falta un campo obligatorio.",
        "- **`tests/test_security.py`** — endpoints sin token, tokens inválidos,",
        "  expirados y firmados con otra clave, **aislamiento entre usuarios**",
        "  (que A no vea ni modifique lo de B), contraseñas con hash y sal que",
        "  nunca aparecen en las respuestas, inyección SQL y entradas maliciosas.",
        "",
        "## Cómo correrlo",
        "",
        "### Opción A — Todo con Docker",
        "",
        "```bash",
        "docker compose up --build",
        "```",
        "",
        "- API y documentación interactiva: http://localhost:8000/docs",
    ]
    if spec.wants_web:
        lines.append("- Web: http://localhost:5173")
    lines += [
        "",
        "### Opción B — Local, sin Docker",
        "",
        "**Backend** (usa SQLite automáticamente):",
        "",
        "```bash",
        "cd backend",
        "python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\\Scripts\\activate",
        "pip install -r requirements.txt",
        "uvicorn app.main:app --reload",
        "```",
        "",
    ]
    if spec.wants_web:
        lines += [
            "**Web:**",
            "",
            "```bash",
            "cd frontend",
            "npm install",
            "npm run dev",
            "```",
            "",
        ]
    if spec.wants_mobile:
        lines += [
            "**Móvil (Expo):**",
            "",
            "```bash",
            "cd mobile",
            "npm install",
            "npm run setup      # instala versiones compatibles con tu SDK de Expo",
            "cp .env.example .env   # pon la IP de tu PC en EXPO_PUBLIC_API_URL",
            "npx expo start",
            "```",
            "",
            "Escanea el QR con la app **Expo Go** en tu celular. El celular y el PC deben estar "
            "en la misma red WiFi, y `EXPO_PUBLIC_API_URL` debe usar la IP local del PC "
            "(ej. `http://192.168.1.20:8000`), no `localhost`.",
            "",
        ]
    if spec.needs_auth:
        lines += [
            "## Usuario de prueba",
            "",
            "Al arrancar por primera vez se crean datos de ejemplo y un usuario demo:",
            "",
            "- **Correo:** demo@demo.com",
            "- **Contraseña:** demo1234",
            "",
        ]
    if spec.integrations:
        lines += [
            "## Próximos pasos sugeridos",
            "",
            *[f"- Integrar: {i}" for i in spec.integrations],
            "",
        ]
    lines += [
        "---",
        "",
        "_Generado con el agente constructor de apps._",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def generate_project(spec: AppSpec, out_root: Path, *, use_llm: bool = True, overwrite: bool = False) -> Path:
    project = out_root / spec.slug
    if project.exists():
        if not overwrite:
            raise FileExistsError(f"Ya existe la carpeta {project}")
        shutil.rmtree(project)
    project.mkdir(parents=True)

    ctx = _context(spec)

    with console.status("[magenta]Escribiendo el backend (FastAPI)...[/magenta]"):
        render_tree(TEMPLATES / "web_backend", project / "backend", ctx)

    with console.status("[magenta]Generando datos de ejemplo...[/magenta]"):
        seed = generate_seed(spec, use_llm)
        (project / "backend" / "app" / "seed.json").write_text(
            json.dumps(seed, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    if spec.wants_web:
        with console.status("[magenta]Escribiendo la web (React)...[/magenta]"):
            render_tree(TEMPLATES / "web_frontend", project / "frontend", ctx)

    if spec.wants_mobile:
        with console.status("[magenta]Escribiendo la app móvil (Expo)...[/magenta]"):
            render_tree(TEMPLATES / "mobile", project / "mobile", ctx)

    (project / ".gitignore").write_text(_gitignore(), encoding="utf-8")
    (project / "docker-compose.yml").write_text(_docker_compose(spec), encoding="utf-8")
    workflows = project / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / "tests.yml").write_text(_ci_workflow(spec), encoding="utf-8")
    (project / "README.md").write_text(_readme(spec), encoding="utf-8")
    (project / "spec.json").write_text(spec.to_json(), encoding="utf-8")

    return project
