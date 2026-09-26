"""Pruebas del servidor web: flujo de la entrevista y seguridad del servidor.

Levanta el servidor de verdad en un puerto libre y le habla por HTTP, así
que cubre el enrutado real y no una simulación.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from web import server as srv


@pytest.fixture
def servidor(tmp_path, fake_llm):
    """Servidor real en un puerto libre, con el modelo simulado."""
    srv.SESSIONS.clear()
    srv.JOBS.clear()
    srv.Handler.out_root = tmp_path / "proyectos"
    srv.Handler.out_root.mkdir(parents=True, exist_ok=True)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    puerto = httpd.server_address[1]
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{puerto}"
    httpd.shutdown()
    httpd.server_close()


def pedir(base, ruta, cuerpo=None, token=srv.TOKEN, cabeceras=None, raw=None):
    """Petición HTTP que devuelve (código, datos) sin lanzar en los errores."""
    heads = {"Content-Type": "application/json"}
    if token is not None:
        heads["X-Token"] = token
    heads.update(cabeceras or {})
    data = raw if raw is not None else (json.dumps(cuerpo).encode() if cuerpo else None)
    req = urllib.request.Request(base + ruta, data=data, headers=heads,
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        crudo = exc.read()
        try:
            return exc.code, json.loads(crudo or b"{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": crudo.decode("utf-8", "replace")}


def esperar(base, job_id, limite=30):
    for _ in range(limite * 10):
        time.sleep(0.1)
        code, job = pedir(base, f"/api/job/{job_id}")
        assert code == 200
        if job["status"] == "done":
            return job["result"]
        if job["status"] == "error":
            pytest.fail("el trabajo falló: " + str(job["error"]))
    pytest.fail("el trabajo no terminó a tiempo")


# ---------------------------------------------------------------------------
# Seguridad del servidor
# ---------------------------------------------------------------------------

class TestSeguridad:
    def test_sin_token_no_responde_nada(self, servidor):
        for ruta in ["/", "/api/health", "/api/job/x"]:
            code, _ = pedir(servidor, ruta, token=None)
            assert code == 403, f"{ruta} respondió {code} sin token"

    def test_token_equivocado_se_rechaza(self, servidor):
        for malo in ["", "otro-token", srv.TOKEN + "x", srv.TOKEN[:-1]]:
            code, _ = pedir(servidor, "/api/health", token=malo)
            assert code == 403, f"aceptó el token '{malo}'"

    def test_los_endpoints_que_escriben_tambien_piden_token(self, servidor):
        for ruta, cuerpo in [
            ("/api/start", {"idea": "x", "platform": "web"}),
            ("/api/answers", {"session_id": "x", "answers": []}),
            ("/api/build", {"session_id": "x"}),
            ("/api/revise", {"session_id": "x", "feedback": "y"}),
            ("/api/demo", {"platform": "web"}),
        ]:
            code, _ = pedir(servidor, ruta, cuerpo, token=None)
            assert code == 403, f"{ruta} respondió {code} sin token"

    def test_el_token_es_suficientemente_largo(self):
        assert len(srv.TOKEN) >= 11

    def test_la_pagina_lleva_el_token_de_sesion(self, servidor):
        req = urllib.request.Request(f"{servidor}/?t={srv.TOKEN}")
        with urllib.request.urlopen(req, timeout=10) as res:
            html = res.read().decode()
        assert f'const TOKEN = "{srv.TOKEN}"' in html
        assert "__TOKEN__" not in html, "quedó el marcador sin reemplazar"

    def test_las_credenciales_del_entorno_no_se_filtran_a_la_pagina(self, servidor, monkeypatch):
        """La página puede nombrar GITHUB_TOKEN como ayuda, pero nunca su valor."""
        monkeypatch.setattr("os.environ", dict(os.environ,
                                               GITHUB_TOKEN="ghp_valorsecretodeprueba123",
                                               ANTHROPIC_API_KEY="sk-ant-valorsecretodeprueba"))
        req = urllib.request.Request(f"{servidor}/?t={srv.TOKEN}")
        with urllib.request.urlopen(req, timeout=10) as res:
            html = res.read().decode()
        assert "ghp_valorsecretodeprueba123" not in html
        assert "sk-ant-valorsecretodeprueba" not in html

    def test_health_no_expone_el_valor_del_token_de_github(self, servidor):
        """Reporta si GitHub está configurado, no con qué credencial."""
        code, datos = pedir(servidor, "/api/health")
        real = os.environ.get("GITHUB_TOKEN", "")
        if real:
            assert real not in json.dumps(datos)
        assert isinstance(datos["github_ready"], bool)

    @pytest.mark.parametrize("ruta", [
        "/../../etc/passwd", "/..%2f..%2fetc%2fpasswd",
        "/static/../server.py", "/web/server.py", "/.env", "/agent/llm.py",
    ])
    def test_no_se_pueden_leer_archivos_del_servidor(self, servidor, ruta):
        """Solo servimos la página; no hay servidor de archivos estático."""
        code, datos = pedir(servidor, ruta)
        assert code in (403, 404), f"{ruta} respondió {code}"
        assert "SECRET" not in str(datos) and "import" not in str(datos)

    def test_una_sesion_inexistente_da_410_y_no_500(self, servidor):
        code, datos = pedir(servidor, "/api/build", {"session_id": "inventada"})
        assert code == 410
        assert "error" in datos

    def test_cuerpo_json_invalido_no_tumba_el_servidor(self, servidor):
        code, _ = pedir(servidor, "/api/start", raw=b"{roto")
        assert code == 400
        # Y el servidor sigue vivo
        assert pedir(servidor, "/api/health")[0] == 200

    def test_plataforma_invalida_se_rechaza(self, servidor):
        code, _ = pedir(servidor, "/api/start", {"idea": "x", "platform": "../../etc"})
        assert code == 400

    def test_idea_vacia_se_rechaza(self, servidor):
        for idea in ["", "   "]:
            code, _ = pedir(servidor, "/api/start", {"idea": idea, "platform": "web"})
            assert code == 400


# ---------------------------------------------------------------------------
# Flujo de la entrevista
# ---------------------------------------------------------------------------

class TestFlujo:
    def test_health_reporta_el_estado(self, servidor):
        code, datos = pedir(servidor, "/api/health")
        assert code == 200
        assert datos["ok"] is True
        for clave in ["engine", "engine_ready", "github", "github_ready"]:
            assert clave in datos

    def test_entrevista_completa_hasta_construir(self, servidor):
        code, datos = pedir(servidor, "/api/start", {"idea": "Turnos para barbería", "platform": "web"})
        assert code == 200
        sid = datos["session_id"]

        ronda = esperar(servidor, datos["job_id"])
        assert ronda["kind"] == "questions"
        assert ronda["round"] == 1
        assert len(ronda["questions"]) >= 1

        code, datos = pedir(servidor, "/api/answers", {"session_id": sid, "answers": ["Solo yo"]})
        assert code == 200
        siguiente = esperar(servidor, datos["job_id"])
        assert siguiente["kind"] in ("questions", "plan")

        code, datos = pedir(servidor, "/api/skip", {"session_id": sid})
        plan = esperar(servidor, datos["job_id"])
        assert plan["kind"] == "plan"
        assert plan["spec"]["app_name"] == "Turnos Barbería"
        assert plan["spec"]["slug"] == "turnos-barberia"

        code, datos = pedir(servidor, "/api/build", {"session_id": sid, "publish": False})
        hecho = esperar(servidor, datos["job_id"])
        assert hecho["kind"] == "built"
        assert hecho["repo_url"] is None
        assert (srv.Handler.out_root / "turnos-barberia" / "README.md").is_file()

    def test_las_respuestas_quedan_en_la_transcripcion(self, servidor, fake_llm):
        code, datos = pedir(servidor, "/api/start", {"idea": "Una idea", "platform": "mobile"})
        sid = datos["session_id"]
        esperar(servidor, datos["job_id"])
        code, datos = pedir(servidor, "/api/answers", {"session_id": sid, "answers": ["Mi equipo"]})
        esperar(servidor, datos["job_id"])

        enviados = [c["user"] for c in fake_llm]
        assert any("Mi equipo" in u for u in enviados), "la respuesta no llegó al modelo"
        assert any("Una idea" in u for u in enviados)

    def test_respuesta_vacia_se_convierte_en_tu_decides(self, servidor, fake_llm):
        code, datos = pedir(servidor, "/api/start", {"idea": "Idea", "platform": "web"})
        sid = datos["session_id"]
        esperar(servidor, datos["job_id"])
        code, datos = pedir(servidor, "/api/answers", {"session_id": sid, "answers": [""]})
        esperar(servidor, datos["job_id"])
        assert any("decide" in c["user"].lower() for c in fake_llm)

    def test_revisar_el_plan_lo_cambia(self, servidor):
        code, datos = pedir(servidor, "/api/demo", {"platform": "web"})
        sid = datos["session_id"]
        assert len(datos["spec"]["entities"]) == 3

        code, datos = pedir(servidor, "/api/revise",
                            {"session_id": sid, "feedback": "agrega cambios por favor"})
        assert code == 200
        plan = esperar(servidor, datos["job_id"])
        assert [e["name"] for e in plan["spec"]["entities"]] == ["Client", "Appointment"]

    def test_no_se_puede_revisar_sin_plan(self, servidor):
        code, datos = pedir(servidor, "/api/start", {"idea": "x", "platform": "web"})
        sid = datos["session_id"]
        code, _ = pedir(servidor, "/api/revise", {"session_id": sid, "feedback": "algo"})
        assert code == 400

    def test_la_demo_no_llama_al_modelo(self, servidor, fake_llm):
        code, datos = pedir(servidor, "/api/demo", {"platform": "both"})
        assert code == 200
        assert datos["spec"]["app_name"] == "GymFlow"
        assert fake_llm == [], "la demo no debería llamar al modelo"

    def test_un_error_del_modelo_llega_como_error_del_trabajo(self, servidor, monkeypatch):
        from agent import llm

        def explota(*a, **k):
            raise llm.LLMError("el modelo se cayó")

        monkeypatch.setattr("agent.llm.ask_json", explota)
        code, datos = pedir(servidor, "/api/start", {"idea": "x", "platform": "web"})
        assert code == 200
        for _ in range(100):
            time.sleep(0.1)
            _, job = pedir(servidor, f"/api/job/{datos['job_id']}")
            if job["status"] != "running":
                break
        assert job["status"] == "error"
        assert "el modelo se cayó" in job["error"]

    def test_un_trabajo_inexistente_da_404(self, servidor):
        code, _ = pedir(servidor, "/api/job/noexiste")
        assert code == 404
