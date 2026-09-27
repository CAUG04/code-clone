"""Pruebas del mensaje cuando faltan dependencias.

Nació de un fallo real: Carlos tenía activo el entorno virtual de una app
generada (que también se llama .venv) en vez del de code-clone, y lo único
que vio fue un traceback de ModuleNotFoundError. El prompt del shell se ve
igual en los dos casos, así que no había ninguna pista.
"""
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _correr(env_extra: dict) -> tuple[int, str]:
    """Corre cli.py con un entorno donde questionary no se puede importar."""
    import os

    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    # Un PYTHONPATH vacío y sin los stubs: questionary no existirá.
    env["PYTHONPATH"] = str(RAIZ)
    env.update(env_extra)
    res = subprocess.run(
        [sys.executable, "cli.py", "--demo"],
        cwd=RAIZ, capture_output=True, text=True, timeout=60, env=env,
    )
    return res.returncode, res.stdout + res.stderr


@pytest.fixture(autouse=True)
def sin_questionary():
    """Estas pruebas solo tienen sentido si questionary NO está instalado."""
    try:
        import questionary  # noqa: F401
        pytest.skip("questionary está instalado; el camino de error no se puede probar")
    except ImportError:
        pass


def test_no_escupe_un_traceback():
    codigo, salida = _correr({})
    assert codigo == 1, "debe salir con error controlado, no con excepción"
    assert "Traceback" not in salida
    assert "ModuleNotFoundError" not in salida


def test_dice_que_paquete_falta():
    _, salida = _correr({})
    assert "questionary" in salida


def test_detecta_el_entorno_equivocado():
    """El caso real: otro .venv activo."""
    _, salida = _correr({"VIRTUAL_ENV": "/Users/x/proyectos/mi-app/backend/.venv"})
    assert "OTRO entorno virtual" in salida
    assert "/Users/x/proyectos/mi-app/backend/.venv" in salida
    assert "se ven igual" in salida or "se ve igual" in salida
    # Y da el comando exacto para arreglarlo
    assert f"source {RAIZ / '.venv' / 'bin' / 'activate'}" in salida


def test_detecta_que_no_hay_entorno_activo():
    _, salida = _correr({})
    assert "ningún entorno virtual activo" in salida
    assert "activate" in salida


def test_si_el_entorno_es_el_correcto_sugiere_instalar():
    _, salida = _correr({"VIRTUAL_ENV": str(RAIZ / ".venv")})
    assert "pip install -r" in salida
    assert "OTRO entorno" not in salida
