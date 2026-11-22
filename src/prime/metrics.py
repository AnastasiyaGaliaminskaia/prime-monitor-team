"""Метрики подписки ПРАЙМ по дневной витрине."""

from datetime import date, timedelta

import pandas as pd


def revenue(payments: list[dict]) -> float:
    """Выручка: сумма списаний."""
    return sum(p["amount"] for p in payments)


def _calendar_period(daily: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Выбрать календарные дни UTC за интервал [start, end)."""
    if end <= start:
        raise ValueError("Конец периода должен быть позже начала")
    return daily.reindex(pd.date_range(start, end - timedelta(days=1), freq="D"))


def payment_totals(
    daily: pd.DataFrame, start: date, end: date
) -> tuple[int | None, float | None, dict[date, str]]:
    """Число платежей, выручка и причины пропуска по дням UTC."""
    period = _calendar_period(daily, start, end)
    columns = ["payments_count", "total_revenue"]
    complete = period["payments_complete"].eq(True).fillna(False)
    has_values = period[columns].notna().all(axis=1)
    issues = {
        day.date(): (
            str(period.at[day, "payment_quality"])
            if not complete.at[day] and pd.notna(period.at[day, "payment_quality"])
            else "missing_daily_payment_data"
        )
        for day in period.index[~(complete & has_values)]
    }
    if issues:
        return None, None, issues
    return int(period["payments_count"].sum()), float(period["total_revenue"].sum()), issues


def period_metrics(daily: pd.DataFrame, start: date, end: date) -> dict:
    """Вернуть пять метрик и причины отсутствия значений за [start, end).

    Индекс витрины содержит уникальные календарные даты UTC без часового пояса.
    В issues ключами служат названия показателей, а причинами — статусы по датам.
    Для нулевого числа платежей средний платёж не определён.
    """
    payments, total_revenue, payment_issues = payment_totals(daily, start, end)
    period = _calendar_period(daily, start, end)
    quality = period["subscription_quality"]
    ended = period["ended_subs"]
    valid_subscriptions = quality.eq("reconciled").fillna(False) & ended.notna()
    subscription_issues = {
        day.date(): (
            str(quality.at[day])
            if pd.notna(quality.at[day]) and quality.at[day] != "reconciled"
            else "missing_daily_subscription_data"
        )
        for day in period.index[~valid_subscriptions]
    }
    last_active = period["active_subs_eod"].iloc[-1]

    issues = {
        **{metric: payment_issues for metric in ("revenue", "payments_count", "average_payment")
           if payment_issues},
        **{metric: subscription_issues for metric in ("active_subscriptions", "ended_subscriptions")
           if subscription_issues},
    }
    if payments == 0:
        issues["average_payment"] = {"period": "no_successful_payments"}
    if not subscription_issues and pd.isna(last_active):
        issues["active_subscriptions"] = {period.index[-1].date(): "missing_active_snapshot"}

    return {
        "revenue": total_revenue,
        "payments_count": payments,
        "average_payment": total_revenue / payments if payments else None,
        "active_subscriptions": int(last_active) if "active_subscriptions" not in issues else None,
        "ended_subscriptions": int(ended.sum()) if not subscription_issues else None,
        "issues": issues,
    }
