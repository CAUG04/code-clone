"""Pruebas del modo iteración.

El modelo y pytest se simulan: lo que se prueba es la máquina de estados
(git, el bucle de reparación, el commit, el revert), no la calidad de las
ediciones del modelo.
"""
import subprocess

import pytest

from agent import iterate as it
from agent.generator import generate_project
from agent.interview import demo_spec


@pytest.fixture
def proyecto(tmp_path):
    """Un proyecto generado de verdad, con su git inicial."""
    return generate_project(demo_spec("web"), tmp_path, use_llm=False, overwrite=True)


def _escribir(proyecto, relativo, texto):
    (proyecto / relativo).write_text(texto, encoding="utf-8")


@pytest.fixture
def modelo(monkeypatch):
    """Simula a Claude Code: cada llamada aplica un cambio programado."""
    estado = {"llamadas": [], "acciones": []}

    def code_agent(prompt, cwd, **kwargs):
        estado["llamadas"].append({"prompt": prompt, "cwd": cwd, "kwargs": kwargs})
        if estado["acciones"]:
            estado["acciones"].pop(0)(cwd)
        return f"hice el cambio {len(estado['llamadas'])}"

    monkeypatch.setattr("agent.llm.code_agent", code_agent)
    return estado


@pytest.fixture
def pytest_falso(monkeypatch):
    """Controla qué devuelve la suite del proyecto."""
    estado = {"resultados": [], "corridas": 0}

    def tests_available(project):
        return True, ""

    def run_tests(project):
        estado["corridas"] += 1
        if estado["resultados"]:
            return estado["resultados"].pop(0)
        return True, "3 passed"

    monkeypatch.setattr("agent.iterate.tests_available", tests_available)
    monkeypatch.setattr("agent.iterate.run_tests", run_tests)
    return estado


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

class TestGit:
    def test_el_proyecto_generado_ya_viene_con_git(self, proyecto):
        assert (proyecto / ".git").is_dir()
        assert it.head(proyecto), "no hay commit inicial"
        assert it.history(proyecto)[0]["mensaje"] == "Proyecto generado por code-clone"

    def test_ensure_git_commitea_lo_pendiente(self, proyecto):
        _escribir(proyecto, "nuevo.txt", "algo")
        it.ensure_git(proyecto, "mi mensaje")
        assert it.history(proyecto)[0]["mensaje"] == "mi mensaje"
        limpio = subprocess.run(["git", "status", "--porcelain"], cwd=proyecto,
                                capture_output=True, text=True).stdout
        assert limpio == ""

    def test_revertir_deshace_el_ultimo_cambio(self, proyecto):
        _escribir(proyecto, "spec.json", '{"cambiado": true}')
        it.ensure_git(proyecto, "cambio de prueba")
        it.revert_last(proyecto)
        assert '"cambiado"' not in (proyecto / "spec.json").read_text()

    def test_no_se_puede_revertir_el_commit_inicial(self, proyecto):
        with pytest.raises(RuntimeError):
            it.revert_last(proyecto)

    def test_historial_sin_git_devuelve_vacio(self, tmp_path):
        assert it.history(tmp_path) == []


# ---------------------------------------------------------------------------
# validaciones de entrada
# ---------------------------------------------------------------------------

class TestValidaciones:
    def test_carpeta_inexistente(self, tmp_path):
        r = it.iterate(tmp_path / "no-existe", "algo")
        assert not r.ok and "No existe" in r.error

    def test_carpeta_que_no_es_proyecto(self, tmp_path):
        (tmp_path / "cualquiera").mkdir()
        r = it.iterate(tmp_path / "cualquiera", "algo")
        assert not r.ok and "spec.json" in r.error

    def test_si_el_modelo_no_cambia_nada_se_avisa(self, proyecto, modelo, pytest_falso):
        r = it.iterate(proyecto, "no hagas nada")
        assert not r.ok
        assert "no cambió ningún archivo" in r.error
        assert r.files_changed == []

    def test_un_error_del_modelo_se_reporta(self, proyecto, monkeypatch):
        from agent import llm

        def explota(*a, **k):
            raise llm.LLMError("se cayó el modelo")

        monkeypatch.setattr("agent.llm.code_agent", explota)
        r = it.iterate(proyecto, "algo")
        assert not r.ok and "se cayó el modelo" in r.error


# ---------------------------------------------------------------------------
# el camino feliz
# ---------------------------------------------------------------------------

class TestCambioExitoso:
    def test_cambio_con_pruebas_en_verde(self, proyecto, modelo, pytest_falso):
        modelo["acciones"] = [lambda cwd: _escribir(cwd, "backend/app/nuevo.py", "x = 1")]
        antes = it.head(proyecto)

        r = it.iterate(proyecto, "agrega un archivo")

        assert r.ok
        assert r.files_changed == ["backend/app/nuevo.py"]
        assert r.tests_ran and r.tests_passed
        assert r.repairs == 0
        assert pytest_falso["corridas"] == 1
        assert r.commit and r.commit != antes
        assert it.history(proyecto)[0]["mensaje"].startswith("iterar: agrega un archivo")

    def test_la_instruccion_llega_al_modelo_con_el_contexto(self, proyecto, modelo, pytest_falso):
        modelo["acciones"] = [lambda cwd: _escribir(cwd, "a.txt", "x")]
        it.iterate(proyecto, "agrega paginación a la lista")

        llamada = modelo["llamadas"][0]
        assert "agrega paginación a la lista" in llamada["prompt"]
        assert llamada["cwd"] == proyecto, "debe correr dentro del proyecto"
        contexto = llamada["kwargs"]["append_system"]
        assert "spec.json" in contexto, "el contexto debe mencionar la fuente de verdad"
        assert "Nunca las borres" in contexto, "debe prohibir debilitar las pruebas"

    def test_se_pueden_omitir_las_pruebas(self, proyecto, modelo, pytest_falso):
        modelo["acciones"] = [lambda cwd: _escribir(cwd, "a.txt", "x")]
        r = it.iterate(proyecto, "algo", with_tests=False)
        assert r.ok and not r.tests_ran
        assert pytest_falso["corridas"] == 0

    def test_si_no_se_pueden_correr_las_pruebas_se_explica(self, proyecto, modelo, monkeypatch):
        modelo["acciones"] = [lambda cwd: _escribir(cwd, "a.txt", "x")]
        monkeypatch.setattr("agent.iterate.tests_available",
                            lambda p: (False, "faltan dependencias"))
        r = it.iterate(proyecto, "algo")
        assert r.ok, "sin poder probar, el cambio no se considera fallido"
        assert not r.tests_ran
        assert r.skipped_tests_reason == "faltan dependencias"
        assert "faltan dependencias" in it.describe(r)


# ---------------------------------------------------------------------------
# el bucle de reparación: la parte que hace útil el modo iteración
# ---------------------------------------------------------------------------

class TestReparacion:
    def test_si_las_pruebas_fallan_se_reintenta_y_se_arregla(self, proyecto, modelo, pytest_falso):
        modelo["acciones"] = [
            lambda cwd: _escribir(cwd, "a.txt", "roto"),
            lambda cwd: _escribir(cwd, "a.txt", "arreglado"),
        ]
        pytest_falso["resultados"] = [(False, "1 failed: assert 1 == 2"), (True, "3 passed")]

        r = it.iterate(proyecto, "algo que rompe")

        assert r.ok, "tras el arreglo debería quedar en verde"
        assert r.repairs == 1
        assert pytest_falso["corridas"] == 2
        assert len(modelo["llamadas"]) == 2
        assert (proyecto / "a.txt").read_text() == "arreglado"

    def test_la_salida_de_las_pruebas_se_le_pasa_al_modelo(self, proyecto, modelo, pytest_falso):
        modelo["acciones"] = [lambda cwd: _escribir(cwd, "a.txt", "x")]
        pytest_falso["resultados"] = [(False, "ERROR_MUY_ESPECIFICO_123"), (True, "ok")]

        it.iterate(proyecto, "algo")

        segundo = " ".join(modelo["llamadas"][1]["prompt"].lower().split())
        assert "ERROR_MUY_ESPECIFICO_123".lower() in segundo
        assert "no lo deshagas" in segundo, "debe insistir en no revertir el cambio pedido"

    def test_se_rinde_tras_el_maximo_de_intentos(self, proyecto, modelo, pytest_falso):
        modelo["acciones"] = [lambda cwd: _escribir(cwd, "a.txt", str(i)) for i in range(5)]
        pytest_falso["resultados"] = [(False, "sigue fallando")] * 5

        r = it.iterate(proyecto, "algo imposible", max_repairs=2)

        assert not r.ok
        assert not r.tests_passed
        assert r.repairs == 2
        assert pytest_falso["corridas"] == 3, "1 inicial + 2 reintentos"

    def test_el_commit_queda_marcado_cuando_las_pruebas_fallan(self, proyecto, modelo, pytest_falso):
        modelo["acciones"] = [lambda cwd: _escribir(cwd, "a.txt", "x")]
        pytest_falso["resultados"] = [(False, "falla")] * 5

        r = it.iterate(proyecto, "algo", max_repairs=0)

        assert not r.ok
        assert it.history(proyecto)[0]["mensaje"].startswith("[pruebas en rojo]")
        assert r.commit, "aun fallando se commitea, para poder revertir"

    def test_se_puede_revertir_un_cambio_fallido(self, proyecto, modelo, pytest_falso):
        original = (proyecto / "spec.json").read_text()
        modelo["acciones"] = [lambda cwd: _escribir(cwd, "spec.json", "roto")]
        pytest_falso["resultados"] = [(False, "falla")] * 5

        it.iterate(proyecto, "algo", max_repairs=0)
        assert (proyecto / "spec.json").read_text() == "roto"

        it.revert_last(proyecto)
        assert (proyecto / "spec.json").read_text() == original


# ---------------------------------------------------------------------------
# el resumen que ve el usuario
# ---------------------------------------------------------------------------

class TestDescribe:
    def test_describe_en_verde(self, proyecto, modelo, pytest_falso):
        modelo["acciones"] = [lambda cwd: _escribir(cwd, "a.txt", "x")]
        texto = it.describe(it.iterate(proyecto, "algo"))
        assert "Pruebas: pasan" in texto
        assert "a.txt" in texto

    def test_describe_en_rojo_sugiere_revertir(self, proyecto, modelo, pytest_falso):
        modelo["acciones"] = [lambda cwd: _escribir(cwd, "a.txt", "x")]
        pytest_falso["resultados"] = [(False, "falla")] * 5
        texto = it.describe(it.iterate(proyecto, "algo", max_repairs=0))
        assert "FALLAN" in texto and "--revert" in texto

    def test_describe_menciona_los_arreglos(self, proyecto, modelo, pytest_falso):
        modelo["acciones"] = [lambda cwd: _escribir(cwd, "a.txt", str(i)) for i in range(3)]
        pytest_falso["resultados"] = [(False, "x"), (True, "ok")]
        texto = it.describe(it.iterate(proyecto, "algo"))
        assert "1 arreglo" in texto


# ---------------------------------------------------------------------------
# seguridad: qué puede hacer el modelo al editar
# ---------------------------------------------------------------------------

class TestSeguridad:
    def test_las_herramientas_permitidas_no_incluyen_bash_libre(self):
        from agent.llm import CODE_TOOLS

        assert "Bash" not in CODE_TOOLS, "Bash sin restringir dejaría correr cualquier comando"
        for t in CODE_TOOLS:
            if t.startswith("Bash"):
                assert t.startswith("Bash(") and t.endswith(")")

    def test_no_se_permiten_comandos_de_red_ni_instalacion(self):
        from agent.llm import CODE_TOOLS

        permitidos = " ".join(CODE_TOOLS).lower()
        for peligroso in ["curl", "wget", "pip install", "npm install", "rm ", "sudo"]:
            assert peligroso not in permitidos, f"no debería permitir {peligroso}"

    def test_se_permite_correr_las_pruebas(self):
        from agent.llm import CODE_TOOLS

        assert any("pytest" in t for t in CODE_TOOLS)

    def test_el_contexto_prohibe_debilitar_las_pruebas_de_seguridad(self):
        assert "test_security" in it.CONTEXTO
        assert "seguridad" in it.CONTEXTO.lower()
