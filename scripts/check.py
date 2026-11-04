#!/usr/bin/env python3
"""Run every check the CI runs: lint, Python tests and browser-code tests.

Uses only what is installed and says what it skipped. Exit status is non-zero if any step fails.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def has_module(name: str) -> bool:
    return subprocess.run([sys.executable, "-c", f"import {name}"], capture_output=True, check=False).returncode == 0


def ruff_command() -> list[str] | None:
    """The installed ruff, whether it is a binary (pipx) or a module of this interpreter."""
    binary = shutil.which("ruff")
    if binary:
        return [binary]
    return [sys.executable, "-m", "ruff"] if has_module("ruff") else None


def steps() -> list[tuple[str, list[str] | None, str]]:
    """``(title, command, reason it is skipped)``; ``command`` is ``None`` when it cannot run."""
    ruff = ruff_command()
    node = shutil.which("node")
    return [
        ("ruff check", ruff and [*ruff, "check", "."], "ruff no está instalado (pipx install ruff o pip install ruff)"),
        ("ruff format --check", ruff and [*ruff, "format", "--check", "."], "ruff no está instalado"),
        ("pruebas de Python", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], ""),
        (
            "pruebas del navegador",
            node and [node, "--test"],
            "node no está instalado (Node 20 o superior)",
        ),
    ]


def main() -> int:
    failed, skipped = [], []
    for title, command, reason in steps():
        print(f"\n=== {title} ===", flush=True)
        if command is None:
            print(f"(omitido: {reason})")
            skipped.append(title)
            continue
        if subprocess.run(command, cwd=ROOT, check=False).returncode != 0:
            failed.append(title)
    print("\n--- resumen ---")
    if skipped:
        print("omitidos:", ", ".join(skipped))
    if failed:
        print("FALLAN:", ", ".join(failed))
        return 1
    print("todo correcto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
