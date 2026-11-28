"""Хранение результатов и последовательный повтор исторических дней."""

import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from .aggregate_cache import AggregateCache
from .analytics_data import AFTER_LAST_DAY
from .report import format_report, iter_reports, validate_report_range

DEFAULT_STORE = Path("out/prime_global.sqlite3")


def _open_store(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS reports (
                report_date TEXT PRIMARY KEY,
                report TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS replay_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_date TEXT NOT NULL,
                incidents TEXT NOT NULL
            );
        """)
    except Exception:
        conn.close()
        raise
    return conn


def _save_report(conn: sqlite3.Connection, report_date: date, report: str) -> None:
    conn.execute("""
        INSERT INTO reports (report_date, report) VALUES (?, ?)
        ON CONFLICT (report_date) DO UPDATE SET report = excluded.report
    """, (report_date.isoformat(), report))


def _state(conn: sqlite3.Connection) -> tuple[str, str] | None:
    return conn.execute("SELECT last_date, incidents FROM replay_state WHERE id = 1").fetchone()


def run_next_day(
    start: date, path: Path = DEFAULT_STORE, *, cache: AggregateCache | None = None,
) -> tuple[date, str] | None:
    """Рассчитать один день и одной транзакцией записать отчёт, дату и инциденты."""
    with closing(_open_store(path)) as conn:
        state = _state(conn)
        day = date.fromisoformat(state[0]) + timedelta(days=1) if state else start
        if state is None:
            validate_report_range(day, day)
        if day > AFTER_LAST_DAY:
            return None
        incidents = set(json.loads(state[1])) if state else None
        row, open_incidents = next(iter_reports(day, day, open_incidents=incidents, cache=cache))
        report = format_report(row)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if _state(conn) != state:
                raise RuntimeError("Другой запуск уже обработал эту дату")
            _save_report(conn, day, report)
            conn.execute("""
                INSERT INTO replay_state (id, last_date, incidents) VALUES (1, ?, ?)
                ON CONFLICT (id) DO UPDATE SET last_date = excluded.last_date,
                                               incidents = excluded.incidents
            """, (day.isoformat(), json.dumps(sorted(open_incidents))))
    return day, report


def run_backfill(
    start: date, end: date, path: Path = DEFAULT_STORE, *, cache: AggregateCache | None = None,
) -> int:
    """Пересчитать диапазон дат отчёта включительно, не продвигая replay."""
    validate_report_range(start, end)
    if cache is not None:
        cache = AggregateCache(cache.directory, refresh=True)
    saved = 0
    with closing(_open_store(path)) as conn:
        for row, _ in iter_reports(start, end, cache=cache):
            with conn:
                _save_report(conn, row["report_date"], format_report(row))
            saved += 1
    return saved
