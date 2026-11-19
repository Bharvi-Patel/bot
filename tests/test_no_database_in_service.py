"""The agreed design: the bot gets store data only through the storefront API and holds no store database credentials.
These tests fail if the service (bot/) or the ingest scripts (rag/) ever import a database driver or the developer-only
database helper (dev_tools/), directly or by loading any bot module."""
import ast
import pathlib
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVICE_FILES = sorted(p for d in ("bot", "rag") for p in (ROOT / d).rglob("*.py"))
FORBIDDEN = ("mysql", "pymysql", "MySQLdb", "dev_tools", "bot.db")


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            for a in node.names:                       # "from bot import db"
                yield f"{node.module}.{a.name}"


def test_there_are_files_to_check():
    assert len(SERVICE_FILES) > 20


@pytest.mark.parametrize("path", SERVICE_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_service_and_ingest_files_import_no_database_code(path):
    bad = [m for m in imported_modules(path) if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)]
    assert not bad, f"{path.relative_to(ROOT)} imports {bad}"


def test_the_old_database_module_is_gone_from_the_service_package():
    assert not (ROOT / "bot" / "db.py").exists()
    assert (ROOT / "dev_tools" / "db.py").exists()          # kept for the root-level eval scripts only


def test_importing_every_bot_module_never_loads_a_database_driver():
    code = textwrap.dedent("""
        import importlib, pkgutil, sys
        import bot
        for m in pkgutil.walk_packages(bot.__path__, "bot."):
            try:
                importlib.import_module(m.name)
            except ImportError:            # an optional dependency missing in this environment is fine
                pass
        loaded = [n for n in sys.modules if n.split(".")[0] in ("mysql", "pymysql", "MySQLdb", "dev_tools")]
        print("LOADED:" + ",".join(sorted(loaded)))
    """)
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert "LOADED:" in out.stdout and out.stdout.strip().endswith("LOADED:"), out.stdout
