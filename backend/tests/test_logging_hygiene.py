"""Static guard over every log call in the application."""

import ast
import logging
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

RESERVED = set(logging.LogRecord("x", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def test_no_log_call_uses_a_reserved_logrecord_attribute_in_extra() -> None:
    """``extra={"created": ...}`` raises KeyError inside logging and turns a request into a 500."""
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict):
                    offenders += [
                        f"{path.relative_to(APP_DIR.parent)}:{key.lineno} uses {key.value!r}"
                        for key in keyword.value.keys
                        if isinstance(key, ast.Constant) and key.value in RESERVED
                    ]
    assert offenders == []
