"""Salida por consola, con `rich` si está instalado y sin él si no.

Así el núcleo (spec, generator, la web) funciona sin dependencias de
interfaz, y las pruebas pueden importarlo en cualquier entorno.
"""
from __future__ import annotations

import contextlib
import re

try:  # pragma: no cover - depende del entorno
    from rich.console import Console as _RichConsole
    from rich.panel import Panel  # noqa: F401  (se re-exporta)
    from rich.table import Table  # noqa: F401  (se re-exporta)

    console = _RichConsole()
    HAS_RICH = True

except ImportError:  # pragma: no cover - camino sin rich
    HAS_RICH = False

    _TAGS = re.compile(r"\[/?[a-zA-Z#0-9_. ]+\]")

    def _plain(value: object) -> str:
        return _TAGS.sub("", str(value))

    class _Console:
        def print(self, *args, **kwargs) -> None:
            print(*[_plain(a) for a in args] if args else [])

        def status(self, *args, **kwargs):
            if args:
                print(_plain(args[0]))
            return contextlib.nullcontext()

    console = _Console()

    class Panel:  # type: ignore[no-redef]
        def __init__(self, body: object = "", title: str = "", **kwargs) -> None:
            self.body, self.title = body, title

        def __str__(self) -> str:
            head = f"\n== {_plain(self.title)} ==\n" if self.title else "\n"
            return head + _plain(self.body)

        @classmethod
        def fit(cls, body: object = "", **kwargs) -> "Panel":
            return cls(body, **kwargs)

    class Table:  # type: ignore[no-redef]
        def __init__(self, title: str = "", **kwargs) -> None:
            self.title = title
            self._cols: list[str] = []
            self._rows: list[tuple[str, ...]] = []

        def add_column(self, name: str = "", **kwargs) -> None:
            self._cols.append(_plain(name))

        def add_row(self, *cells: object) -> None:
            self._rows.append(tuple(_plain(c) for c in cells))

        def __str__(self) -> str:
            out = [_plain(self.title)] if self.title else []
            if self._cols:
                out.append("  " + " | ".join(self._cols))
            out += ["  " + " | ".join(r) for r in self._rows]
            return "\n".join(out)
