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
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import questionary  # noqa: E402
from agent import llm  # noqa: E402
from agent.ui import console  # noqa: E402
from agent.generator import generate_project  # noqa: E402
from agent.github_publish import PublishError, publish  # noqa: E402
from agent.interview import Interview, confirm_spec_loop, demo_spec  # noqa: E402
from agent.spec import AppSpec  # noqa: E402


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
    auth = p.add_mutually_exclusive_group()
    auth.add_argument(
        "--no-auth", action="store_true",
        help="Generar la app SIN login (sobrescribe lo que diga el spec)",
    )
    auth.add_argument(
        "--auth", action="store_true",
        help="Generar la app CON login (sobrescribe lo que diga el spec)",
    )

    g = p.add_argument_group("modo iteración (cambiar una app ya generada)")
    g.add_argument(
        "--iterate", type=Path, metavar="CARPETA",
        help='Proyecto a modificar. Ej: --iterate proyectos/gym-flow "agrega paginación"',
    )
    g.add_argument("instruction", nargs="?", help="Qué cambiar, en lenguaje natural")
    g.add_argument("--revert", type=Path, metavar="CARPETA", help="Deshacer la última iteración")
    g.add_argument("--history", type=Path, metavar="CARPETA", help="Ver las iteraciones de un proyecto")
    g.add_argument("--skip-tests", action="store_true", help="No correr las pruebas tras el cambio")
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


def aplicar_overrides(spec: AppSpec, args: argparse.Namespace) -> None:
    """Aplica los flags que sobrescriben el plan."""
    if args.platform:
        spec.platform = args.platform  # type: ignore[assignment]
    if args.no_auth and spec.needs_auth:
        spec.needs_auth = False
        console.print("[dim]Quitando el login (--no-auth)[/dim]")
    elif args.auth and not spec.needs_auth:
        spec.needs_auth = True
        console.print("[dim]Agregando login (--auth)[/dim]")


def get_spec(args: argparse.Namespace) -> tuple[AppSpec, bool]:
    """Devuelve (spec, use_llm)."""
    auto = args.yes

    if args.spec:
        spec = AppSpec.from_json_file(args.spec)
        aplicar_overrides(spec, args)
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
        demo = demo_spec(platform)
        aplicar_overrides(demo, args)
        return confirm_spec_loop(demo, None, auto=auto), False

    # A partir de aquí hay entrevista, que siempre necesita terminal.
    require_interactive(args)

    if not llm.available():
        console.print(
            "[yellow]No encontré el comando `claude`.[/yellow] El agente usa tu Claude Code "
            "local, sin clave de API:\n\n"
            "     npm install -g @anthropic-ai/claude-code\n"
            "     claude      → inicia sesión y sal con /exit"
        )
        if questionary.confirm("¿Quieres probar con una app de demostración mientras tanto?", default=True).ask():
            return confirm_spec_loop(demo_spec("both"), None), False
        sys.exit(1)

    console.print(f"[dim]Motor: {llm.backend_description()}[/dim]")
    spec = Interview(idea=args.idea or "", platform=args.platform or "").run()
    aplicar_overrides(spec, args)
    return spec, True


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


def print_next_steps(project: Path, spec: AppSpec) -> None:
    """Imprime los comandos SIN recuadro.

    Un Panel de rich dibuja bordes con '│', y al copiar del terminal esos
    caracteres se pegan al comando: el shell responde "command not found: │".
    Los comandos van en líneas limpias, listas para copiar.
    """
    tiene_docker = shutil.which("docker") is not None

    console.print()
    console.print("[bold green]🚀 Cómo correr tu app[/bold green]")
    console.print(f"[dim]El README dentro del proyecto tiene el detalle completo.[/dim]\n")

    if tiene_docker:
        console.print("[bold]Todo junto con Docker:[/bold]")
        console.print(f"  cd {project}")
        console.print("  docker compose up --build")
        console.print()
        console.print("[bold]O por partes:[/bold]")
    else:
        # Sin Docker instalado no tiene sentido ofrecerlo como primera opción.
        console.print("[bold]Backend[/bold] (esta terminal):")

    if tiene_docker:
        console.print("\n[bold]Backend[/bold] (terminal 1):")
    console.print(f"  cd {project / 'backend'}")
    console.print("  pip install -r requirements.txt")
    console.print("  uvicorn app.main:app --reload")
    console.print("  [dim]→ http://localhost:8000/docs[/dim]")

    if spec.wants_web:
        console.print("\n[bold]Web[/bold] (pestaña nueva, cmd+T):")
        console.print(f"  cd {project / 'frontend'}")
        console.print("  npm install")
        console.print("  npm run dev")
        console.print("  [dim]→ http://localhost:5173[/dim]")

    if spec.wants_mobile:
        console.print("\n[bold]Móvil[/bold] (otra pestaña):")
        console.print(f"  cd {project / 'mobile'}")
        console.print("  npm install")
        console.print("  npm run setup")
        console.print("  cp .env.example .env    [dim]# pon la IP de tu PC[/dim]")
        console.print("  npx expo start")

    if spec.wants_web:
        console.print("\n[bold]Pruebas E2E[/bold] (opcional):")
        console.print(f"  cd {project / 'frontend'}")
        console.print("  npx playwright install chromium")
        console.print("  npm run test:e2e")

    console.print("\n[bold]Cambiar algo después:[/bold]")
    console.print(f'  python cli.py --iterate {project} "lo que quieras cambiar"')

    if spec.needs_auth:
        console.print("\n[dim]Usuario de prueba: demo@demo.com / demo1234[/dim]")
    if not tiene_docker:
        console.print("\n[dim]No encontré Docker; no lo necesitas para correrla así.[/dim]")


def main() -> None:
    args = parse_args()
    console.print(BANNER)

    if args.revert:
        do_revert(args)
        return

    if args.history:
        do_history(args)
        return

    if args.iterate:
        do_iterate(args)
        return

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

    print_next_steps(project, spec)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[dim]Hasta luego 👋[/dim]")
        sys.exit(130)
