"""Pruebas del generador: que el código que produce sea válido y completo."""
import ast
import json
import re

import pytest

from agent.generator import generate_project
from agent.spec import AppSpec

# Restos de plantilla que nunca deben quedar en el código generado.
RESTOS_JINJA = re.compile(r"\{=|=\}|\{%|%\}|__entity__")


IGNORAR = {".git", "node_modules", "__pycache__", ".venv"}


def _archivos(proyecto):
    """Archivos del proyecto, sin los internos de git ni las dependencias."""
    return [
        p for p in proyecto.rglob("*")
        if p.is_file() and not IGNORAR & set(p.relative_to(proyecto).parts)
    ]


class TestEstructura:
    @pytest.mark.parametrize("plataforma,esperadas,ausentes", [
        ("web", ["backend", "frontend"], ["mobile"]),
        ("mobile", ["backend", "mobile"], ["frontend"]),
        ("both", ["backend", "frontend", "mobile"], []),
    ])
    def test_solo_se_generan_las_carpetas_pedidas(self, tmp_path, plataforma, esperadas, ausentes):
        from agent.interview import demo_spec

        p = generate_project(demo_spec(plataforma), tmp_path, use_llm=False)
        for d in esperadas:
            assert (p / d).is_dir(), f"falta {d}"
        for d in ausentes:
            assert not (p / d).exists(), f"{d} no debería estar en una app '{plataforma}'"

    def test_archivos_de_raiz(self, tmp_path, spec_both):
        p = generate_project(spec_both, tmp_path, use_llm=False)
        for f in ["README.md", ".gitignore", "docker-compose.yml", "spec.json",
                  ".github/workflows/tests.yml"]:
            assert (p / f).is_file(), f"falta {f}"

    def test_un_router_y_un_modelo_por_entidad(self, tmp_path, spec_both):
        p = generate_project(spec_both, tmp_path, use_llm=False)
        modelos = (p / "backend/app/models.py").read_text()
        for e in spec_both.entities:
            assert (p / f"backend/app/routers/{e.snake}.py").is_file(), f"falta router de {e.name}"
            assert f"class {e.name}(Base)" in modelos
            assert f'__tablename__ = "{e.plural}"' in modelos

    def test_el_spec_guardado_coincide(self, tmp_path, spec_both):
        p = generate_project(spec_both, tmp_path, use_llm=False)
        assert json.loads((p / "spec.json").read_text()) == json.loads(spec_both.to_json())

    def test_sobrescribir_requiere_permiso(self, tmp_path, spec_web):
        generate_project(spec_web, tmp_path, use_llm=False)
        with pytest.raises(FileExistsError):
            generate_project(spec_web, tmp_path, use_llm=False)
        generate_project(spec_web, tmp_path, use_llm=False, overwrite=True)  # no lanza


class TestCodigoValido:
    def test_todo_el_python_generado_compila(self, tmp_path, spec_both):
        p = generate_project(spec_both, tmp_path, use_llm=False)
        archivos = [f for f in _archivos(p) if f.suffix == ".py"]
        assert len(archivos) > 5, "se generaron muy pocos archivos Python"
        for f in archivos:
            try:
                ast.parse(f.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                pytest.fail(f"{f.relative_to(p)} no es Python válido: {exc}")

    @pytest.mark.parametrize("plataforma", ["web", "mobile", "both"])
    def test_no_quedan_restos_de_plantilla(self, tmp_path, plataforma):
        from agent.interview import demo_spec

        p = generate_project(demo_spec(plataforma), tmp_path, use_llm=False)
        for f in _archivos(p):
            texto = f.read_text(encoding="utf-8", errors="ignore")
            resto = RESTOS_JINJA.search(texto)
            assert resto is None, f"{f.relative_to(p)} tiene resto de plantilla: {resto.group()}"

    def test_los_json_generados_son_validos(self, tmp_path, spec_both):
        p = generate_project(spec_both, tmp_path, use_llm=False)
        for f in [x for x in _archivos(p) if x.suffix == ".json"]:
            json.loads(f.read_text(encoding="utf-8"))  # lanza si está mal

    def test_no_hay_archivos_vacios(self, tmp_path, spec_both):
        p = generate_project(spec_both, tmp_path, use_llm=False)
        vacios = [
            f.relative_to(p) for f in _archivos(p)
            if not f.read_text(encoding="utf-8", errors="ignore").strip()
            and f.name != "__init__.py"
        ]
        assert not vacios, f"archivos vacíos: {vacios}"


class TestLogin:
    def test_con_login_se_genera_auth(self, tmp_path, spec_both):
        p = generate_project(spec_both, tmp_path, use_llm=False)
        assert (p / "backend/app/auth.py").is_file()
        assert "class User(Base)" in (p / "backend/app/models.py").read_text()
        assert "owner_id" in (p / "backend/app/models.py").read_text()
        assert (p / "frontend/src/pages/Login.tsx").is_file()

    def test_sin_login_no_se_genera_auth(self, tmp_path, spec_sin_login):
        p = generate_project(spec_sin_login, tmp_path, use_llm=False)
        assert not (p / "backend/app/auth.py").exists()
        modelos = (p / "backend/app/models.py").read_text()
        assert "class User(Base)" not in modelos
        assert "owner_id" not in modelos


class TestPruebasGeneradas:
    def test_cada_app_trae_su_suite(self, tmp_path, spec_both):
        p = generate_project(spec_both, tmp_path, use_llm=False)
        for f in ["tests/__init__.py", "tests/conftest.py", "tests/test_api.py",
                  "tests/test_security.py", "pytest.ini"]:
            assert (p / "backend" / f).is_file(), f"falta {f}"

    def test_los_datos_de_prueba_cubren_todos_los_campos(self, tmp_path, spec_both):
        p = generate_project(spec_both, tmp_path, use_llm=False)
        conftest = (p / "backend/tests/conftest.py").read_text()
        arbol = ast.parse(conftest)
        payloads = next(
            n.value for n in arbol.body
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "PAYLOADS"
        )
        datos = ast.literal_eval(payloads)
        assert set(datos) == {e.plural for e in spec_both.entities}
        for e in spec_both.entities:
            assert set(datos[e.plural]) == {f.name for f in e.fields}

    def test_las_pruebas_de_seguridad_cubren_el_aislamiento(self, tmp_path, spec_both):
        """La prueba más importante no puede faltar."""
        p = generate_project(spec_both, tmp_path, use_llm=False)
        texto = (p / "backend/tests/test_security.py").read_text()
        for esperado in [
            "test_un_usuario_no_ve_los_datos_de_otro",
            "test_un_usuario_no_puede_modificar_ni_borrar_lo_de_otro",
            "test_sin_token_no_se_puede_hacer_nada",
            "test_la_contrasena_nunca_viaja_en_las_respuestas",
            "test_inyeccion_sql_no_rompe_ni_borra_nada",
        ]:
            assert esperado in texto, f"falta la prueba {esperado}"

    def test_el_seed_se_puede_desactivar(self, tmp_path, spec_both):
        """Sin esto las pruebas generadas no serían deterministas."""
        p = generate_project(spec_both, tmp_path, use_llm=False)
        assert 'SKIP_SEED' in (p / "backend/app/main.py").read_text()


class TestDatosDeEjemplo:
    def test_el_seed_sintetico_respeta_los_tipos(self, tmp_path):
        """Cada tipo de campo debe producir un valor que el backend acepte."""
        campos = [{"name": f"campo_{t}", "type": t} for t in
                  ["string", "text", "integer", "float", "boolean", "date", "datetime", "email", "url"]]
        spec = AppSpec("Tipos", "t", "d", "web", needs_auth=False,
                       entities=[{"name": "Todo", "description": "d", "fields": campos}])
        p = generate_project(spec, tmp_path, use_llm=False)
        seed = json.loads((p / "backend/app/seed.json").read_text())

        assert set(seed) == {"Todo"}
        fila = seed["Todo"][0]
        assert isinstance(fila["campo_integer"], int)
        assert isinstance(fila["campo_float"], float)
        assert isinstance(fila["campo_boolean"], bool)
        assert "@" in fila["campo_email"]
        assert fila["campo_url"].startswith("http")
        # Las fechas deben ser ISO parseables
        from datetime import date, datetime
        date.fromisoformat(fila["campo_date"])
        datetime.fromisoformat(fila["campo_datetime"])

    def test_no_se_llama_al_modelo_cuando_use_llm_es_falso(self, tmp_path, spec_web, monkeypatch):
        def explota(*a, **k):
            raise AssertionError("no debería llamar al modelo con use_llm=False")

        monkeypatch.setattr("agent.llm.ask_json", explota)
        generate_project(spec_web, tmp_path, use_llm=False)


class TestPostgres:
    def test_la_url_de_postgres_se_normaliza(self, tmp_path, spec_web):
        """Render y Railway entregan 'postgres://', que rompería psycopg 3."""
        p = generate_project(spec_web, tmp_path, use_llm=False)
        db = (p / "backend/app/database.py").read_text()
        assert "postgresql+psycopg://" in db
        assert 'startswith("postgres://")' in db
