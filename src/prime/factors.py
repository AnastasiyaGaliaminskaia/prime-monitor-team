"""Разложение изменения выручки цепными подстановками по С6."""

from datetime import date, timedelta
from math import isclose

import pandas as pd

from .metrics import payment_totals


def _chain_effects(n0: int, r0: float, n1: int, r1: float) -> tuple[float, float]:
    """Вклад числа платежей и среднего платежа при ненулевых объёмах."""
    p0, p1 = r0 / n0, r1 / n1
    count_effect = (n1 - n0) * p0
    average_effect = n1 * (p1 - p0)
    if not isclose(count_effect + average_effect, r1 - r0, rel_tol=0, abs_tol=0.01):
        raise ArithmeticError("Вклады факторов не сходятся с изменением выручки")
    return count_effect, average_effect


def weekly_revenue_factors(daily: pd.DataFrame, report_date: date) -> dict:
    """Сравнить последние семь дней UTC и предыдущие семь.

    Дата отчёта исключена. Сначала меняется число платежей, затем средний
    платёж. При проблемах с данными доступны границы окон и причины пропуска.
    """
    week = timedelta(days=7)
    current_start = report_date - week
    previous_start = current_start - week
    n0, r0, previous_issues = payment_totals(daily, previous_start, current_start)
    n1, r1, current_issues = payment_totals(daily, current_start, report_date)

    issues = {}
    if previous_issues or n0 == 0:
        issues["previous_week"] = previous_issues or {"period": "no_successful_payments"}
    if current_issues or n1 == 0:
        issues["current_week"] = current_issues or {"period": "no_successful_payments"}

    count_effect = average_effect = None
    if n0 and n1:
        count_effect, average_effect = _chain_effects(n0, r0, n1, r1)

    return {
        "previous_start": previous_start,
        "current_start": current_start,
        "end": report_date,
        "previous_payment_count": n0,
        "current_payment_count": n1,
        "previous_revenue": r0,
        "payment_count_effect": count_effect,
        "average_payment_effect": average_effect,
        "current_revenue": r1,
        "issues": issues,
    }


def channel_revenue_factors(channel_daily: pd.DataFrame, total: dict) -> dict:
    """Разложить изменение выручки по каналам и уточнить ведущий вклад."""
    r0, r1 = total["previous_revenue"], total["current_revenue"]
    if r0 is None or r1 is None:
        return {"reason": "incomplete_windows", "channels": [], "leading": None}

    start = pd.Timestamp(total["previous_start"])
    middle = pd.Timestamp(total["current_start"])
    end = pd.Timestamp(total["end"])
    previous = channel_daily.loc[channel_daily["day"].ge(start) & channel_daily["day"].lt(middle)]
    current = channel_daily.loc[channel_daily["day"].ge(middle) & channel_daily["day"].lt(end)]
    measures = ["payments_count", "total_revenue"]
    for window, count, revenue in (
        (previous, total["previous_payment_count"], r0),
        (current, total["current_payment_count"], r1),
    ):
        if (
            window.duplicated(["day", "channel"]).any()
            or window[["channel", *measures]].isna().any().any()
            or window["payments_count"].sum() != count
            or not isclose(window["total_revenue"].sum(), revenue, rel_tol=0, abs_tol=0.01)
        ):
            return {"reason": "channel_totals_mismatch", "channels": [], "leading": None}

    first = previous.groupby("channel")[measures].sum()
    last = current.groupby("channel")[measures].sum()
    joined = first.join(last, how="outer", lsuffix="_previous", rsuffix="_current").fillna(0)
    contributions = []
    for channel, values in joined.iterrows():
        before = float(values["total_revenue_previous"])
        after = float(values["total_revenue_current"])
        contributions.append({
            "channel": channel,
            "previous_revenue": before,
            "current_revenue": after,
            "delta": after - before,
            "previous_payment_count": int(values["payments_count_previous"]),
            "current_payment_count": int(values["payments_count_current"]),
        })
    contributions.sort(key=lambda row: (-abs(row["delta"]), row["channel"]))
    if not isclose(sum(row["delta"] for row in contributions), r1 - r0, rel_tol=0, abs_tol=0.01):
        return {"reason": "channel_totals_mismatch", "channels": [], "leading": None}

    difference = r1 - r0
    leading = next((row for row in contributions if row["delta"] * difference > 0), None)
    if leading is not None:
        leading = dict(leading, payment_count_effect=None, average_payment_effect=None)
        n0, n1 = leading["previous_payment_count"], leading["current_payment_count"]
        if n0 and n1:
            leading["payment_count_effect"], leading["average_payment_effect"] = _chain_effects(
                n0, leading["previous_revenue"], n1, leading["current_revenue"]
            )

    return {
        "reason": None,
        "channels": contributions,
        "leading": leading,
    }
