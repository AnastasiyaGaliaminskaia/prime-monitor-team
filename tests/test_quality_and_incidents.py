"""Регрессии покрытия платежей и восстановления инцидента при backfill."""

import sqlite3
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from prime import replay, report
from prime.alerts import detect_anomalies
from prime.analytics_data import _payment_quality
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


class BackfillTests(unittest.TestCase):
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

        def load_daily(start, end, *, subscriptions):
            return daily[daily.index.to_series().dt.date.between(start, end, inclusive="left")], channels

        with TemporaryDirectory() as folder:
            path = Path(folder) / "prime.sqlite3"
            with (
                patch.object(report, "load_daily", side_effect=load_daily),
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
                    self.assertEqual(replay.run_backfill(latest, latest, path), 1)
                    after = conn.execute(
                        "SELECT report FROM reports WHERE report_date = ?", (latest.isoformat(),)
                    ).fetchone()[0]
                    self.assertNotIn("Всплеск завершений", after)
                    self.assertEqual(conn.execute("SELECT last_date, incidents FROM replay_state").fetchone(), state_before)
                    self.assertEqual(conn.execute("SELECT count(*) FROM reports").fetchone()[0], 16)


if __name__ == "__main__":
    unittest.main()
