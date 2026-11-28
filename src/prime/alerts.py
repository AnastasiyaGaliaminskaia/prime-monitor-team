"""Правила бизнес-алёртов для дневной витрины ПРАЙМ."""

from datetime import date
from statistics import median

import pandas as pd

LOOKBACK_WEEKS = 8
MIN_COMPARABLE_DAYS = 4
RULES = {
    "ended_subscriptions": ("subscription_quality", "reconciled", "up", 0.50),
    "revenue": ("payments_complete", True, "down", 0.20),
    "average_payment": ("payments_complete", True, "up", 0.15),
}


def detect_anomalies(daily: pd.DataFrame, day: date) -> dict:
    """Сравнить день с предыдущими восемью одноимёнными днями недели."""
    day = pd.Timestamp(day)
    current = daily.reindex([day]).iloc[0]
    baseline_days = [day - pd.Timedelta(weeks=n) for n in range(1, LOOKBACK_WEEKS + 1)]
    same_weekday = daily.reindex(baseline_days)
    result = {}

    for metric, (quality, valid, direction, minimum_change) in RULES.items():
        observed = current[metric]
        if pd.isna(current[quality]) or current[quality] != valid or pd.isna(observed):
            result[metric] = {"status": "missing_data"}
            continue
        history = same_weekday.loc[same_weekday[quality].eq(valid), metric].dropna()
        if len(history) < MIN_COMPARABLE_DAYS:
            result[metric] = {"status": "insufficient_history"}
            continue

        expected = median(history)
        mad = median(abs(history - expected))
        threshold = max(abs(expected) * minimum_change, 4 * mad)
        deviation = observed - expected if direction == "up" else expected - observed
        result[metric] = {
            "status": "anomaly" if deviation > threshold else "normal",
            "observed": float(observed),
            "expected": float(expected),
            "threshold": float(threshold),
        }
    return result


def update_incidents(
    assessments: dict, open_incidents: set[str]
) -> tuple[set[str], list[str]]:
    """Открыть новый инцидент или закрыть его после подтверждённой нормы."""
    still_open = open_incidents.copy()
    new_incidents = []
    for metric, result in assessments.items():
        if result["status"] == "anomaly" and metric not in still_open:
            still_open.add(metric)
            new_incidents.append(metric)
        elif result["status"] == "normal":
            still_open.discard(metric)
    return still_open, new_incidents


def alert_details(assessments: dict, new: list[str], channels: pd.DataFrame) -> list[dict]:
    """Подготовить новые события и разбивку по ведущему каналу завершений."""
    alerts = [{"metric": metric, **assessments[metric]} for metric in new]
    for alert in alerts:
        if alert["metric"] == "ended_subscriptions" and not channels.empty:
            top = channels.sort_values(["ended_subs", "channel"], ascending=[False, True]).iloc[0]
            alert["channel"] = str(top["channel"])
            alert["channel_count"] = int(top["ended_subs"])
    return alerts
