"""Capa de acceso al modelo.

Soporta dos backends y elige solo:

  1. "cli"  -> usa tu Claude Code local (`claude -p`). No necesita clave de
               API: aprovecha la sesión con la que ya iniciaste sesión.
  2. "api"  -> usa la API de Anthropic con ANTHROPIC_API_KEY.

Puedes forzar uno con la variable de entorno LLM_BACKEND=cli|api.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

# El modelo a usar. Con el backend "cli" acepta alias ("sonnet", "opus").
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "")
# Tiempo máximo por llamada al CLI (segundos).
CLI_TIMEOUT = int(os.environ.get("CLAUDE_CLI_TIMEOUT", "300"))

JSON_RULE = (
    "\n\nIMPORTANTE: responde ÚNICAMENTE con JSON válido, sin texto adicional "
    "antes ni después, y sin bloques de código markdown."
)


class LLMNotConfigured(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Detección de backend
# ---------------------------------------------------------------------------

def claude_cli_path() -> str | None:
    return shutil.which("claude")


def detect_backend() -> str:
    """Devuelve 'cli', 'api' o '' si no hay ninguno disponible."""
    forced = os.environ.get("LLM_BACKEND", "").strip().lower()
    if forced in ("cli", "api"):
        return forced
    if claude_cli_path():
        return "cli"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    return ""


def available() -> bool:
    return bool(detect_backend())


def backend_description() -> str:
    backend = detect_backend()
    if backend == "cli":
        return "Claude Code local (sin clave de API)"
    if backend == "api":
        return "API de Anthropic (ANTHROPIC_API_KEY)"
    return "ninguno"


def _require_backend() -> str:
    backend = detect_backend()
    if backend == "cli" and not claude_cli_path():
        raise LLMNotConfigured(
            "Pediste LLM_BACKEND=cli pero no encontré el comando `claude` en el PATH.\n"
            "Instálalo con: npm install -g @anthropic-ai/claude-code"
        )
    if backend == "api" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise LLMNotConfigured(
            "Pediste LLM_BACKEND=api pero no hay ANTHROPIC_API_KEY en el entorno."
        )
    if not backend:
        raise LLMNotConfigured(
            "No encontré cómo hablar con el modelo. Tienes dos opciones:\n"
            "  1. Instalar Claude Code e iniciar sesión (no necesitas clave de API):\n"
            "       npm install -g @anthropic-ai/claude-code\n"
            "       claude    (inicia sesión y luego sal con /exit)\n"
            "  2. Poner ANTHROPIC_API_KEY en tu archivo .env"
        )
    return backend


# ---------------------------------------------------------------------------
# Backend 1: Claude Code local
# ---------------------------------------------------------------------------

# Flags que mejoran el aislamiento pero que no existen en versiones viejas del
# CLI. Si el CLI los rechaza, reintentamos sin ellos.
OPTIONAL_FLAGS = ["--permission-mode", "dontAsk", "--restricted"]

_cli_supports_optional: bool | None = None
_cli_supports_schema: bool | None = None


def _cli_call(system: str, user: str, schema: dict | None = None) -> tuple[str, Any]:
    """Llama a `claude -p`. Devuelve (texto, salida_estructurada_o_None)."""
    global _cli_supports_optional, _cli_supports_schema

    exe = claude_cli_path()
    if not exe:
        raise LLMNotConfigured("No encontré el comando `claude` en el PATH.")

    use_optional = _cli_supports_optional is not False
    use_schema = schema is not None and _cli_supports_schema is not False

    for attempt in range(3):
        cmd = [exe, "-p", user, "--system-prompt", system, "--output-format", "json"]
        if DEFAULT_MODEL:
            cmd += ["--model", DEFAULT_MODEL]
        if use_schema:
            cmd += ["--json-schema", json.dumps(schema)]
        if use_optional:
            cmd += OPTIONAL_FLAGS

        # Corremos en una carpeta vacía para que el CLI no cargue el CLAUDE.md
        # ni la configuración del proyecto en el que estés parado.
        with tempfile.TemporaryDirectory() as workdir:
            try:
                res = subprocess.run(
                    cmd, cwd=workdir, capture_output=True, text=True, timeout=CLI_TIMEOUT
                )
            except subprocess.TimeoutExpired:
                raise LLMError(
                    f"Claude Code no respondió en {CLI_TIMEOUT}s. "
                    "Puedes subir el límite con CLAUDE_CLI_TIMEOUT."
                )

        stderr = (res.stderr or "").strip()

        # Flag no soportada por esta versión del CLI -> reintenta sin ella.
        if res.returncode != 0 and _is_unknown_option(stderr):
            if use_schema:
                _cli_supports_schema = False
                use_schema = False
                continue
            if use_optional:
                _cli_supports_optional = False
                use_optional = False
                continue

        if res.returncode != 0:
            raise LLMError(_cli_error_message(stderr or res.stdout))

        if use_optional:
            _cli_supports_optional = True
        if use_schema:
            _cli_supports_schema = True

        return _parse_cli_output(res.stdout)

    raise LLMError("No pude ejecutar Claude Code después de varios intentos.")


def _is_unknown_option(stderr: str) -> bool:
    low = stderr.lower()
    return "unknown option" in low or "unknown argument" in low or "unknown command" in low


def _cli_error_message(raw: str) -> str:
    low = (raw or "").lower()
    if "login" in low or "authenticat" in low or "oauth" in low:
        return (
            "Claude Code no tiene sesión iniciada. Abre una terminal, corre `claude`, "
            "inicia sesión, sal con /exit y vuelve a intentar.\n\nDetalle: " + raw[:500]
        )
    if "rate limit" in low or "usage limit" in low:
        return (
            "Alcanzaste el límite de uso de tu plan de Claude. Espera a que se "
            "reinicie y vuelve a intentar.\n\nDetalle: " + raw[:500]
        )
    return f"Claude Code devolvió un error:\n{raw[:1000]}"


def _parse_cli_output(stdout: str) -> tuple[str, Any]:
    stdout = (stdout or "").strip()
    if not stdout:
        raise LLMError("Claude Code no devolvió ninguna respuesta.")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        # --output-format json falló por algún motivo; usamos el texto crudo.
        return stdout, None

    if isinstance(payload, dict):
        if payload.get("is_error"):
            raise LLMError(_cli_error_message(str(payload.get("result", ""))))
        text = payload.get("result") or ""
        return str(text).strip(), payload.get("structured_output")
    return stdout, None


# ---------------------------------------------------------------------------
# Backend 2: API de Anthropic
# ---------------------------------------------------------------------------

def _api_call(system: str, user: str, max_tokens: int, temperature: float) -> str:
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise LLMNotConfigured(
            "Para usar la API necesitas el paquete `anthropic`: pip install anthropic"
        ) from exc

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=DEFAULT_MODEL or "claude-sonnet-4-5",
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def ask(system: str, user: str, *, max_tokens: int = 2000, temperature: float = 0.6) -> str:
    backend = _require_backend()
    if backend == "cli":
        text, _ = _cli_call(system, user)
        return text
    return _api_call(system, user, max_tokens, temperature)


def ask_json(
    system: str,
    user: str,
    *,
    schema: dict | None = None,
    max_tokens: int = 2000,
    temperature: float = 0.4,
) -> Any:
    """Pide una respuesta y la devuelve parseada como JSON.

    Con el backend "cli" y un `schema`, usa la salida estructurada del CLI,
    que es mucho más confiable que parsear texto.
    """
    backend = _require_backend()

    if backend == "cli":
        text, structured = _cli_call(system + JSON_RULE, user, schema)
        if structured is not None:
            return structured
        return _extract_json(text)

    text = _api_call(system + JSON_RULE, user, max_tokens, temperature)
    return _extract_json(text)


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise LLMError(
            "El modelo no devolvió JSON válido. Respuesta recibida:\n" + text[:500]
        )
