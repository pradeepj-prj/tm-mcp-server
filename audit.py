"""SQLite-backed audit logger for MCP tool invocations."""

import json
import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    request_id      TEXT,
    session_id      TEXT,
    client_name     TEXT,
    client_version  TEXT,
    tool_name       TEXT    NOT NULL,
    parameters      TEXT,
    success         INTEGER NOT NULL,
    error_msg       TEXT,
    duration_ms     REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_timestamp  ON tool_calls (timestamp);
CREATE INDEX IF NOT EXISTS idx_session_id ON tool_calls (session_id);
CREATE INDEX IF NOT EXISTS idx_tool_name  ON tool_calls (tool_name);
CREATE INDEX IF NOT EXISTS idx_client     ON tool_calls (client_name);
"""


class AuditLogger:
    """Async SQLite audit logger — records every MCP tool invocation."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def _ensure_db(self) -> None:
        """Lazily initialize the DB connection on first use."""
        if self._db is None:
            await self.initialize()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def log_tool_call(
        self,
        *,
        tool_name: str,
        parameters: dict | None = None,
        success: bool,
        error_msg: str | None = None,
        duration_ms: float,
        request_id: str | None = None,
        session_id: str | None = None,
        client_name: str | None = None,
        client_version: str | None = None,
    ) -> None:
        """Insert an audit record. Never raises — failures are logged and swallowed."""
        try:
            await self._ensure_db()
            await self._db.execute(
                """
                INSERT INTO tool_calls
                    (timestamp, request_id, session_id, client_name, client_version,
                     tool_name, parameters, success, error_msg, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    request_id,
                    session_id,
                    client_name,
                    client_version,
                    tool_name,
                    json.dumps(parameters) if parameters else None,
                    1 if success else 0,
                    error_msg,
                    duration_ms,
                ),
            )
            await self._db.commit()
        except Exception:
            logger.exception("Failed to write audit record for %s", tool_name)

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    async def _fetchall_dicts(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a query and return rows as list of dicts."""
        await self._ensure_db()
        self._db.row_factory = aiosqlite.Row
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def query_recent(self, *, limit: int = 50) -> list[dict]:
        return await self._fetchall_dicts(
            "SELECT * FROM tool_calls ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    async def query_with_filters(
        self,
        *,
        tool_name: str | None = None,
        session_id: str | None = None,
        client_name: str | None = None,
        since: str | None = None,
        until: str | None = None,
        errors_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[str | int] = []

        if tool_name:
            clauses.append("tool_name = ?")
            params.append(tool_name)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if client_name:
            clauses.append("client_name = ?")
            params.append(client_name)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)
        if errors_only:
            clauses.append("success = 0")

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        return await self._fetchall_dicts(
            f"SELECT * FROM tool_calls{where} ORDER BY id DESC LIMIT ?",
            tuple(params),
        )

    async def get_summary_stats(self) -> dict:
        await self._ensure_db()
        self._db.row_factory = aiosqlite.Row

        # Overall stats
        cur = await self._db.execute(
            """
            SELECT
                COUNT(*)                                    AS total_calls,
                COUNT(DISTINCT tool_name)                   AS unique_tools,
                COUNT(DISTINCT client_name)                 AS unique_clients,
                COUNT(DISTINCT session_id)                  AS unique_sessions,
                ROUND(100.0 * SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) / MAX(COUNT(*), 1), 1)
                                                            AS error_rate_pct,
                ROUND(AVG(duration_ms), 1)                  AS avg_duration_ms,
                ROUND(MAX(duration_ms), 1)                  AS max_duration_ms,
                MIN(timestamp)                              AS first_call,
                MAX(timestamp)                              AS last_call
            FROM tool_calls
            """
        )
        overall = dict(await cur.fetchone())

        # Per-tool breakdown
        rows = await self._fetchall_dicts(
            """
            SELECT
                tool_name,
                COUNT(*)                                    AS calls,
                ROUND(100.0 * SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) / MAX(COUNT(*), 1), 1)
                                                            AS error_rate_pct,
                ROUND(AVG(duration_ms), 1)                  AS avg_duration_ms,
                ROUND(MAX(duration_ms), 1)                  AS max_duration_ms
            FROM tool_calls
            GROUP BY tool_name
            ORDER BY calls DESC
            """
        )

        return {"overall": overall, "per_tool": rows}
