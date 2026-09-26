"""Pruebas de la iteración por la web: endpoints y su seguridad."""
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from agent.generator import generate_project
from agent.interview import demo_spec
from web import server as srv

try:  # pytest importa tests/ como paquete; otros corredores no
    from .test_web import esperar, pedir
except ImportError:  # pragma: no cover
    from test_web import esperar, pedir


@pytest.fixture
def servidor_con_proyecto(tmp_path, fake_llm, monkeypatch):
    """Servidor real con una app ya generada en la carpeta de salida."""
    srv.SESSIONS.clear()
    srv.JOBS.clear()
    salida = tmp_path / "proyectos"
    salida.mkdir(parents=True, exist_ok=True)
    srv.Handler.out_root = salida

    proyecto = generate_project(demo_spec("web"), salida, use_llm=False, overwrite=True)

    # El modelo y las pruebas del proyecto se simulan.
    def code_agent(prompt, cwd, **kwargs):
        from pathlib import Path

        (Path(cwd) / "backend" / "app" / "nuevo.py").write_text("x = 1", encoding="utf-8")
        return "agregué un archivo"

    monkeypatch.setattr("agent.llm.code_agent", code_agent)
    monkeypatch.setattr("agent.iterate.tests_available", lambda p: (True, ""))
    monkeypatch.setattr("agent.iterate.run_tests", lambda p: (True, "5 passed"))

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", proyecto
    httpd.shutdown()
    httpd.server_close()


class TestListado:
    def test_lista_los_proyectos_generados(self, servidor_con_proyecto):
        base, proyecto = servidor_con_proyecto
        code, datos = pedir(base, "/api/projects")
        assert code == 200
        assert len(datos["projects"]) == 1
        p = datos["projects"][0]
        assert p["slug"] == "gym-flow"
        assert p["name"] == "GymFlow"
        assert p["platform"] == "web"
        assert "Member" in p["entities"]
        assert p["history"], "debe traer el historial de git"

    def test_ignora_carpetas_que_no_son_proyectos(self, servidor_con_proyecto):
        base, proyecto = servidor_con_proyecto
        (proyecto.parent / "basura").mkdir()
        (proyecto.parent / "otra").mkdir()
        (proyecto.parent / "otra" / "spec.json").write_text("{roto", encoding="utf-8")
        code, datos = pedir(base, "/api/projects")
        assert [p["slug"] for p in datos["projects"]] == ["gym-flow"]

    def test_el_listado_pide_token(self, servidor_con_proyecto):
        base, _ = servidor_con_proyecto
        assert pedir(base, "/api/projects", token=None)[0] == 403


class TestSeguridadDeRutas:
    @pytest.mark.parametrize("slug", [
        "../../etc", "..", "../otro", "/etc/passwd", "", ".",
        "gym-flow/../../..", "no-existe",
    ])
    def test_no_se_puede_salir_de_la_carpeta_de_proyectos(self, servidor_con_proyecto, slug):
        """El slug viene del navegador: no puede apuntar fuera de out_root."""
        base, _ = servidor_con_proyecto
        code, _ = pedir(base, "/api/iterate", {"slug": slug, "instruction": "algo"})
        assert code == 410, f"el slug '{slug}' respondió {code}"

    def test_revert_tambien_valida_la_ruta(self, servidor_con_proyecto):
        base, _ = servidor_con_proyecto
        code, _ = pedir(base, "/api/revert", {"slug": "../../etc"})
        assert code == 410

    def test_iterate_y_revert_piden_token(self, servidor_con_proyecto):
        base, _ = servidor_con_proyecto
        for ruta, cuerpo in [
            ("/api/iterate", {"slug": "gym-flow", "instruction": "x"}),
            ("/api/revert", {"slug": "gym-flow"}),
        ]:
            assert pedir(base, ruta, cuerpo, token=None)[0] == 403

    def test_instruccion_vacia_se_rechaza(self, servidor_con_proyecto):
        base, _ = servidor_con_proyecto
        for instr in ["", "   "]:
            code, _ = pedir(base, "/api/iterate", {"slug": "gym-flow", "instruction": instr})
            assert code == 400


class TestIteracion:
    def test_iterar_cambia_el_proyecto_y_reporta(self, servidor_con_proyecto):
        base, proyecto = servidor_con_proyecto
        code, datos = pedir(base, "/api/iterate",
                            {"slug": "gym-flow", "instruction": "agrega un archivo"})
        assert code == 200

        r = esperar(base, datos["job_id"])
        assert r["kind"] == "iterated"
        assert r["ok"] is True
        assert r["files_changed"] == ["backend/app/nuevo.py"]
        assert r["tests_ran"] and r["tests_passed"]
        assert r["commit"]
        assert (proyecto / "backend" / "app" / "nuevo.py").exists()

    def test_se_puede_iterar_sin_pruebas(self, servidor_con_proyecto):
        base, _ = servidor_con_proyecto
        code, datos = pedir(base, "/api/iterate",
                            {"slug": "gym-flow", "instruction": "algo", "with_tests": False})
        r = esperar(base, datos["job_id"])
        assert r["ok"] and not r["tests_ran"]

    def test_revertir_deshace_la_iteracion(self, servidor_con_proyecto):
        base, proyecto = servidor_con_proyecto
        code, datos = pedir(base, "/api/iterate",
                            {"slug": "gym-flow", "instruction": "agrega un archivo"})
        esperar(base, datos["job_id"])
        assert (proyecto / "backend" / "app" / "nuevo.py").exists()

        code, datos = pedir(base, "/api/revert", {"slug": "gym-flow"})
        assert code == 200
        assert "agrega un archivo" in datos["reverted"]
        assert not (proyecto / "backend" / "app" / "nuevo.py").exists()

    def test_no_se_puede_revertir_mas_alla_del_inicio(self, servidor_con_proyecto):
        base, _ = servidor_con_proyecto
        code, datos = pedir(base, "/api/revert", {"slug": "gym-flow"})
        assert code == 400
        assert "anterior" in datos["error"]

    def test_la_salida_de_las_pruebas_se_recorta(self, servidor_con_proyecto, monkeypatch):
        """No queremos mandarle megas de log al celular."""
        monkeypatch.setattr("agent.iterate.run_tests", lambda p: (False, "X" * 50_000))
        base, _ = servidor_con_proyecto
        code, datos = pedir(base, "/api/iterate",
                            {"slug": "gym-flow", "instruction": "algo"})
        r = esperar(base, datos["job_id"], limite=60)
        assert len(r["test_output"]) <= 4000
        assert r["tests_passed"] is False
