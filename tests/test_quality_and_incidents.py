"""Регрессии качества данных, кэширования и повторного прогона."""

import io
import sqlite3
import sys
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import date
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from prime import cli, replay, report
from prime.aggregate_cache import AggregateCache
from prime.alerts import detect_anomalies
from prime.analytics_data import _payment_quality, load_payment_channels
from prime.metrics import payment_totals


class DataQualityTests(unittest.TestCase):
    def test_missing_hours_do_not_look_like_revenue_drop(self):
        days = pd.date_range("2026-06-01", "2026-08-22")
        daily = pd.DataFrame({
            "payment_hours": 24,
            "has_sessions": True,
            "bad_amounts": 0,
            "payments_count": 20,
            "total_revenue": 2000.0,
            "revenue": 2000.0,
            "average_payment": 100.0,
            "ended_subscriptions": 30,
            "subscription_quality": "reconciled",
        }, index=days)
        affected = days[-1]
        daily.loc[affected, ["payment_hours", "payments_count", "total_revenue", "revenue"]] = [2, 2, 200, 200]
        daily["payment_quality"] = _payment_quality(daily)
        daily["payments_complete"] = daily["payment_quality"].eq("ok")
        self.assertEqual(daily.at[affected, "payment_quality"], "incomplete_coverage")
        self.assertEqual(detect_anomalies(daily, affected.date())["revenue"]["status"], "missing_data")
        payments, revenue, issues = payment_totals(daily, date(2026, 8, 16), date(2026, 8, 23))
        self.assertIsNone(payments)
        self.assertIsNone(revenue)
        self.assertEqual(issues, {affected.date(): "incomplete_coverage"})


class AggregateCacheTests(unittest.TestCase):
    def test_repeat_query_reads_parquet_without_connecting(self):
        start = date(2026, 8, 22)
        end = date(2026, 8, 23)
        frame = pd.DataFrame({
            "day": [start], "channel": ["organic"], "payments_count": [20], "total_revenue": [2000.0],
        })
        with TemporaryDirectory() as folder:
            cache = AggregateCache(Path(folder))
            with (
                patch("prime.analytics_data.get_engine") as get_engine,
                patch("prime.analytics_data.pd.read_sql", return_value=frame.copy()) as read_sql,
            ):
                original = load_payment_channels(start, end, cache=cache)
                get_engine.assert_called_once()
                read_sql.assert_called_once()

            with patch("prime.analytics_data.get_engine", side_effect=AssertionError("Кэш пропущен")):
                cached = load_payment_channels(start, end, cache=cache)
            pd.testing.assert_frame_equal(original, cached)


class BackfillTests(unittest.TestCase):
    def test_backfill_reports_blocked_cache_and_succeeds_after_retry(self):
        day = date(2026, 8, 24)
        params = {"end": day}
        revenue = 100

        def fetch():
            return pd.DataFrame({"revenue": [revenue]})

        def iter_reports(start, end, *, cache):
            frame = cache.read("daily_payments", params, fetch)
            yield {"report_date": day, "revenue": int(frame.at[0, "revenue"])}, set()

        args = ["prime", "backfill", "--start", day.isoformat(), "--end", day.isoformat()]
        with TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "global.sqlite3"
            cache = AggregateCache(root / "cache")
            with (
                patch.object(replay, "iter_reports", side_effect=iter_reports),
                patch.object(replay, "format_report", side_effect=lambda row: str(row["revenue"])),
                patch.object(cli, "run_backfill", partial(replay.run_backfill, path=path)),
                patch.object(cli, "AggregateCache", return_value=cache),
                patch.object(cli, "Path", side_effect=lambda name: root / name),
                patch.object(cli.logging, "basicConfig"),
                patch.object(sys, "argv", args),
                patch.object(cli.log, "error"),
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(replay.run_backfill(day, day, path, cache=cache), 1)
                revenue = 200
                with patch.object(Path, "replace", side_effect=PermissionError("Parquet file is open")):
                    self.assertEqual(cli.main(), 1)

                self.assertIn("Не удалось обновить Parquet-кэш", stderr.getvalue())
                self.assertEqual(stdout.getvalue(), "")
                with closing(sqlite3.connect(path)) as conn:
                    self.assertEqual(conn.execute("SELECT report FROM reports").fetchone(), ("100",))
                self.assertEqual(cache.read("daily_payments", params, fetch).at[0, "revenue"], 100)

                self.assertEqual(cli.main(), 0)
                with closing(sqlite3.connect(path)) as conn:
                    self.assertEqual(conn.execute("SELECT report FROM reports").fetchone(), ("200",))
                self.assertEqual(cache.read("daily_payments", params, fetch).at[0, "revenue"], 200)

    def test_backfill_restores_incident_across_long_gap(self):
        days = pd.date_range("2026-05-01", "2026-08-22")
        daily = pd.DataFrame({
            "payments_count": 1, "total_revenue": 100.0, "revenue": 100.0,
            "average_payment": 100.0, "payments_complete": True, "payment_quality": "ok",
            "ended_subs": 30, "ended_subscriptions": 30,
            "active_subs_eod": 500, "subscription_quality": "reconciled",
            "new_subscriptions": 30, "new_paying_clients": 1,
            "failed_attempts": 0, "completed_attempts": 1,
        }, index=days)
        daily.loc["2026-08-08":"2026-08-21", "subscription_quality"] = "no_endings_in_source"
        spikes = pd.to_datetime(["2026-08-07", "2026-08-22"])
        daily.loc[spikes, ["ended_subs", "ended_subscriptions"]] = 200
        channels = pd.DataFrame({"day": spikes, "channel": "organic", "ended_subs": 200})
        by_payment_channel = pd.DataFrame({
            "day": days, "channel": "organic", "total_revenue": 100.0, "payments_count": 1,
        })

        def load_daily(start, end, *, subscriptions, cache=None):
            return daily[daily.index.to_series().dt.date.between(start, end, inclusive="left")], channels

        with TemporaryDirectory() as folder:
            path = Path(folder) / "prime.sqlite3"
            with (
                patch.object(report, "load_daily", side_effect=load_daily) as daily_loader,
                patch.object(report, "load_payment_channels", return_value=by_payment_channel),
                patch.object(report, "load_churn_cohorts", return_value={}),
                patch.object(report, "load_cbr_rate", return_value=None),
                patch.object(report, "load_imoex", return_value=None),
                patch.object(report.log, "warning"),
            ):
                first = date(2026, 8, 8)
                latest = date(2026, 8, 23)
                self.assertIn("Всплеск завершений", replay.run_next_day(first, path)[1])
                for _ in range((latest - first).days):
                    current, rendered = replay.run_next_day(first, path)
                self.assertEqual(current, latest)
                self.assertNotIn("Всплеск завершений", rendered)

                with closing(sqlite3.connect(path)) as conn:
                    state_before = conn.execute("SELECT last_date, incidents FROM replay_state").fetchone()
                    self.assertIn("ended_subscriptions", state_before[1])
                    cache = AggregateCache(Path(folder) / "cache")
                    self.assertEqual(replay.run_backfill(latest, latest, path, cache=cache), 1)
                    backfill_cache = daily_loader.call_args.kwargs["cache"]
                    self.assertEqual(backfill_cache.directory, cache.directory)
                    self.assertTrue(backfill_cache.refresh)
                    self.assertFalse(cache.refresh)
                    after = conn.execute(
                        "SELECT report FROM reports WHERE report_date = ?", (latest.isoformat(),)
                    ).fetchone()[0]
                    self.assertNotIn("Всплеск завершений", after)
                    self.assertEqual(conn.execute("SELECT last_date, incidents FROM replay_state").fetchone(), state_before)
                    self.assertEqual(conn.execute("SELECT count(*) FROM reports").fetchone()[0], 16)


if __name__ == "__main__":
    unittest.main()
