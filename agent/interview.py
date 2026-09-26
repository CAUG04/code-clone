"""Agente entrevistador.

Antes de escribir una sola línea de código, conversa con el usuario,
hace preguntas inteligentes (con opciones sugeridas) para mejorar la
idea, y al final produce un AppSpec.
"""
from __future__ import annotations

import json
from typing import Callable

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import llm
from .spec import AppSpec, Entity, Field

console = Console()

MAX_ROUNDS = 4          # rondas máximas de preguntas
QUESTIONS_PER_ROUND = 3  # preguntas por ronda

OTHER = "✏️  Otra (escribir mi respuesta)"
SKIP = "🤷 Tú decide (lo que sea mejor)"

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

INTERVIEWER_SYSTEM = """Eres un Product Manager y arquitecto de software senior
que trabaja dentro de una plataforma que genera aplicaciones a partir de
lenguaje natural. Tu trabajo es entrevistar al usuario ANTES de construir
su app, para entender qué necesita y proponerle mejoras que no había
considerado.

Reglas para tus preguntas:
- Escribe en español, tono cercano y claro, sin jerga innecesaria.
- Haz preguntas que cambien de verdad lo que se va a construir:
  usuarios objetivo, funcionalidades clave, datos que se guardan,
  roles/permisos, flujo principal, monetización, estilo visual,
  integraciones (pagos, email, mapas, IA...), casos borde.
- Cada pregunta debe traer entre 2 y 4 opciones concretas y útiles
  (el usuario siempre podrá escribir otra respuesta).
- Propón ideas: al menos una opción por ronda debe sugerir algo que
  mejore el producto y que el usuario probablemente no pidió.
- No repitas preguntas ya respondidas.
- Cuando tengas suficiente información para construir un MVP sólido,
  marca "ready": true.
"""

QUESTIONS_FORMAT = """Devuelve JSON con esta forma exacta:
{
  "ready": false,
  "thinking": "1-2 frases sobre qué te falta entender",
  "questions": [
    {
      "question": "texto de la pregunta",
      "why": "por qué importa (una frase corta)",
      "options": ["opción 1", "opción 2", "opción 3"],
      "multi": false
    }
  ]
}
"multi": true si tiene sentido elegir varias opciones a la vez.
Máximo %d preguntas. Si "ready" es true, "questions" puede ir vacío.
""" % QUESTIONS_PER_ROUND

QUESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "ready": {"type": "boolean"},
        "thinking": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "why": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "multi": {"type": "boolean"},
                },
                "required": ["question", "options"],
            },
        },
    },
    "required": ["ready", "questions"],
}

FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "type": {
            "type": "string",
            "enum": ["string", "text", "integer", "float", "boolean",
                     "date", "datetime", "email", "url"],
        },
        "required": {"type": "boolean"},
        "description": {"type": "string"},
    },
    "required": ["name", "type"],
}

SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "app_name": {"type": "string"},
        "tagline": {"type": "string"},
        "description": {"type": "string"},
        "platform": {"type": "string", "enum": ["web", "mobile", "both"]},
        "target_users": {"type": "string"},
        "features": {"type": "array", "items": {"type": "string"}},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "fields": {"type": "array", "items": FIELD_SCHEMA},
                },
                "required": ["name", "fields"],
            },
        },
        "needs_auth": {"type": "boolean"},
        "color_primary": {"type": "string"},
        "style_notes": {"type": "string"},
        "integrations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["app_name", "tagline", "description", "entities", "needs_auth"],
}

SPEC_SYSTEM = """Eres un arquitecto de software. A partir de la entrevista
con el usuario, diseñas la especificación de un MVP funcional.

Reglas:
- "entities" son los modelos de datos (tablas). Entre 2 y 6 entidades.
  NO incluyas una entidad de usuarios si needs_auth es true: el sistema
  de autenticación ya la crea.
- Tipos de campo permitidos: string, text, integer, float, boolean,
  date, datetime, email, url.
- No incluyas el campo "id" (se crea automáticamente).
- Nombres de entidades en inglés, PascalCase singular (ej. "Product").
- Nombres de campos en inglés, snake_case (ej. "unit_price").
- "features" son frases cortas en español, las funcionalidades clave.
- "color_primary" es un color hex que encaje con el estilo pedido.
- "app_name" corto y memorable.
"""

SPEC_FORMAT = """Devuelve JSON con esta forma exacta:
{
  "app_name": "string",
  "tagline": "frase corta de valor",
  "description": "2-3 frases",
  "platform": "web" | "mobile" | "both",
  "target_users": "string",
  "features": ["string", ...],
  "entities": [
    {
      "name": "PascalCase",
      "description": "string",
      "fields": [
        {"name": "snake_case", "type": "string", "required": true, "description": "string"}
      ]
    }
  ],
  "needs_auth": true,
  "color_primary": "#RRGGBB",
  "style_notes": "string",
  "integrations": ["string", ...]
}
"""


# ---------------------------------------------------------------------------
# Entrevista
# ---------------------------------------------------------------------------

class Interview:
    def __init__(self, idea: str = "", platform: str = "") -> None:
        self.idea: str = idea
        self.platform: str = platform or "web"
        self._platform_preset = bool(platform)
        self.qa: list[dict] = []  # [{"question":..., "answer":...}]

    # -- helpers ------------------------------------------------------------

    def transcript(self) -> str:
        lines = [
            f"Idea inicial del usuario: {self.idea}",
            f"Plataforma elegida: {self.platform}",
        ]
        for i, item in enumerate(self.qa, 1):
            lines.append(f"P{i}: {item['question']}")
            lines.append(f"R{i}: {item['answer']}")
        return "\n".join(lines)

    # -- pasos --------------------------------------------------------------

    def ask_idea(self) -> None:
        if self.idea.strip():
            console.print(f"[dim]Idea: {self.idea}[/dim]")
            return
        console.print(
            Panel.fit(
                "[bold]¿Qué quieres construir hoy?[/bold]\n"
                "Descríbelo con tus palabras, como se lo contarías a un amigo.\n"
                "[dim]Ej: 'Una app para que los gimnasios de mi barrio gestionen "
                "sus clientes y membresías'[/dim]",
                border_style="magenta",
            )
        )
        while not self.idea.strip():
            self.idea = questionary.text("💡 Tu idea:").ask() or ""

    def ask_platform(self) -> None:
        if self._platform_preset:
            console.print(f"[dim]Plataforma: {self.platform}[/dim]")
            return
        choice = questionary.select(
            "¿Qué tipo de software quieres?",
            choices=[
                questionary.Choice("🌐 Web app (se abre en el navegador)", "web"),
                questionary.Choice("📱 App móvil (iOS y Android)", "mobile"),
                questionary.Choice("🌐📱 Ambas (web + móvil, mismo backend)", "both"),
            ],
        ).ask()
        self.platform = choice or "web"

    def record(self, question: str, answer: str) -> None:
        """Guarda una respuesta. Lo usa tanto la terminal como la web."""
        self.qa.append({"question": question, "answer": answer})

    def next_questions(self, round_no: int) -> dict:
        """Le pide al modelo la siguiente ronda de preguntas.

        Es la parte sin interfaz: la terminal y la web la comparten.
        Devuelve {"ready": bool, "thinking": str, "questions": [...]}.
        """
        data = llm.ask_json(
            INTERVIEWER_SYSTEM + "\n\n" + QUESTIONS_FORMAT,
            self.transcript()
            + f"\n\nEsta es la ronda {round_no} de máximo {MAX_ROUNDS}. "
            "Genera las siguientes preguntas.",
            schema=QUESTIONS_SCHEMA,
        )
        if not isinstance(data, dict):
            return {"ready": True, "questions": []}
        data.setdefault("questions", [])
        data["questions"] = (data["questions"] or [])[:QUESTIONS_PER_ROUND]
        return data

    def ask_round(self, round_no: int) -> bool:
        """Hace una ronda de preguntas en la terminal. True si ya hay suficiente info."""
        with console.status("[magenta]Pensando en qué preguntarte...[/magenta]"):
            data = self.next_questions(round_no)

        if data.get("ready") and round_no > 1:
            return True

        questions = data.get("questions") or []
        if not questions:
            return True

        if data.get("thinking"):
            console.print(f"\n[dim italic]🤔 {data['thinking']}[/dim italic]")

        for q in questions:
            answer = self._ask_one(q)
            if answer is None:  # Ctrl+C
                raise KeyboardInterrupt
            self.record(q.get("question", ""), answer)

        return False

    def _ask_one(self, q: dict) -> str | None:
        text = q.get("question", "").strip()
        why = q.get("why", "").strip()
        options = [str(o) for o in (q.get("options") or [])][:4]
        multi = bool(q.get("multi"))

        console.print()
        console.print(f"[bold cyan]❓ {text}[/bold cyan]")
        if why:
            console.print(f"   [dim]{why}[/dim]")

        if not options:
            return questionary.text("   Respuesta:").ask()

        if multi:
            picked = questionary.checkbox(
                "   Elige una o varias (espacio para marcar, enter para seguir):",
                choices=options + [OTHER],
            ).ask()
            if picked is None:
                return None
            if OTHER in picked:
                picked.remove(OTHER)
                extra = questionary.text("   Escribe tu respuesta:").ask() or ""
                if extra.strip():
                    picked.append(extra.strip())
            return ", ".join(picked) if picked else "Sin preferencia, decide tú"

        picked = questionary.select(
            "   Elige:", choices=options + [OTHER, SKIP]
        ).ask()
        if picked is None:
            return None
        if picked == OTHER:
            return questionary.text("   Escribe tu respuesta:").ask() or "Sin respuesta"
        if picked == SKIP:
            return "Sin preferencia, decide tú lo mejor para el producto"
        return picked

    def build_spec(self) -> AppSpec:
        with console.status("[magenta]Diseñando la arquitectura de tu app...[/magenta]"):
            data = llm.ask_json(
                SPEC_SYSTEM + "\n\n" + SPEC_FORMAT,
                self.transcript(),
                schema=SPEC_SCHEMA,
                max_tokens=4000,
            )
        data["platform"] = self.platform
        return AppSpec.from_dict(data)

    def revise_spec(self, spec: AppSpec, feedback: str) -> AppSpec:
        with console.status("[magenta]Aplicando tus cambios...[/magenta]"):
            data = llm.ask_json(
                SPEC_SYSTEM + "\n\n" + SPEC_FORMAT,
                self.transcript()
                + "\n\nEspecificación actual:\n"
                + spec.to_json()
                + "\n\nEl usuario pide estos cambios:\n"
                + feedback
                + "\n\nDevuelve la especificación completa actualizada.",
                schema=SPEC_SCHEMA,
                max_tokens=4000,
            )
        data["platform"] = self.platform
        return AppSpec.from_dict(data)

    # -- flujo completo -----------------------------------------------------

    def run(self) -> AppSpec:
        self.ask_idea()
        self.ask_platform()

        console.print(
            "\n[magenta]Genial. Antes de construir, te haré algunas preguntas "
            "para que el resultado sea mucho mejor.[/magenta]"
        )
        for r in range(1, MAX_ROUNDS + 1):
            if self.ask_round(r):
                break
            if r < MAX_ROUNDS:
                more = questionary.select(
                    "¿Seguimos afinando?",
                    choices=[
                        questionary.Choice("Sí, hazme más preguntas", True),
                        questionary.Choice("No, ya tienes suficiente: ¡constrúyela!", False),
                    ],
                ).ask()
                if not more:
                    break

        spec = self.build_spec()
        return confirm_spec_loop(spec, self.revise_spec)


# ---------------------------------------------------------------------------
# Mostrar y confirmar spec
# ---------------------------------------------------------------------------

def show_spec(spec: AppSpec) -> None:
    platform_label = {"web": "🌐 Web", "mobile": "📱 Móvil", "both": "🌐📱 Web + Móvil"}
    console.print()
    console.print(
        Panel(
            f"[bold]{spec.app_name}[/bold] — {spec.tagline}\n\n"
            f"{spec.description}\n\n"
            f"[bold]Plataforma:[/bold] {platform_label.get(spec.platform, spec.platform)}\n"
            f"[bold]Usuarios:[/bold] {spec.target_users}\n"
            f"[bold]Login de usuarios:[/bold] {'Sí' if spec.needs_auth else 'No'}\n"
            f"[bold]Color principal:[/bold] {spec.color_primary}\n"
            f"[bold]Estilo:[/bold] {spec.style_notes}",
            title="📋 Plan de tu app",
            border_style="green",
        )
    )
    if spec.features:
        console.print("[bold]Funcionalidades:[/bold]")
        for f in spec.features:
            console.print(f"  • {f}")

    for ent in spec.entities:
        t = Table(title=f"🗂  {ent.name} — {ent.description}", show_lines=False)
        t.add_column("Campo")
        t.add_column("Tipo")
        t.add_column("Obligatorio")
        t.add_column("Descripción")
        for f in ent.fields:
            t.add_row(f.name, f.type, "sí" if f.required else "no", f.description)
        console.print(t)

    if spec.integrations:
        console.print(
            "[bold]Integraciones sugeridas (para una siguiente fase):[/bold] "
            + ", ".join(spec.integrations)
        )


def confirm_spec_loop(
    spec: AppSpec,
    reviser: Callable[[AppSpec, str], AppSpec] | None,
    auto: bool = False,
) -> AppSpec:
    """Muestra el plan y pide confirmación.

    Con auto=True solo lo muestra y sigue, sin preguntar: es el modo para
    sesiones sin terminal (nube, CI, o manejado por otro agente).
    """
    if auto:
        show_spec(spec)
        return spec

    while True:
        show_spec(spec)
        options = [questionary.Choice("✅ Se ve bien, ¡construye!", "ok")]
        if reviser:
            options.append(questionary.Choice("✏️  Quiero cambiar algo", "edit"))
        options.append(questionary.Choice("❌ Cancelar", "cancel"))
        action = questionary.select("¿Qué hacemos?", choices=options).ask()
        if action == "ok":
            return spec
        if action == "edit" and reviser:
            feedback = questionary.text(
                "¿Qué cambiarías? (ej. 'agrega una entidad de Reseñas con estrellas')"
            ).ask()
            if feedback and feedback.strip():
                spec = reviser(spec, feedback.strip())
            continue
        raise KeyboardInterrupt


# ---------------------------------------------------------------------------
# Modo demo (sin API key) — útil para probar los generadores
# ---------------------------------------------------------------------------

def demo_spec(platform: str = "both") -> AppSpec:
    return AppSpec(
        app_name="GymFlow",
        tagline="Gestiona tu gimnasio sin hojas de cálculo",
        description=(
            "App para gimnasios de barrio que permite registrar clientes, "
            "planes de membresía y la asistencia diaria."
        ),
        platform=platform,  # type: ignore[arg-type]
        target_users="Dueños y recepcionistas de gimnasios pequeños",
        features=[
            "Registro de clientes",
            "Planes de membresía con precio y duración",
            "Control de asistencia diaria",
        ],
        entities=[
            Entity(
                name="Member",
                description="Cliente del gimnasio",
                fields=[
                    Field("full_name", "string", True, "Nombre completo"),
                    Field("email", "email", False, "Correo"),
                    Field("phone", "string", False, "Celular"),
                    Field("active", "boolean", True, "¿Membresía activa?"),
                ],
            ),
            Entity(
                name="Plan",
                description="Plan de membresía",
                fields=[
                    Field("name", "string", True, "Nombre del plan"),
                    Field("price", "float", True, "Precio mensual"),
                    Field("duration_days", "integer", True, "Duración en días"),
                ],
            ),
            Entity(
                name="Attendance",
                description="Registro de asistencia",
                fields=[
                    Field("member_name", "string", True, "Cliente"),
                    Field("check_in", "datetime", True, "Hora de entrada"),
                    Field("notes", "text", False, "Notas"),
                ],
            ),
        ],
        needs_auth=True,
        color_primary="#f97316",
        style_notes="Energético, moderno, tarjetas redondeadas",
        integrations=["Pagos con Wompi/Stripe", "Recordatorios por WhatsApp"],
    )
