"""Разложение изменения выручки цепными подстановками по С6."""

from datetime import date, timedelta
from math import isclose

import pandas as pd

from .metrics import payment_totals


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
        p0, p1 = r0 / n0, r1 / n1
        count_effect = (n1 - n0) * p0
        average_effect = n1 * (p1 - p0)
        if not isclose(count_effect + average_effect, r1 - r0, rel_tol=0, abs_tol=0.01):
            raise ArithmeticError("Вклады факторов не сходятся с изменением выручки")

    return {
        "previous_start": previous_start,
        "current_start": current_start,
        "end": report_date,
        "previous_revenue": r0,
        "payment_count_effect": count_effect,
        "average_payment_effect": average_effect,
        "current_revenue": r1,
        "issues": issues,
    }
