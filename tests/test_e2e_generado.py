"""Pruebas de las E2E generadas.

No corren Playwright (eso pasa en CI, que sí puede instalar navegadores).
Lo que se verifica aquí es la consistencia: que cada `data-testid` que usan
las pruebas exista de verdad en el frontend, y que los datos de prueba
cubran todos los campos. Un testid mal escrito produce pruebas que fallan
siempre o, peor, que no prueban nada.
"""
import ast
import json
import re

import pytest

from agent.generator import generate_project
from agent.interview import demo_spec
from agent.spec import AppSpec

# getByTestId("algo") con comillas simples, dobles o plantilla sin interpolar
USO_TESTID = re.compile(r"""getByTestId\(\s*['"`]([a-z0-9_-]+)['"`]""")
# Los interpolados, como `nav-${entidad.plural}` o `field-${campo.name}`
USO_TESTID_PREFIJO = re.compile(r"getByTestId\(\s*`([a-z-]+)-\$\{")

# data-testid="algo" en el JSX
DEF_TESTID = re.compile(r"""data-testid=["']([a-z0-9_-]+)["']""")
# data-testid={`algo-${...}`}
DEF_TESTID_PREFIJO = re.compile(r"data-testid=\{`([a-z-]+)-\$\{")


@pytest.fixture(scope="module")
def proyectos(tmp_path_factory):
    """Un proyecto con login y otro sin, para cubrir las dos ramas."""
    base = tmp_path_factory.mktemp("e2e")
    salida = {}
    for nombre, auth in (("auth", True), ("noauth", False)):
        spec = demo_spec("web")
        spec.needs_auth = auth
        salida[nombre] = generate_project(spec, base / nombre, use_llm=False, overwrite=True)
    return salida


def _archivos_e2e(proyecto):
    return sorted((proyecto / "frontend" / "tests" / "e2e").glob("*.ts"))


def _fuente_frontend(proyecto):
    src = proyecto / "frontend" / "src"
    return "\n".join(f.read_text(encoding="utf-8") for f in src.rglob("*.tsx"))


class TestArchivos:
    def test_se_generan_las_pruebas_e2e(self, proyectos):
        for proyecto in proyectos.values():
            frontend = proyecto / "frontend"
            assert (frontend / "playwright.config.ts").is_file()
            nombres = [f.name for f in _archivos_e2e(proyecto)]
            for esperado in ["ayudas.ts", "humo.spec.ts", "crud.spec.ts",
                             "seguridad.spec.ts"]:
                assert esperado in nombres, f"falta {esperado}"
            assert (frontend / "tests/e2e/limpiar-base.mjs").is_file()

    def test_una_app_movil_no_lleva_e2e_de_web(self, tmp_path):
        """Playwright prueba la web; la app móvil no tiene navegador."""
        p = generate_project(demo_spec("mobile"), tmp_path, use_llm=False, overwrite=True)
        assert not (p / "frontend").exists()

    def test_playwright_esta_en_las_dependencias(self, proyectos):
        for proyecto in proyectos.values():
            pkg = json.loads((proyecto / "frontend" / "package.json").read_text())
            assert "@playwright/test" in pkg["devDependencies"]
            assert "playwright test" in pkg["scripts"]["test:e2e"]

    def test_el_codigo_e2e_no_usa_dirname(self, proyectos):
        """El frontend generado es ESM ("type": "module")."""
        for proyecto in proyectos.values():
            assert json.loads((proyecto / "frontend/package.json").read_text())["type"] == "module"
            for archivo in list(_archivos_e2e(proyecto)) + list(
                (proyecto / "frontend/tests/e2e").glob("*.mjs")
            ):
                assert "__dirname" not in archivo.read_text(encoding="utf-8"), (
                    f"{archivo.name} usa __dirname, que no existe en ESM"
                )

    def test_los_artefactos_de_playwright_se_ignoran_en_git(self, proyectos):
        for proyecto in proyectos.values():
            ignorado = (proyecto / ".gitignore").read_text()
            for entrada in ["test-results/", "playwright-report/"]:
                assert entrada in ignorado


class TestConfiguracion:
    def test_playwright_levanta_backend_y_frontend(self, proyectos):
        cfg = (proyectos["auth"] / "frontend" / "playwright.config.ts").read_text()
        assert "webServer" in cfg
        assert "uvicorn" in cfg, "debe arrancar el backend"
        assert "npm run build" in cfg, "debe compilar el frontend"
        assert cfg.count("command:") == 2, "se esperan dos servidores"

    def test_la_base_de_e2e_esta_aparte_y_sin_datos_de_ejemplo(self, proyectos):
        cfg = (proyectos["auth"] / "frontend" / "playwright.config.ts").read_text()
        assert "e2e.db" in cfg, "no debe usar la base de desarrollo"
        assert 'SKIP_SEED: "1"' in cfg, "los datos de ejemplo harían fallar los conteos"

    def test_la_base_se_borra_antes_de_arrancar_playwright(self, proyectos):
        """No puede ser un globalSetup.

        Playwright levanta el backend ANTES del globalSetup, así que borrar el
        archivo SQLite desde ahí deja al servidor con un handle a un archivo
        inexistente: "attempt to write a readonly database". Tiene que correr
        antes de `playwright test`.
        """
        for proyecto in proyectos.values():
            cfg = (proyecto / "frontend" / "playwright.config.ts").read_text()
            assert "globalSetup" not in cfg, (
                "borrar la base desde globalSetup rompe el backend ya arrancado"
            )
            pkg = json.loads((proyecto / "frontend" / "package.json").read_text())
            script = pkg["scripts"]["test:e2e"]
            assert script.index("limpiar-base") < script.index("playwright test"), (
                "la limpieza debe ir antes de playwright test"
            )
            limpieza = (proyecto / "frontend/tests/e2e/limpiar-base.mjs").read_text()
            assert "e2e.db" in limpieza and "unlinkSync" in limpieza
            assert "__dirname" not in limpieza, "no existe en ESM; usar import.meta.url"

    def test_no_corren_en_paralelo(self, proyectos):
        """Comparten una sola base de datos."""
        cfg = (proyectos["auth"] / "frontend" / "playwright.config.ts").read_text()
        assert "fullyParallel: false" in cfg
        assert "workers: 1" in cfg


class TestSelectores:
    """La parte que de verdad importa: que los testids coincidan."""

    def test_todos_los_testid_usados_existen_en_el_frontend(self, proyectos):
        for nombre, proyecto in proyectos.items():
            jsx = _fuente_frontend(proyecto)
            definidos = set(DEF_TESTID.findall(jsx))
            prefijos_definidos = set(DEF_TESTID_PREFIJO.findall(jsx))

            for archivo in _archivos_e2e(proyecto):
                codigo = archivo.read_text(encoding="utf-8")
                for usado in set(USO_TESTID.findall(codigo)):
                    assert usado in definidos, (
                        f"[{nombre}] {archivo.name} usa data-testid '{usado}' "
                        f"que no existe en el frontend. Definidos: {sorted(definidos)}"
                    )
                for prefijo in set(USO_TESTID_PREFIJO.findall(codigo)):
                    assert prefijo in prefijos_definidos, (
                        f"[{nombre}] {archivo.name} usa el prefijo '{prefijo}-${{...}}' "
                        f"que no existe. Definidos: {sorted(prefijos_definidos)}"
                    )

    def test_hay_un_testid_por_cada_campo_del_formulario(self, proyectos):
        """`field-<nombre>` tiene que resolver para todos los campos."""
        jsx = _fuente_frontend(proyectos["auth"])
        assert "data-testid={`field-${f.name}`}" in jsx
        # Los tres tipos de control del formulario lo llevan
        assert jsx.count("data-testid={`field-${f.name}`}") == 3, (
            "checkbox, textarea e input deben tener testid"
        )

    def test_los_testid_de_navegacion_cubren_todas_las_entidades(self, proyectos):
        jsx = _fuente_frontend(proyectos["auth"])
        assert "data-testid={`nav-${e.plural}`}" in jsx
        assert 'data-testid="nav-home"' in jsx

    def test_sin_login_no_se_usan_testid_de_autenticacion(self, proyectos):
        """Los testids de login no existen si la app no tiene login."""
        proyecto = proyectos["noauth"]
        jsx = _fuente_frontend(proyecto)
        assert "login-page" not in jsx
        for archivo in _archivos_e2e(proyecto):
            codigo = archivo.read_text(encoding="utf-8")
            for prohibido in ["login-page", "logout", "auth-error", "toggle-mode"]:
                assert f'getByTestId("{prohibido}")' not in codigo, (
                    f"{archivo.name} usa '{prohibido}' en una app sin login"
                )


class TestDatosDePrueba:
    def test_los_metadatos_e2e_cubren_todas_las_entidades_y_campos(self, proyectos):
        spec = AppSpec.from_json_file(proyectos["auth"] / "spec.json")
        ayudas = (proyectos["auth"] / "frontend/tests/e2e/ayudas.ts").read_text()
        crudo = ayudas.split("export const ENTITIES =", 1)[1].split(" as {", 1)[0]
        datos = json.loads(crudo.strip())

        assert [e["plural"] for e in datos] == [e.plural for e in spec.entities]
        for meta, entidad in zip(datos, spec.entities):
            assert [f["name"] for f in meta["fields"]] == [f.name for f in entidad.fields]
            assert meta["displayField"] == entidad.display_field
            for campo in meta["fields"]:
                assert campo["value"] not in (None, ""), f"{campo['name']} sin valor de prueba"

    def test_los_valores_de_prueba_encajan_con_el_tipo(self, proyectos):
        ayudas = (proyectos["auth"] / "frontend/tests/e2e/ayudas.ts").read_text()
        crudo = ayudas.split("export const ENTITIES =", 1)[1].split(" as {", 1)[0]
        for entidad in json.loads(crudo.strip()):
            for campo in entidad["fields"]:
                valor, tipo = campo["value"], campo["type"]
                if tipo in ("integer",):
                    assert isinstance(valor, int) and not isinstance(valor, bool)
                elif tipo == "float":
                    assert isinstance(valor, (int, float))
                elif tipo == "boolean":
                    assert isinstance(valor, bool)
                elif tipo == "email":
                    assert "@" in valor
                elif tipo == "url":
                    assert str(valor).startswith("http")
                elif tipo == "date":
                    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor)
                elif tipo == "datetime":
                    # El input datetime-local espera "AAAA-MM-DDTHH:MM"
                    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", valor)

    def test_todos_los_tipos_de_campo_tienen_valor_e2e(self, tmp_path):
        from agent.spec import FIELD_TYPES

        campos = [{"name": f"campo_{t}", "type": t} for t in FIELD_TYPES]
        spec = AppSpec("Tipos", "t", "d", "web", needs_auth=False,
                       entities=[{"name": "Todo", "description": "d", "fields": campos}])
        p = generate_project(spec, tmp_path, use_llm=False, overwrite=True)
        ayudas = (p / "frontend/tests/e2e/ayudas.ts").read_text()
        crudo = ayudas.split("export const ENTITIES =", 1)[1].split(" as {", 1)[0]
        datos = json.loads(crudo.strip())[0]
        assert len(datos["fields"]) == len(FIELD_TYPES)


class TestContenidoDeLasPruebas:
    def test_las_e2e_de_seguridad_cubren_lo_importante(self, proyectos):
        codigo = (proyectos["auth"] / "frontend/tests/e2e/seguridad.spec.ts").read_text()
        for tema in [
            "no se ejecuta",                 # XSS
            "no ve los datos de otro",       # aislamiento entre usuarios
            "borra el token",                # limpieza de sesión
            "token inventado",               # token manipulado
            "contraseña equivocada",
        ]:
            assert tema in codigo, f"falta la prueba de: {tema}"

    def test_sin_login_las_e2e_de_seguridad_se_reducen(self, proyectos):
        """Sin autenticación no hay aislamiento que probar, pero el XSS sí."""
        codigo = (proyectos["noauth"] / "frontend/tests/e2e/seguridad.spec.ts").read_text()
        assert "no se ejecuta" in codigo, "el XSS se prueba siempre"
        assert "no ve los datos de otro" not in codigo

    def test_el_crud_e2e_recorre_todas_las_entidades(self, proyectos):
        codigo = (proyectos["auth"] / "frontend/tests/e2e/crud.spec.ts").read_text()
        assert "for (const entidad of ENTITIES)" in codigo
        for accion in ["crear", "editar", "eliminar", "búsqueda", "recargar"]:
            assert accion in codigo.lower(), f"falta probar: {accion}"

    def test_el_dialogo_de_confirmar_borrado_se_acepta(self, proyectos):
        """El botón de eliminar abre un confirm(); sin manejarlo la prueba se cuelga."""
        codigo = (proyectos["auth"] / "frontend/tests/e2e/crud.spec.ts").read_text()
        assert 'page.once("dialog"' in codigo
