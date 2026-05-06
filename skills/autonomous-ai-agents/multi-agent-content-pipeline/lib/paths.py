"""
Resolves the on-disk root for Zeus pipeline state files.

Returns ``$HERMES_HOME/.hermes`` when ``HERMES_HOME`` is set (prod), otherwise
``~/.hermes`` (local dev). Anchoring to ``HERMES_HOME`` instead of ``HOME``
keeps writers (``pipeline_test.py`` invoked via Hermes' ``execute_code``,
which retargets ``HOME`` to ``$HERMES_HOME/home/``) and readers (the
``publish_watcher`` daemon, which inherits the login ``HOME``) pointing at the
same directory.
"""
from __future__ import annotations

import os
from pathlib import Path


def zeus_data_dir() -> Path:
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        return Path(hermes_home) / ".hermes"
    return Path(os.path.expanduser("~/.hermes"))


def zeus_data_path(*parts: str) -> Path:
    return zeus_data_dir().joinpath(*parts)
