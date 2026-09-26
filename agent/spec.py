"""Estructura de la especificación de la app.

Es el "contrato" entre el agente entrevistador y los generadores de
código: la entrevista produce un AppSpec y los generadores lo consumen.
"""
from __future__ import annotations

import json
import keyword
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Literal

Platform = Literal["web", "mobile", "both"]

# Tipos de campo soportados y cómo se traducen en cada capa.
# El último valor es un literal Python válido, para los datos de prueba.
FIELD_TYPES = {
    #  tipo       SQLAlchemy      Pydantic       TypeScript  input HTML       valor de prueba
    "string":   ("String(255)",  "str",         "string",   "text",          '"Texto de prueba"'),
    "text":     ("Text",         "str",         "string",   "textarea",      '"Descripción larga de prueba."'),
    "integer":  ("Integer",      "int",         "number",   "number",        "7"),
    "float":    ("Float",        "float",       "number",   "number",        "12500.5"),
    "boolean":  ("Boolean",      "bool",        "boolean",  "checkbox",      "True"),
    "date":     ("Date",         "dt.date",     "string",   "date",          '"2026-01-15"'),
    "datetime": ("DateTime",     "dt.datetime", "string",   "datetime-local", '"2026-01-15T10:30:00"'),
    "email":    ("String(255)",  "str",         "string",   "email",         '"prueba@ejemplo.com"'),
    "url":      ("String(500)",  "str",         "string",   "url",           '"https://ejemplo.com/x"'),
}


# Nombres que el generador ya usa internamente.
RESERVED_FIELDS = {"owner_id", "created_at", "model_config", "metadata", "registry"}
# Nombres de entidad que chocan con modelos internos.
RESERVED_ENTITIES = {"User", "Base", "Token", "Model"}


@dataclass
class Field:
    name: str
    type: str = "string"
    required: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        self.name = to_snake(self.name)
        if keyword.iskeyword(self.name) or self.name in RESERVED_FIELDS:
            self.name = f"{self.name}_value"
        if self.type not in FIELD_TYPES:
            self.type = "string"

    @property
    def sa_type(self) -> str:
        return FIELD_TYPES[self.type][0]

    @property
    def py_type(self) -> str:
        return FIELD_TYPES[self.type][1]

    @property
    def ts_type(self) -> str:
        return FIELD_TYPES[self.type][2]

    @property
    def input_type(self) -> str:
        return FIELD_TYPES[self.type][3]

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").capitalize()

    @property
    def test_literal(self) -> str:
        """Literal Python con un valor válido, para los tests generados."""
        return FIELD_TYPES[self.type][4]

    @property
    def wrong_type_literal(self) -> str:
        """Un valor del tipo equivocado, para probar que la validación lo rechaza."""
        return '"no-es-un-numero"' if self.type in ("integer", "float") else "None"


@dataclass
class Entity:
    name: str
    description: str = ""
    fields: list[Field] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = to_pascal(self.name)
        if self.name in RESERVED_ENTITIES:
            self.name = f"App{self.name}"
        self.fields = [f if isinstance(f, Field) else Field(**f) for f in self.fields]
        # "id" lo generamos siempre nosotros.
        seen: set[str] = set()
        unique = []
        for f in self.fields:
            if f.name != "id" and f.name not in seen:
                seen.add(f.name)
                unique.append(f)
        self.fields = unique

    @property
    def snake(self) -> str:
        return to_snake(self.name)

    @property
    def plural(self) -> str:
        s = self.snake
        if s.endswith("y") and not s.endswith(("ay", "ey", "oy", "uy")):
            return s[:-1] + "ies"
        if s.endswith(("s", "x", "z", "ch", "sh")):
            return s + "es"
        return s + "s"

    @property
    def title(self) -> str:
        return self.name

    @property
    def title_plural(self) -> str:
        return self.plural.replace("_", " ").title()

    @property
    def display_field(self) -> str:
        """Campo que se usa como 'título' en listas."""
        for preferred in ("name", "title", "nombre", "titulo"):
            for f in self.fields:
                if f.name == preferred:
                    return f.name
        for f in self.fields:
            if f.type in ("string", "email"):
                return f.name
        return "id"


@dataclass
class AppSpec:
    app_name: str
    tagline: str
    description: str
    platform: Platform
    target_users: str = ""
    features: list[str] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    needs_auth: bool = True
    color_primary: str = "#6366f1"
    style_notes: str = ""
    integrations: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.entities = [e if isinstance(e, Entity) else Entity(**e) for e in self.entities]
        # Evita entidades duplicadas (mismo nombre tras normalizar).
        seen: set[str] = set()
        self.entities = [e for e in self.entities if not (e.name in seen or seen.add(e.name))]
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color_primary or ""):
            self.color_primary = "#6366f1"

    @property
    def slug(self) -> str:
        return to_kebab(self.app_name)

    @property
    def wants_web(self) -> bool:
        return self.platform in ("web", "both")

    @property
    def wants_mobile(self) -> bool:
        return self.platform in ("mobile", "both")

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "AppSpec":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in allowed})

    @classmethod
    def from_json_file(cls, path) -> "AppSpec":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


# ---------- utilidades de nombres ----------

def _deaccent(s: str) -> str:
    """'NotaFácil' -> 'NotaFacil', 'Niños' -> 'Ninos'.

    Sin esto los nombres con tildes o ñ perdían letras: 'nota-f-cil'.
    """
    normalized = unicodedata.normalize("NFKD", s)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def _words(s: str) -> list[str]:
    s = _deaccent(s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"[^A-Za-z0-9]+", " ", s)
    return [w for w in s.strip().split() if w]


def to_snake(s: str) -> str:
    out = "_".join(w.lower() for w in _words(s)) or "item"
    return out if not out[0].isdigit() else f"f_{out}"


def to_pascal(s: str) -> str:
    out = "".join(w[:1].upper() + w[1:].lower() for w in _words(s)) or "Item"
    return out if not out[0].isdigit() else f"E{out}"


def to_kebab(s: str) -> str:
    return "-".join(w.lower() for w in _words(s)) or "my-app"
