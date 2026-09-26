"""Fixtures de las pruebas del agente."""
import json
from pathlib import Path

import pytest

from agent.interview import demo_spec
from agent.spec import AppSpec


@pytest.fixture
def out_dir(tmp_path) -> Path:
    return tmp_path / "salida"


@pytest.fixture
def spec_web() -> AppSpec:
    return demo_spec("web")


@pytest.fixture
def spec_both() -> AppSpec:
    return demo_spec("both")


@pytest.fixture
def spec_sin_login() -> AppSpec:
    s = demo_spec("mobile")
    s.needs_auth = False
    return s


@pytest.fixture
def fake_llm(monkeypatch):
    """Reemplaza las llamadas al modelo por respuestas fijas.

    Las pruebas nunca deben llamar al modelo de verdad: serían lentas,
    costarían dinero y darían resultados distintos en cada corrida.
    """
    llamadas = []

    def _ask_json(system, user, *, schema=None, **kwargs):
        llamadas.append({"system": system, "user": user, "schema": schema})
        if "Genera las siguientes preguntas" in user:
            return {
                "ready": False,
                "thinking": "Quiero entender a los usuarios",
                "questions": [
                    {
                        "question": "¿Quién va a usarla?",
                        "why": "define los permisos",
                        "options": ["Solo yo", "Mi equipo"],
                        "multi": False,
                    }
                ],
            }
        if "cambios" in user.lower():
            return _SPEC_REVISADO
        return _SPEC_BASE

    def _ask(system, user, **kwargs):
        llamadas.append({"system": system, "user": user})
        return "texto de prueba"

    monkeypatch.setattr("agent.llm.ask_json", _ask_json)
    monkeypatch.setattr("agent.llm.ask", _ask)
    monkeypatch.setattr("agent.llm.available", lambda: True)
    monkeypatch.setattr("agent.llm.detect_backend", lambda: "cli")
    return llamadas


_SPEC_BASE = {
    "app_name": "Turnos Barbería",
    "tagline": "Agenda sin llamadas",
    "description": "Turnos para barberías.",
    "platform": "web",
    "target_users": "Barberos",
    "features": ["Agendar turnos"],
    "needs_auth": True,
    "color_primary": "#1f2937",
    "style_notes": "Oscuro",
    "integrations": ["WhatsApp"],
    "entities": [
        {
            "name": "Client",
            "description": "Cliente",
            "fields": [
                {"name": "full_name", "type": "string", "required": True, "description": "Nombre"},
                {"name": "phone", "type": "string", "required": False, "description": "Celular"},
            ],
        }
    ],
}

_SPEC_REVISADO = json.loads(json.dumps(_SPEC_BASE))
_SPEC_REVISADO["entities"].append(
    {
        "name": "Appointment",
        "description": "Turno",
        "fields": [{"name": "starts_at", "type": "datetime", "required": True, "description": "Inicio"}],
    }
)
