#!/usr/bin/env python3
"""Arranca la web de Code Clone en tu PC para abrirla desde el celular.

    python serve.py                 # http://<ip-de-tu-pc>:8777
    python serve.py --port 9000
    python serve.py --out ~/mis-apps

El celular y el PC tienen que estar en la misma WiFi. Para entrar desde
fuera de casa, levanta un túnel: cloudflared tunnel --url http://localhost:8777
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from web.server import serve  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Web de Code Clone (corre en tu PC)")
    p.add_argument("--port", type=int, default=8777, help="Puerto (default: 8777)")
    p.add_argument("--host", default="0.0.0.0", help="Interfaz (default: todas)")
    p.add_argument("--out", type=Path, default=Path("proyectos"), help="Dónde dejar los proyectos")
    args = p.parse_args()
    serve(host=args.host, port=args.port, out_root=args.out)


if __name__ == "__main__":
    main()
