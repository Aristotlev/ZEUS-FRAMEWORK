"""Smoke tests for the Redis + pgvector stack glue."""
from __future__ import annotations

import importlib
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_pg_url_uses_env_overrides(monkeypatch):
    monkeypatch.setenv("MNEMOSYNE_PG_HOST", "db.example.com")
    monkeypatch.setenv("MNEMOSYNE_PG_PORT", "6543")
    monkeypatch.setenv("MNEMOSYNE_PG_DB", "zeus_test")
    monkeypatch.setenv("MNEMOSYNE_PG_USER", "zeus")
    monkeypatch.setenv("MNEMOSYNE_PG_PASSWORD", "supersecret")

    if "stack.hermes_stack" in sys.modules:
        del sys.modules["stack.hermes_stack"]
    mod = importlib.import_module("stack.hermes_stack")

    assert mod.PG_URL["host"] == "db.example.com"
    assert mod.PG_URL["port"] == 6543
    assert mod.PG_URL["database"] == "zeus_test"
    assert mod.PG_URL["user"] == "zeus"
    assert mod.PG_URL["password"] == "supersecret"


def test_default_password_is_a_placeholder():
    """The default password must NOT be a real-looking secret."""
    for var in (
        "MNEMOSYNE_PG_HOST", "MNEMOSYNE_PG_PORT", "MNEMOSYNE_PG_DB",
        "MNEMOSYNE_PG_USER", "MNEMOSYNE_PG_PASSWORD",
    ):
        os.environ.pop(var, None)

    if "stack.hermes_stack" in sys.modules:
        del sys.modules["stack.hermes_stack"]
    mod = importlib.import_module("stack.hermes_stack")

    assert mod.PG_URL["password"] == "change-me-in-prod"
