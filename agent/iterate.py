"""Modo iteración: cambiar una app ya generada, en lenguaje natural.

El bucle es:

    1. Guardar el estado actual en git, para poder revertir.
    2. Pedirle a Claude Code que haga el cambio (él tiene las herramientas
       de lectura y edición; nosotros le damos el contexto y los límites).
    3. Correr las pruebas del proyecto.
    4. Si se rompieron, devolverle la salida y pedirle que lo arregle.
       Hasta `max_repairs` intentos.
    5. Commitear si todo quedó bien.

El paso 3 es la parte que importa: sin él, el agente "cree" que funcionó.
Con él, hay una señal objetiva de si el cambio rompió algo.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import llm
from .ui import console

MAX_REPAIRS = 2
TEST_TIMEOUT = 600

CONTEXTO = """Estás modificando una aplicación ya generada, a pedido de su dueño.

Reglas:
- Haz el cambio más pequeño que cumpla lo pedido. No refactorices de paso.
- `spec.json` en la raíz describe el modelo de datos y es la fuente de
  verdad. Si cambias entidades o campos, actualízalo también, o el proyecto
  queda inconsistente.
- Si agregas o cambias un campo, tócalo en TODAS las capas que apliquen:
  `backend/app/models.py`, `backend/app/schemas.py`, los datos de prueba en
  `backend/tests/conftest.py`, y en el frontend `src/entities.ts` (web) o
  `src/entities.js` (móvil).
- El proyecto tiene pruebas en `backend/tests/`. Si tu cambio afecta el
  comportamiento que prueban, actualiza las pruebas. Nunca las borres ni las
  debilites para que pasen.
- Las pruebas de seguridad (`test_security.py`) no se tocan salvo que el
  cambio sea de seguridad. Si una falla, el bug está en tu cambio.
- Responde al final con un resumen corto, en español, de qué cambiaste y en
  qué archivos. Sin listas largas ni código."""

REPARAR = """Tu cambio anterior rompió las pruebas. Esta es la salida:

```
{salida}
```

Arréglalo. El cambio que pidió el usuario debe quedar hecho: no lo
deshagas para que las pruebas pasen. Si la prueba que falla quedó
desactualizada por el cambio (por ejemplo espera un campo que ya no
existe), actualiza la prueba para que refleje el comportamiento nuevo,
sin debilitar lo que verifica."""


@dataclass
class IterationResult:
    ok: bool
    summary: str = ""
    files_changed: list[str] = field(default_factory=list)
    diff_stat: str = ""
    tests_ran: bool = False
    tests_passed: bool = False
    test_output: str = ""
    repairs: int = 0
    commit: str | None = None
    error: str | None = None
    skipped_tests_reason: str | None = None


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

def _git(project: Path, *args: str, check: bool = True) -> str:
    res = subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, timeout=120
    )
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(res.stderr or res.stdout).strip()}")
    return res.stdout.strip()


def ensure_git(project: Path, mensaje: str = "Estado antes de iterar") -> None:
    """Deja el proyecto en git con todo commiteado, para poder revertir."""
    if shutil.which("git") is None:
        raise RuntimeError("El modo iteración necesita git instalado.")
    if not (project / ".git").exists():
        _git(project, "init", "-b", "main")
        if not _git(project, "config", "user.name", check=False):
            _git(project, "config", "user.name", "code-clone")
            _git(project, "config", "user.email", "code-clone@localhost")
    if _git(project, "status", "--porcelain"):
        _git(project, "add", "-A")
        _git(project, "commit", "-m", mensaje, check=False)


def head(project: Path) -> str:
    return _git(project, "rev-parse", "HEAD", check=False)


def changes(project: Path) -> tuple[str, list[str]]:
    """Qué cambió desde el último commit: (resumen, lista de archivos).

    Primero hace `git add -A`, porque `git diff HEAD` a secas ignora los
    archivos nuevos sin seguimiento, y el modelo crea archivos a menudo.
    """
    _git(project, "add", "-A", check=False)
    stat = _git(project, "diff", "--cached", "--stat", "HEAD", check=False)
    nombres = [
        f for f in _git(project, "diff", "--cached", "--name-only", "HEAD", check=False).splitlines()
        if f
    ]
    return stat, nombres


def revert_last(project: Path) -> str:
    """Deshace la última iteración. Devuelve el mensaje del commit deshecho."""
    ensure_git(project)
    mensaje = _git(project, "log", "-1", "--pretty=%s", check=False)
    total = _git(project, "rev-list", "--count", "HEAD", check=False)
    if total.isdigit() and int(total) <= 1:
        raise RuntimeError("No hay una iteración anterior a la que volver.")
    _git(project, "reset", "--hard", "HEAD~1")
    return mensaje


def history(project: Path, limit: int = 10) -> list[dict]:
    if not (project / ".git").exists():
        return []
    salida = _git(project, "log", f"-{limit}", "--pretty=%h%x1f%s%x1f%ar", check=False)
    entradas = []
    for linea in salida.splitlines():
        partes = linea.split("\x1f")
        if len(partes) == 3:
            entradas.append({"sha": partes[0], "mensaje": partes[1], "cuando": partes[2]})
    return entradas


# ---------------------------------------------------------------------------
# pruebas
# ---------------------------------------------------------------------------

def _python_for(project: Path) -> list[str] | None:
    """Intérprete a usar: el venv del backend si existe, si no el del sistema."""
    for candidato in (
        project / "backend" / ".venv" / "bin" / "python",
        project / "backend" / ".venv" / "Scripts" / "python.exe",
        project / ".venv" / "bin" / "python",
    ):
        if candidato.exists():
            return [str(candidato)]
    import sys

    return [sys.executable]


def tests_available(project: Path) -> tuple[bool, str]:
    """¿Se pueden correr las pruebas del proyecto ahora mismo?"""
    backend = project / "backend"
    if not (backend / "tests").is_dir():
        return False, "el proyecto no tiene carpeta backend/tests"
    py = _python_for(project)
    if py is None:
        return False, "no encontré un intérprete de Python"
    res = subprocess.run(
        [*py, "-m", "pytest", "--version"],
        cwd=backend, capture_output=True, text=True, timeout=120,
    )
    if res.returncode != 0:
        return False, (
            "las dependencias del proyecto no están instaladas "
            "(cd backend && pip install -r requirements.txt)"
        )
    return True, ""


def run_tests(project: Path) -> tuple[bool, str]:
    backend = project / "backend"
    py = _python_for(project)
    res = subprocess.run(
        [*py, "-m", "pytest", "-q", "--tb=short"],
        cwd=backend, capture_output=True, text=True, timeout=TEST_TIMEOUT,
    )
    salida = (res.stdout or "") + (res.stderr or "")
    return res.returncode == 0, salida.strip()


def _recortar(salida: str, limite: int = 6000) -> str:
    """Las fallas útiles están al final de la salida de pytest."""
    if len(salida) <= limite:
        return salida
    return "[...salida recortada...]\n" + salida[-limite:]


# ---------------------------------------------------------------------------
# iteración
# ---------------------------------------------------------------------------

def iterate(
    project: Path,
    instruction: str,
    *,
    note=lambda m: None,
    max_repairs: int = MAX_REPAIRS,
    with_tests: bool = True,
) -> IterationResult:
    project = Path(project).resolve()
    if not project.is_dir():
        return IterationResult(ok=False, error=f"No existe la carpeta {project}")
    if not (project / "spec.json").exists():
        return IterationResult(
            ok=False,
            error=f"{project.name} no parece un proyecto generado por code-clone "
                  "(no tiene spec.json).",
        )

    try:
        ensure_git(project)
    except RuntimeError as exc:
        return IterationResult(ok=False, error=str(exc))

    antes = head(project)
    resultado = IterationResult(ok=False)

    # 1. El cambio
    note("Leyendo el proyecto y haciendo el cambio...")
    try:
        resultado.summary = llm.code_agent(
            f"El dueño de esta app pide el siguiente cambio:\n\n{instruction}",
            project,
            append_system=CONTEXTO,
        )
    except (llm.LLMError, llm.LLMNotConfigured) as exc:
        resultado.error = str(exc)
        return resultado

    resultado.diff_stat, resultado.files_changed = changes(project)
    if not resultado.files_changed:
        resultado.error = (
            "El modelo no cambió ningún archivo. Prueba a ser más específico "
            "sobre qué quieres que cambie."
        )
        return resultado

    # 2. Las pruebas, y el bucle de reparación
    if with_tests:
        disponibles, motivo = tests_available(project)
        if not disponibles:
            resultado.skipped_tests_reason = motivo
            note(f"No pude correr las pruebas: {motivo}")
        else:
            for intento in range(max_repairs + 1):
                note("Corriendo las pruebas..." if intento == 0
                     else f"Las pruebas fallaron; intento de arreglo {intento} de {max_repairs}...")
                resultado.tests_ran = True
                paso, salida = run_tests(project)
                resultado.tests_passed = paso
                resultado.test_output = salida
                if paso or intento == max_repairs:
                    break
                resultado.repairs = intento + 1
                try:
                    resultado.summary += "\n\n" + llm.code_agent(
                        REPARAR.format(salida=_recortar(salida)),
                        project,
                        append_system=CONTEXTO,
                    )
                except (llm.LLMError, llm.LLMNotConfigured) as exc:
                    resultado.error = f"Falló el intento de arreglo: {exc}"
                    break
            resultado.diff_stat, resultado.files_changed = changes(project)

    # 3. Commit
    fallaron = resultado.tests_ran and not resultado.tests_passed
    etiqueta = "iterar: " + instruction.strip().splitlines()[0][:70]
    if fallaron:
        etiqueta = "[pruebas en rojo] " + etiqueta
    _git(project, "add", "-A")
    _git(project, "commit", "-m", etiqueta, check=False)
    despues = head(project)
    resultado.commit = despues if despues != antes else None

    resultado.ok = not fallaron and resultado.error is None
    return resultado


def describe(resultado: IterationResult) -> str:
    """Resumen legible, para la terminal o la web."""
    if resultado.error and not resultado.files_changed:
        return f"No se pudo: {resultado.error}"

    lineas = [resultado.summary.strip()] if resultado.summary.strip() else []

    if resultado.files_changed:
        lineas.append("")
        lineas.append(f"Archivos tocados ({len(resultado.files_changed)}):")
        lineas += [f"  {f}" for f in resultado.files_changed[:15]]
        if len(resultado.files_changed) > 15:
            lineas.append(f"  ...y {len(resultado.files_changed) - 15} más")

    lineas.append("")
    if not resultado.tests_ran:
        motivo = resultado.skipped_tests_reason or "no se pidieron"
        lineas.append(f"Pruebas: no se corrieron ({motivo})")
    elif resultado.tests_passed:
        extra = f" tras {resultado.repairs} arreglo(s)" if resultado.repairs else ""
        lineas.append(f"Pruebas: pasan{extra}")
    else:
        lineas.append("Pruebas: FALLAN. El cambio quedó commiteado pero marcado en rojo.")
        lineas.append("Puedes revertirlo con --revert.")

    if resultado.commit:
        lineas.append(f"Commit: {resultado.commit[:8]}")
    return "\n".join(lineas)
