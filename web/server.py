"""Servidor web del agente — solo librería estándar de Python.

Corre en tu PC y lo abres desde el celular. La entrevista ocurre por chat
en el navegador; el trabajo (llamar al modelo, generar el proyecto, subirlo
a GitHub) lo hace tu PC, así que usa tu sesión de Claude Code sin clave de API.

Sin dependencias nuevas a propósito: así no hay nada que instalar y el
servidor arranca en cualquier Python 3.10+.
"""
from __future__ import annotations

import json
import secrets
import socket
import threading
import traceback
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agent import llm
from agent.generator import generate_project
from agent.github_publish import (
    PublishError,
    publish,
    publishing_description,
    publishing_method,
)
from agent.interview import MAX_ROUNDS, Interview, demo_spec
from agent.spec import AppSpec

STATIC = Path(__file__).parent / "static"

# Estado en memoria. Es una herramienta personal de un solo usuario:
# si reinicias el servidor, las entrevistas en curso se pierden.
SESSIONS: dict[str, dict] = {}
JOBS: dict[str, dict] = {}
_lock = threading.Lock()

TOKEN = secrets.token_urlsafe(8)


# ---------------------------------------------------------------------------
# Trabajos en segundo plano
# ---------------------------------------------------------------------------
# Las llamadas al modelo tardan entre 15 y 60 segundos. Si el navegador del
# celular se queda esperando tanto rato, la pantalla se apaga y la petición
# se corta. Por eso todo lo lento se lanza como trabajo y el navegador
# pregunta por el resultado cada segundo.

def start_job(fn, note: str = "Trabajando...") -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        JOBS[job_id] = {"status": "running", "note": note, "result": None, "error": None}

    def runner() -> None:
        try:
            result = fn(lambda n: set_note(job_id, n))
            with _lock:
                JOBS[job_id].update(status="done", result=result)
        except (llm.LLMNotConfigured, llm.LLMError, PublishError) as exc:
            with _lock:
                JOBS[job_id].update(status="error", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            with _lock:
                JOBS[job_id].update(status="error", error=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=runner, daemon=True).start()
    return job_id


def set_note(job_id: str, note: str) -> None:
    with _lock:
        if job_id in JOBS:
            JOBS[job_id]["note"] = note


# ---------------------------------------------------------------------------
# Lógica de la entrevista
# ---------------------------------------------------------------------------

def spec_dict(spec: AppSpec) -> dict:
    data = asdict(spec)
    data["slug"] = spec.slug
    return data


def new_session(idea: str, platform: str) -> str:
    sid = uuid.uuid4().hex
    with _lock:
        SESSIONS[sid] = {
            "interview": Interview(idea=idea, platform=platform),
            "round": 0,
            "spec": None,
            "pending": [],  # preguntas que el navegador está contestando
        }
    return sid


def get_session(sid: str) -> dict:
    with _lock:
        sess = SESSIONS.get(sid)
    if sess is None:
        raise KeyError("Esa entrevista ya no existe. ¿Reiniciaste el servidor?")
    return sess


def advance(sess: dict, note) -> dict:
    """Pide la siguiente ronda de preguntas, o el plan si ya hay suficiente."""
    iv: Interview = sess["interview"]
    sess["round"] += 1
    round_no = sess["round"]

    if round_no > MAX_ROUNDS:
        return build_plan(sess, note)

    note("Pensando en qué preguntarte...")
    data = iv.next_questions(round_no)
    questions = data.get("questions") or []

    if (data.get("ready") and round_no > 1) or not questions:
        return build_plan(sess, note)

    sess["pending"] = questions
    return {
        "kind": "questions",
        "round": round_no,
        "max_rounds": MAX_ROUNDS,
        "thinking": data.get("thinking", ""),
        "questions": questions,
    }


def build_plan(sess: dict, note) -> dict:
    note("Diseñando la arquitectura de tu app...")
    iv: Interview = sess["interview"]
    spec = iv.build_spec()
    sess["spec"] = spec
    return {"kind": "plan", "spec": spec_dict(spec)}


def revise_plan(sess: dict, feedback: str, note) -> dict:
    note("Aplicando tus cambios...")
    iv: Interview = sess["interview"]
    spec = iv.revise_spec(sess["spec"], feedback)
    sess["spec"] = spec
    return {"kind": "plan", "spec": spec_dict(spec)}


def build_project(sess: dict, out_root: Path, do_publish: bool, private: bool, note) -> dict:
    spec: AppSpec = sess["spec"]
    note("Escribiendo el código de tu app...")
    project = generate_project(spec, out_root, use_llm=llm.available(), overwrite=True)

    result = {
        "kind": "built",
        "project": str(project),
        "slug": spec.slug,
        "platform": spec.platform,
        "needs_auth": spec.needs_auth,
        "repo_url": None,
        "publish_error": None,
    }

    if do_publish:
        note("Subiendo a GitHub...")
        try:
            result["repo_url"] = publish(project, project.name, spec.tagline, private=private)
        except PublishError as exc:
            result["publish_error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "CodeClone"
    out_root: Path = Path("proyectos")

    # -- utilidades ---------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:
        # Silencia el log por petición: el sondeo cada segundo lo llenaría.
        if "/api/job/" not in self.path:
            super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # el celular cerró la pestaña

    def _json(self, data: dict, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def _authorized(self, query: dict) -> bool:
        return self.headers.get("X-Token") == TOKEN or query.get("t", [None])[0] == TOKEN

    # -- rutas --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path

        if path in ("/", "/index.html"):
            if not self._authorized(query):
                self._send(403, b"Enlace invalido. Usa el enlace completo que imprime el servidor.", "text/plain; charset=utf-8")
                return
            html = (STATIC / "index.html").read_text(encoding="utf-8")
            html = html.replace("__TOKEN__", TOKEN)
            self._send(200, html.encode(), "text/html; charset=utf-8")
            return

        if not self._authorized(query):
            self._error(403, "No autorizado")
            return

        if path == "/api/health":
            self._json({
                "ok": True,
                "engine": llm.backend_description(),
                "engine_ready": llm.available(),
                "github": publishing_description(),
                "github_ready": publishing_method() is not None,
            })
            return

        if path.startswith("/api/job/"):
            job_id = path.rsplit("/", 1)[-1]
            with _lock:
                job = JOBS.get(job_id)
            if job is None:
                self._error(404, "Ese trabajo no existe")
                return
            self._json(job)
            return

        self._error(404, "Ruta no encontrada")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._authorized(parse_qs(parsed.query)):
            self._error(403, "No autorizado")
            return

        path = parsed.path
        body = self._body()

        try:
            if path == "/api/start":
                idea = (body.get("idea") or "").strip()
                platform = body.get("platform") or "web"
                if not idea:
                    self._error(400, "Cuéntame tu idea primero")
                    return
                if platform not in ("web", "mobile", "both"):
                    self._error(400, "Plataforma inválida")
                    return
                sid = new_session(idea, platform)
                sess = get_session(sid)
                job = start_job(lambda note: advance(sess, note), "Pensando en qué preguntarte...")
                self._json({"session_id": sid, "job_id": job})
                return

            if path == "/api/answers":
                sess = get_session(body.get("session_id") or "")
                answers = body.get("answers") or []
                pending = sess.get("pending") or []
                iv: Interview = sess["interview"]
                for i, q in enumerate(pending):
                    text = str(answers[i]).strip() if i < len(answers) else ""
                    iv.record(q.get("question", ""), text or "Sin preferencia, decide tú lo mejor")
                sess["pending"] = []
                job = start_job(lambda note: advance(sess, note))
                self._json({"job_id": job})
                return

            if path == "/api/skip":
                # "Ya tienes suficiente, constrúyela"
                sess = get_session(body.get("session_id") or "")
                sess["pending"] = []
                job = start_job(lambda note: build_plan(sess, note), "Diseñando la arquitectura...")
                self._json({"job_id": job})
                return

            if path == "/api/revise":
                sess = get_session(body.get("session_id") or "")
                feedback = (body.get("feedback") or "").strip()
                if not feedback:
                    self._error(400, "Dime qué cambiarías")
                    return
                if sess.get("spec") is None:
                    self._error(400, "Todavía no hay un plan que cambiar")
                    return
                job = start_job(lambda note: revise_plan(sess, feedback, note), "Aplicando tus cambios...")
                self._json({"job_id": job})
                return

            if path == "/api/build":
                sess = get_session(body.get("session_id") or "")
                if sess.get("spec") is None:
                    self._error(400, "Todavía no hay un plan que construir")
                    return
                do_publish = bool(body.get("publish"))
                private = bool(body.get("private", True))
                job = start_job(
                    lambda note: build_project(sess, self.out_root, do_publish, private, note),
                    "Escribiendo el código...",
                )
                self._json({"job_id": job})
                return

            if path == "/api/demo":
                platform = body.get("platform") or "both"
                sid = uuid.uuid4().hex
                spec = demo_spec(platform)
                with _lock:
                    SESSIONS[sid] = {"interview": Interview(), "round": 0, "spec": spec, "pending": []}
                self._json({"session_id": sid, "spec": spec_dict(spec), "kind": "plan"})
                return

        except KeyError as exc:
            self._error(410, str(exc).strip("'"))
            return

        self._error(404, "Ruta no encontrada")


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------

def lan_ip() -> str:
    """IP de este PC en la red local, para abrirla desde el celular."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No envía nada: solo obliga al sistema a elegir la interfaz de salida.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def serve(host: str = "0.0.0.0", port: int = 8777, out_root: Path | None = None) -> None:
    Handler.out_root = (out_root or Path("proyectos")).resolve()
    Handler.out_root.mkdir(parents=True, exist_ok=True)

    httpd = ThreadingHTTPServer((host, port), Handler)
    ip = lan_ip()
    url = f"http://{ip}:{port}/?t={TOKEN}"

    print()
    print("  Code Clone — servidor web")
    print("  " + "-" * 46)
    print(f"  Motor:     {llm.backend_description()}")
    print(f"  Proyectos: {Handler.out_root}")
    print()
    print("  Abre esto en tu celular (misma WiFi):")
    print(f"    {url}")
    print()
    print(f"  En este PC:  http://localhost:{port}/?t={TOKEN}")
    print()
    print("  El enlace lleva una clave: sin ella el servidor no responde.")
    print("  Ctrl+C para detenerlo.")
    print()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Servidor detenido.")
    finally:
        httpd.server_close()
