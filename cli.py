#!/usr/bin/env python3
"""Code Clone — agente constructor de apps en tu terminal.

Uso interactivo (terminal normal):
    python cli.py                    # flujo completo: entrevista -> código -> GitHub
    python cli.py --demo             # genera una app de ejemplo
    python cli.py --publish-only proyectos/mi-app   # solo sube a GitHub

Uso no interactivo (sesión en la nube, CI, o manejado por otro agente):
    python cli.py --spec spec.json --yes
    python cli.py --demo --platform web --yes --no-github
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import questionary  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402

from agent import llm  # noqa: E402
from agent.generator import generate_project  # noqa: E402
from agent.github_publish import PublishError, publish  # noqa: E402
from agent.interview import Interview, confirm_spec_loop, demo_spec  # noqa: E402
from agent.spec import AppSpec  # noqa: E402

console = Console()


BANNER = """[bold magenta]
   ▄▀█ █▀█ █▀█   █▄▄ █░█ █ █░░ █▀▄ █▀▀ █▀█
   █▀█ █▀▀ █▀▀   █▄█ █▄█ █ █▄▄ █▄▀ ██▄ █▀▄[/bold magenta]
   [dim]Describe tu idea. Yo pregunto, diseño, programo y subo a GitHub.[/dim]
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Agente constructor de apps (web y móvil)")
    p.add_argument("--demo", action="store_true", help="Genera una app de ejemplo sin usar IA")
    p.add_argument("--spec", type=Path, help="Usar un spec.json existente (salta la entrevista)")
    p.add_argument("--out", type=Path, default=Path("proyectos"), help="Carpeta de salida (default: ./proyectos)")
    p.add_argument("--no-github", action="store_true", help="No subir a GitHub")
    p.add_argument("--public", action="store_true", help="Crear el repo como público (default: privado)")
    p.add_argument("--publish-only", type=Path, metavar="CARPETA", help="Solo subir a GitHub un proyecto ya generado")
    p.add_argument(
        "-y", "--yes", action="store_true",
        help="No preguntar nada: acepta el plan, reemplaza carpetas y sigue. "
             "Necesario cuando no hay terminal interactiva (nube, CI, otro agente).",
    )
    p.add_argument(
        "--idea", type=str,
        help="La idea de la app, para no tener que escribirla en la entrevista",
    )
    p.add_argument(
        "--platform", choices=["web", "mobile", "both"],
        help="Plataforma, para no tener que elegirla en la entrevista",
    )
    return p.parse_args()


def interactive() -> bool:
    """¿Hay una terminal de verdad para hacer preguntas?"""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def require_interactive(args: argparse.Namespace) -> None:
    """Falla temprano y con un mensaje claro en vez de quedarse colgado."""
    if args.yes or interactive():
        return
    console.print(
        "[red]No hay una terminal interactiva y no pasaste --yes.[/red]\n\n"
        "El agente necesita hacerte preguntas, y aquí no puede. Opciones:\n"
        "  • Córrelo en una terminal normal, o\n"
        "  • pasa [bold]--yes[/bold] con todo lo que necesita decidido:\n"
        "      python cli.py --spec spec.json --yes\n"
        "      python cli.py --demo --platform web --yes"
    )
    sys.exit(2)


def get_spec(args: argparse.Namespace) -> tuple[AppSpec, bool]:
    """Devuelve (spec, use_llm)."""
    auto = args.yes

    if args.spec:
        spec = AppSpec.from_json_file(args.spec)
        if args.platform:
            spec.platform = args.platform  # type: ignore[assignment]
        return confirm_spec_loop(spec, None, auto=auto), llm.available()

    if args.demo:
        platform = args.platform
        if not platform:
            require_interactive(args)
            platform = questionary.select(
                "¿Qué tipo de software quieres para la demo?",
                choices=[
                    questionary.Choice("🌐 Web", "web"),
                    questionary.Choice("📱 Móvil", "mobile"),
                    questionary.Choice("🌐📱 Ambas", "both"),
                ],
            ).ask() or "web"
        return confirm_spec_loop(demo_spec(platform), None, auto=auto), False

    # A partir de aquí hay entrevista, que siempre necesita terminal.
    require_interactive(args)

    if not llm.available():
        console.print(
            "[yellow]No encontré cómo hablar con el modelo.[/yellow] Tienes dos opciones:\n\n"
            "  [bold]1. Claude Code local[/bold] (no necesitas clave de API, usa tu plan):\n"
            "     npm install -g @anthropic-ai/claude-code\n"
            "     claude      → inicia sesión y sal con /exit\n\n"
            "  [bold]2. Clave de API[/bold]: pon ANTHROPIC_API_KEY en tu archivo .env"
        )
        if questionary.confirm("¿Quieres probar con una app de demostración mientras tanto?", default=True).ask():
            return confirm_spec_loop(demo_spec("both"), None), False
        sys.exit(1)

    console.print(f"[dim]Motor: {llm.backend_description()}[/dim]")
    return Interview(idea=args.idea or "", platform=args.platform or "").run(), True


def maybe_publish(project: Path, spec: AppSpec | None, args: argparse.Namespace, ask: bool = True) -> None:
    if args.no_github:
        return
    if ask and not args.yes:
        if not questionary.confirm(
            "¿Subo el proyecto a un repositorio nuevo en GitHub?", default=True
        ).ask():
            return
    name = project.name
    description = spec.tagline if spec else name
    try:
        with console.status("[magenta]Subiendo a GitHub...[/magenta]"):
            url = publish(project, name, description, private=not args.public)
        if url:
            console.print(f"[green]✓ Repositorio listo:[/green] [bold]{url}[/bold]")
    except PublishError as exc:
        console.print(f"[red]No pude subir a GitHub:[/red] {exc}")


def next_steps(project: Path, spec: AppSpec) -> str:
    lines = [f"[bold]cd {project}[/bold]", "", "[bold]Backend[/bold] (terminal 1):",
             "  cd backend && python -m venv .venv && source .venv/bin/activate",
             "  pip install -r requirements.txt && uvicorn app.main:app --reload"]
    if spec.wants_web:
        lines += ["", "[bold]Web[/bold] (terminal 2):", "  cd frontend && npm install && npm run dev",
                  "  → http://localhost:5173"]
    if spec.wants_mobile:
        lines += ["", "[bold]Móvil[/bold] (terminal 3):",
                  "  cd mobile && npm install && npm run setup",
                  "  cp .env.example .env   (pon la IP de tu PC)",
                  "  npx expo start   → escanea el QR con Expo Go"]
    lines += ["", "O todo junto con Docker: [bold]docker compose up --build[/bold]"]
    if spec.needs_auth:
        lines += ["", "Usuario demo: [bold]demo@demo.com[/bold] / [bold]demo1234[/bold]"]
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    console.print(BANNER)

    if args.publish_only:
        project = args.publish_only.resolve()
        if not project.is_dir():
            console.print(f"[red]No existe la carpeta {project}[/red]")
            sys.exit(1)
        spec_file = project / "spec.json"
        spec = AppSpec.from_json_file(spec_file) if spec_file.exists() else None
        maybe_publish(project, spec, args, ask=False)
        return

    try:
        spec, use_llm = get_spec(args)
    except (llm.LLMNotConfigured, llm.LLMError) as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    out_root = args.out.resolve()
    target = out_root / spec.slug
    overwrite = False
    if target.exists():
        if args.yes:
            overwrite = True
            console.print(f"[dim]Reemplazando {target} (--yes)[/dim]")
        else:
            overwrite = questionary.confirm(
                f"Ya existe {target}. ¿La reemplazo?", default=False
            ).ask()
            if not overwrite:
                console.print("Cancelado. Cambia el nombre de la app o usa --out.")
                sys.exit(1)

    project = generate_project(spec, out_root, use_llm=use_llm, overwrite=overwrite)
    console.print(f"\n[green]✓ Proyecto generado en[/green] [bold]{project}[/bold]")

    maybe_publish(project, spec, args)

    console.print(Panel(next_steps(project, spec), title="🚀 Cómo correr tu app", border_style="green"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[dim]Hasta luego 👋[/dim]")
        sys.exit(130)
