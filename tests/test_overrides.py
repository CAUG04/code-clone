"""Pruebas de los flags que sobrescriben el plan.

Quitar o poner el login toca el modelo, los esquemas, el frontend y las
pruebas. Es un cambio de spec, no de código: por eso se hace regenerando
con un flag y no editando a mano.
"""
import argparse

import pytest

import cli
from agent.generator import generate_project
from agent.interview import demo_spec


def _args(**kwargs) -> argparse.Namespace:
    base = {"platform": None, "no_auth": False, "auth": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


class TestOverrides:
    def test_no_auth_quita_el_login(self):
        spec = demo_spec("web")
        assert spec.needs_auth
        cli.aplicar_overrides(spec, _args(no_auth=True))
        assert not spec.needs_auth

    def test_auth_agrega_el_login(self):
        spec = demo_spec("web")
        spec.needs_auth = False
        cli.aplicar_overrides(spec, _args(auth=True))
        assert spec.needs_auth

    def test_sin_flags_no_cambia_nada(self):
        spec = demo_spec("web")
        antes = spec.needs_auth
        cli.aplicar_overrides(spec, _args())
        assert spec.needs_auth == antes

    def test_platform_se_sobrescribe(self):
        spec = demo_spec("web")
        cli.aplicar_overrides(spec, _args(platform="both"))
        assert spec.platform == "both"
        assert spec.wants_web and spec.wants_mobile

    def test_no_auth_sobre_una_app_que_ya_no_tiene_login_no_hace_nada(self):
        spec = demo_spec("web")
        spec.needs_auth = False
        cli.aplicar_overrides(spec, _args(no_auth=True))
        assert not spec.needs_auth


class TestRegenerarSinLogin:
    """El caso real: una app generada con login que hay que dejar sin login."""

    def test_el_login_desaparece_de_todas_las_capas(self, tmp_path):
        spec = demo_spec("web")
        proyecto = generate_project(spec, tmp_path, use_llm=False, overwrite=True)
        assert (proyecto / "backend/app/auth.py").is_file()

        # Regenerar el mismo spec con el flag
        spec2 = demo_spec("web")
        cli.aplicar_overrides(spec2, _args(no_auth=True))
        proyecto = generate_project(spec2, tmp_path, use_llm=False, overwrite=True)

        assert not (proyecto / "backend/app/auth.py").exists()
        assert not (proyecto / "frontend/src/pages/Login.tsx").exists()

        modelos = (proyecto / "backend/app/models.py").read_text()
        assert "class User" not in modelos
        assert "owner_id" not in modelos
        assert "ForeignKey" not in modelos

        main = (proyecto / "backend/app/main.py").read_text()
        assert "auth_router" not in main
        assert "DEMO_EMAIL" not in main

        esquemas = (proyecto / "backend/app/schemas.py").read_text()
        for clase in ["UserCreate", "UserLogin", "Token"]:
            assert clase not in esquemas

    def test_las_pruebas_tambien_se_adaptan(self, tmp_path):
        spec = demo_spec("web")
        cli.aplicar_overrides(spec, _args(no_auth=True))
        proyecto = generate_project(spec, tmp_path, use_llm=False, overwrite=True)

        seguridad = (proyecto / "backend/tests/test_security.py").read_text()
        assert "no ve los datos de otro" not in seguridad
        assert "inyeccion" in seguridad.lower() or "inyección" in seguridad.lower()

        conftest = (proyecto / "backend/tests/conftest.py").read_text()
        assert "auth_a" not in conftest

        e2e = (proyecto / "frontend/tests/e2e/seguridad.spec.ts").read_text()
        assert "login-page" not in e2e

    def test_el_spec_guardado_refleja_el_cambio(self, tmp_path):
        import json

        spec = demo_spec("web")
        cli.aplicar_overrides(spec, _args(no_auth=True))
        proyecto = generate_project(spec, tmp_path, use_llm=False, overwrite=True)
        assert json.loads((proyecto / "spec.json").read_text())["needs_auth"] is False

    def test_el_readme_no_menciona_el_usuario_demo(self, tmp_path):
        spec = demo_spec("web")
        cli.aplicar_overrides(spec, _args(no_auth=True))
        proyecto = generate_project(spec, tmp_path, use_llm=False, overwrite=True)
        assert "demo@demo.com" not in (proyecto / "README.md").read_text()
