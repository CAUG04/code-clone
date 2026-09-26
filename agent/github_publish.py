"""Sube el proyecto generado a un repositorio nuevo en GitHub.

Estrategia:
  1. Si hay GITHUB_TOKEN en el entorno -> usa la API (PyGithub) y git.
  2. Si no, pero está instalado el CLI `gh` con sesión iniciada -> lo usa.
  3. Si no hay ninguno, deja el repo git local listo y explica cómo subirlo.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .ui import console


class PublishError(RuntimeError):
    pass


def publishing_method() -> str | None:
    """Cómo se puede subir a GitHub ahora mismo, o None si no se puede.

    Devuelve "token" (GITHUB_TOKEN en el entorno) o "gh" (GitHub CLI con
    sesión iniciada). La web lo usa para no ofrecer algo que va a fallar.
    """
    if os.environ.get("GITHUB_TOKEN", "").strip():
        return "token"
    if shutil.which("gh"):
        try:
            res = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                return "gh"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def publishing_description() -> str:
    method = publishing_method()
    if method == "token":
        user = os.environ.get("GITHUB_USERNAME", "").strip()
        return f"GITHUB_TOKEN{f' ({user})' if user else ''}"
    if method == "gh":
        return "GitHub CLI (gh)"
    return "sin configurar"


def _run(cmd: list[str], cwd: Path, secret: str | None = None) -> str:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        msg = (res.stderr or res.stdout).strip()
        if secret:
            msg = msg.replace(secret, "***")
        shown = " ".join(cmd)
        if secret:
            shown = shown.replace(secret, "***")
        raise PublishError(f"Falló `{shown}`:\n{msg}")
    return res.stdout.strip()


def init_local_repo(project: Path, message: str) -> None:
    if shutil.which("git") is None:
        raise PublishError("No encontré `git` instalado.")
    if not (project / ".git").exists():
        _run(["git", "init", "-b", "main"], project)
    _run(["git", "add", "-A"], project)

    # Si el usuario no tiene identidad de git configurada, ponemos una local.
    name = subprocess.run(["git", "config", "user.name"], cwd=project, capture_output=True, text=True).stdout.strip()
    if not name:
        user = os.environ.get("GITHUB_USERNAME", "app-builder")
        _run(["git", "config", "user.name", user], project)
        _run(["git", "config", "user.email", f"{user}@users.noreply.github.com"], project)

    status = _run(["git", "status", "--porcelain"], project)
    if status:
        _run(["git", "commit", "-m", message], project)


def publish(project: Path, repo_name: str, description: str, private: bool = True) -> str | None:
    """Crea el repo y hace push. Devuelve la URL del repo, o None si no se pudo subir."""
    init_local_repo(project, f"Proyecto inicial: {description}"[:200])

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return _publish_with_token(project, repo_name, description, private, token)
    if shutil.which("gh"):
        auth = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
        if auth.returncode == 0:
            return _publish_with_gh(project, repo_name, description, private)

    console.print(
        "[yellow]No encontré GITHUB_TOKEN ni el CLI `gh` con sesión iniciada.[/yellow]\n"
        f"El repositorio git local está listo en [bold]{project}[/bold]. Para subirlo:\n"
        "  • Agrega GITHUB_TOKEN a tu .env y vuelve a correr con --publish-only, o\n"
        "  • Instala GitHub CLI y ejecuta: gh auth login && "
        f"gh repo create {repo_name} --private --source \"{project}\" --push"
    )
    return None


def _publish_with_token(project: Path, repo_name: str, description: str, private: bool, token: str) -> str:
    try:
        from github import Auth, Github, GithubException
    except ImportError as exc:
        raise PublishError("Falta PyGithub: pip install PyGithub") from exc

    gh = Github(auth=Auth.Token(token))
    user = gh.get_user()
    login = user.login

    # Busca un nombre libre: mi-app, mi-app-2, mi-app-3...
    name = repo_name
    for i in range(2, 50):
        try:
            user.get_repo(name)
            name = f"{repo_name}-{i}"
        except GithubException as e:
            if e.status == 404:
                break
            raise PublishError(f"Error consultando GitHub: {e.data}") from e

    try:
        repo = user.create_repo(name, description=description[:350], private=private, auto_init=False)
    except GithubException as e:
        raise PublishError(f"GitHub no permitió crear el repo: {e.data}") from e

    push_url = f"https://x-access-token:{token}@github.com/{login}/{name}.git"
    _run(["git", "push", push_url, "main"], project, secret=token)

    # Deja el remoto sin el token guardado en .git/config.
    clean_url = f"https://github.com/{login}/{name}.git"
    remotes = _run(["git", "remote"], project).split()
    if "origin" in remotes:
        _run(["git", "remote", "set-url", "origin", clean_url], project)
    else:
        _run(["git", "remote", "add", "origin", clean_url], project)
    return repo.html_url


def _publish_with_gh(project: Path, repo_name: str, description: str, private: bool) -> str:
    visibility = "--private" if private else "--public"
    _run(
        ["gh", "repo", "create", repo_name, visibility, "--source", ".", "--push",
         "--description", description[:350]],
        project,
    )
    return _run(["gh", "repo", "view", "--json", "url", "-q", ".url"], project)
