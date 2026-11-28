"""Developer-only access to the old South Jackson MySQL views, for the eval scripts in evals/ and the check scripts in dev_tools/ (never the service).

Connects as the read-only user (chatbot_ro), which can only SELECT from vw_chat_* views.
Every tool passes SQL with %s placeholders plus a params list; values are never pasted into the SQL text.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

import mysql.connector
from dotenv import load_dotenv

# The repo's .env is otherwise loaded only when the bot package is imported, which `eval_accuracy --check-truth` never does,
# so SJ_DB_USER was missing there however .env was filled in. Values already in the environment win.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def get_connection():
    return mysql.connector.connect(
        host=os.getenv("SJ_DB_HOST", "localhost"),
        port=int(os.getenv("SJ_DB_PORT", "3306")),
        user=os.environ["SJ_DB_USER"],
        password=os.environ["SJ_DB_PASSWORD"],
        database=os.getenv("SJ_DB_NAME", "southjacksonfurniture_dev"),
        charset="utf8mb4",
        connection_timeout=5,
    )


def _clean(value: Any) -> Any:
    """Make MySQL values JSON-friendly for the model."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def run_query(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        timeout_ms = int(os.getenv("SJ_DB_QUERY_TIMEOUT_MS", "15000"))
        cur.execute(f"SET SESSION MAX_EXECUTION_TIME={timeout_ms}")  # stops runaway queries
        cur.execute(sql, tuple(params))
        return [{k: _clean(v) for k, v in row.items()} for row in cur.fetchall()]
    finally:
        conn.close()
