#!/usr/bin/env python3
"""Fail when a code-read environment variable is absent from user docs."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
SYSTEM_ALLOWLIST = {"HOME", "PATH", "PYTHONPATH"}
ENV_HELPERS = {"_int", "_float", "_bool", "_categories", "_env_number"}


def _literal_argument(node: ast.Call) -> str:
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return node.args[0].value
    return ""


def environment_variables(path: Path) -> set[str]:
    """Find literal keys read through os.environ/os.getenv and config helpers."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    variables: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            is_environ_get = (
                isinstance(function, ast.Attribute)
                and function.attr == "get"
                and isinstance(function.value, ast.Attribute)
                and isinstance(function.value.value, ast.Name)
                and function.value.value.id == "os"
                and function.value.attr == "environ"
            )
            is_getenv = (
                isinstance(function, ast.Attribute)
                and function.attr == "getenv"
                and isinstance(function.value, ast.Name)
                and function.value.id == "os"
            )
            is_config_helper = isinstance(function, ast.Name) and function.id in ENV_HELPERS
            if is_environ_get or is_getenv or is_config_helper:
                variable = _literal_argument(node)
                if PATTERN.fullmatch(variable):
                    variables.add(variable)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and PATTERN.fullmatch(node.slice.value)
        ):
            variables.add(node.slice.value)
    return variables


def main() -> int:
    used: set[str] = set()
    for path in (ROOT / "scripts" / "nrw_events").rglob("*.py"):
        used.update(environment_variables(path))
    used -= SYSTEM_ALLOWLIST
    missing_by_document = {}
    for relative_path in (".env.example", "README.md", "SKILL.md"):
        documented = set(PATTERN.findall((ROOT / relative_path).read_text(encoding="utf-8")))
        missing = sorted(used - documented)
        if missing:
            missing_by_document[relative_path] = missing
    if missing_by_document:
        for document, missing in missing_by_document.items():
            print(f"Undocumented environment settings in {document}: {', '.join(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
