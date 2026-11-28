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


def period_metrics(
    daily: pd.DataFrame,
    start: date,
    end: date,
    *,
    churn_cohort: tuple[int, int] | None = None,
) -> dict:
    """Вернуть показатели отчёта, операционные счётчики и причины пропусков за [start, end).

    Индекс витрины содержит уникальные календарные даты UTC без часового пояса.
    В issues ключами служат названия показателей, а причинами — статусы по датам.
    churn_cohort содержит число активных на начало периода и число завершившихся из них.
    Средний платёж и доля оттока отсутствуют при нулевом знаменателе.
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
        **{metric: payment_issues for metric in ("revenue", "payments_count", "average_payment", "new_paying_clients", "failed_payment_rate")
           if payment_issues},
        **{metric: subscription_issues for metric in ("active_subscriptions", "ended_subscriptions", "new_subscriptions", "churn_rate")
           if subscription_issues},
    }
    if payments == 0:
        issues["average_payment"] = {"period": "no_successful_payments"}
    if not subscription_issues and pd.isna(last_active):
        issues["active_subscriptions"] = {period.index[-1].date(): "missing_active_snapshot"}

    new_subscriptions = churn_rate = None
    if not subscription_issues:
        if "new_subscriptions" in period and period["new_subscriptions"].notna().all():
            new_subscriptions = int(period["new_subscriptions"].sum())
        else:
            issues["new_subscriptions"] = {"period": "missing_subscription_starts"}

        if churn_cohort is None:
            issues["churn_rate"] = {"period": "missing_churn_cohort"}
        else:
            active_at_start, ended_from_cohort = churn_cohort
            if not 0 <= ended_from_cohort <= active_at_start:
                issues["churn_rate"] = {"period": "invalid_churn_cohort"}
            elif active_at_start == 0:
                issues["churn_rate"] = {"period": "no_active_subscriptions_at_start"}
            else:
                churn_rate = ended_from_cohort / active_at_start

    new_paying_clients = failed_payment_rate = None
    if not payment_issues:
        new_payers = period.get("new_paying_clients")
        if new_payers is not None and new_payers.notna().all():
            new_paying_clients = int(new_payers.sum())
        else:
            issues["new_paying_clients"] = {"period": "missing_first_payment_counts"}

        attempts = period.reindex(columns=["failed_attempts", "completed_attempts"])
        if attempts.isna().any().any():
            issues["failed_payment_rate"] = {"period": "missing_payment_attempt_counts"}
        else:
            failed = int(attempts["failed_attempts"].sum())
            completed = int(attempts["completed_attempts"].sum())
            if completed:
                failed_payment_rate = failed / completed
            else:
                issues["failed_payment_rate"] = {"period": "no_completed_payment_attempts"}

    return {
        "revenue": total_revenue,
        "payments_count": payments,
        "average_payment": total_revenue / payments if payments else None,
        "active_subscriptions": int(last_active) if "active_subscriptions" not in issues else None,
        "ended_subscriptions": int(ended.sum()) if not subscription_issues else None,
        "new_subscriptions": new_subscriptions,
        "churn_rate": churn_rate,
        "new_paying_clients": new_paying_clients,
        "failed_payment_rate": failed_payment_rate,
        "issues": issues,
    }
