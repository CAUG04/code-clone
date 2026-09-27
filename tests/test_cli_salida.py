"""Pruebas de lo que el CLI imprime.

Nacieron de un fallo real: los comandos salían dentro de un recuadro de
rich, y al copiarlos del terminal se venían los bordes '│' pegados. El
shell respondía "command not found: │". Un comando que no se puede copiar
no sirve de nada.
"""
import shutil
from pathlib import Path

import pytest

import cli
from agent.interview import demo_spec

# Caracteres de dibujo de caja que rich usa para los bordes.
BORDES = "│┌┐└┘─├┤┬┴┼╭╮╯╰━┃"


def _salida(capsys, spec, con_docker: bool, ruta="/Users/x/proyectos/mi-app") -> str:
    real = shutil.which
    shutil.which = lambda c: ("/usr/bin/docker" if con_docker else None) if c == "docker" else real(c)
    try:
        cli.print_next_steps(Path(ruta), spec)
    finally:
        shutil.which = real
    return capsys.readouterr().out


@pytest.mark.parametrize("plataforma", ["web", "mobile", "both"])
@pytest.mark.parametrize("con_docker", [True, False])
def test_ningun_comando_lleva_caracteres_de_recuadro(capsys, plataforma, con_docker):
    salida = _salida(capsys, demo_spec(plataforma), con_docker)
    encontrados = [c for c in BORDES if c in salida]
    assert not encontrados, (
        f"la salida tiene caracteres de recuadro {encontrados}: "
        "al copiar el comando se pegan y el shell falla"
    )


@pytest.mark.parametrize("plataforma", ["web", "mobile", "both"])
def test_las_rutas_son_absolutas(capsys, plataforma):
    """Con rutas relativas el usuario no sabe desde dónde correrlas."""
    salida = _salida(capsys, demo_spec(plataforma), con_docker=False)
    for linea in salida.splitlines():
        if linea.strip().startswith("cd "):
            ruta = linea.strip()[3:]
            assert ruta.startswith("/"), f"ruta relativa: {ruta}"


def test_sin_docker_no_se_ofrece_docker(capsys):
    salida = _salida(capsys, demo_spec("web"), con_docker=False)
    assert "docker compose" not in salida
    assert "No encontré Docker" in salida


def test_con_docker_se_ofrece_primero(capsys):
    salida = _salida(capsys, demo_spec("web"), con_docker=True)
    assert "docker compose up --build" in salida
    assert salida.index("docker compose") < salida.index("uvicorn")


@pytest.mark.parametrize("plataforma,espera_web,espera_movil", [
    ("web", True, False), ("mobile", False, True), ("both", True, True),
])
def test_solo_se_muestran_los_pasos_que_aplican(capsys, plataforma, espera_web, espera_movil):
    salida = _salida(capsys, demo_spec(plataforma), con_docker=False)
    assert ("npm run dev" in salida) is espera_web
    assert ("expo start" in salida) is espera_movil
    # Las E2E son de la web
    assert ("test:e2e" in salida) is espera_web


def test_se_explica_como_iterar(capsys):
    salida = _salida(capsys, demo_spec("web"), con_docker=False)
    assert "--iterate" in salida


def test_se_muestra_el_usuario_demo_solo_si_hay_login(capsys):
    con = demo_spec("web")
    assert "demo@demo.com" in _salida(capsys, con, con_docker=False)

    sin = demo_spec("web")
    sin.needs_auth = False
    assert "demo@demo.com" not in _salida(capsys, sin, con_docker=False)
