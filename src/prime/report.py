"""Собрать дневной отчёт ПРАЙМ и подготовить его для вывода."""

import logging
from datetime import date, timedelta

import pandas as pd

from .aggregate_cache import AggregateCache
from .alerts import LOOKBACK_WEEKS, RULES, alert_details, detect_anomalies, update_incidents
from .analytics_data import AFTER_LAST_DAY, FIRST_DAY, load_churn_cohorts, load_daily, load_payment_channels
from .factors import channel_revenue_factors, weekly_revenue_factors
from .market_data import load_cbr_rate, load_imoex
from .metrics import period_metrics

log = logging.getLogger(__name__)

METRICS = {
    "revenue": ("Выручка", "money"),
    "payments_count": ("Успешные платежи", "count"),
    "average_payment": ("Средний платёж", "money"),
    "active_subscriptions": ("Активные подписки на конец периода", "count"),
    "new_paying_clients": ("Новые платящие клиенты", "count"),
    "failed_payment_rate": ("Доля неуспешных списаний", "percent"),
    "churn_rate": ("Доля оттока", "percent"),
}
REASONS = {
    "no_payments_in_source": "в источнике нет платежей",
    "incomplete_coverage": "неполное покрытие платёжного дня",
    "invalid_amount": "некорректная сумма платежа",
    "no_endings_in_source": "в источнике нет завершений подписок",
    "reconciliation_failed": "число завершений не сходится с разбивкой по каналам",
    "missing_daily_payment_data": "нет дневных данных о платежах",
    "missing_daily_subscription_data": "нет дневных данных о подписках",
    "missing_active_snapshot": "нет снимка активных подписок",
    "no_successful_payments": "нет успешных платежей",
    "missing_first_payment_counts": "нет данных о первых успешных платежах",
    "missing_payment_attempt_counts": "нет данных о попытках списания",
    "no_completed_payment_attempts": "нет завершённых попыток списания",
    "missing_churn_cohort": "нет стартовой когорты подписок",
    "invalid_churn_cohort": "некорректные размеры когорты оттока",
    "no_active_subscriptions_at_start": "нет активных подписок на начало периода",
}


class ReportDateError(ValueError):
    """Запрошена дата вне доступного периода."""


def validate_report_range(start: date, end: date) -> None:
    if start > end:
        raise ReportDateError("Начало периода должно быть не позже конца")
    if not FIRST_DAY < start <= end <= AFTER_LAST_DAY:
        raise ReportDateError(
            f"Дата отчёта вне диапазона {(FIRST_DAY + timedelta(days=1)):%d.%m.%Y}"
            f"–{AFTER_LAST_DAY:%d.%m.%Y}"
        )


def build_day(report_date: date, *, cache: AggregateCache | None = None) -> dict:
    """Собрать отчёт за одну дату без записи состояния."""
    row, _ = next(iter_reports(report_date, report_date, cache=cache))
    return row


def _incidents_before(
    daily: pd.DataFrame, start: date, end: date, *, cache: AggregateCache | None = None,
) -> set[str]:
    """Найти последнее подтверждённое состояние каждого правила до end."""
    open_incidents = set()
    unresolved = set(RULES)
    while unresolved:
        day = end - timedelta(days=1)
        while unresolved and day >= start:
            assessments = detect_anomalies(daily, day)
            for metric in tuple(unresolved):
                status = assessments[metric]["status"]
                if status in ("normal", "anomaly"):
                    unresolved.remove(metric)
                    if status == "anomaly":
                        open_incidents.add(metric)
            day -= timedelta(days=1)
        if not unresolved or start == FIRST_DAY:
            break
        end = start
        start = max(FIRST_DAY, end - timedelta(weeks=4))
        history_start = max(FIRST_DAY, start - timedelta(weeks=LOOKBACK_WEEKS))
        daily, _ = load_daily(history_start, end, subscriptions=True, cache=cache)
    return open_incidents


def iter_reports(
    start: date, end: date, *, open_incidents: set[str] | None = None, cache: AggregateCache | None = None,
):
    """Считать отчёты по датам включительно, загружая витрину за диапазон один раз."""
    validate_report_range(start, end)
    first_event_day = start - timedelta(days=1)
    # Начинаем с ближайших двух недель и при пропусках смотрим дальше назад.
    evaluation_start = max(FIRST_DAY, first_event_day - timedelta(weeks=2))
    if open_incidents is not None:
        evaluation_start = first_event_day
    load_start = max(FIRST_DAY, evaluation_start - timedelta(weeks=LOOKBACK_WEEKS))
    daily, channels = load_daily(load_start, end, subscriptions=True, cache=cache)
    payments_by_channel = load_payment_channels(max(FIRST_DAY, start - timedelta(days=14)), end, cache=cache)
    dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    periods = list(dict.fromkeys(
        (report_date - timedelta(days=length), report_date)
        for report_date in dates
        for length in (1, 7)
        if report_date - timedelta(days=length) >= FIRST_DAY
    ))
    cohorts = load_churn_cohorts(periods, cache=cache)

    incidents = (
        _incidents_before(daily, evaluation_start, first_event_day, cache=cache)
        if open_incidents is None else open_incidents
    )

    for report_date in dates:
        day = report_date - timedelta(days=1)
        week_start = report_date - timedelta(days=7)
        assessments = detect_anomalies(daily, day)
        incidents, new = update_incidents(assessments, incidents)
        factors = weekly_revenue_factors(daily, report_date)
        by_channel = channel_revenue_factors(payments_by_channel, factors)
        row = {
            "report_date": report_date,
            "day": day,
            "week_start": week_start,
            "day_metrics": period_metrics(
                daily, day, report_date, churn_cohort=cohorts.get((day, report_date))
            ),
            "week_metrics": period_metrics(
                daily, week_start, report_date, churn_cohort=cohorts.get((week_start, report_date))
            ),
            "factors": factors,
            "channel_factors": by_channel,
            "cbr_rate": load_cbr_rate(report_date),
            "imoex": load_imoex(report_date),
            "alert_assessments": assessments,
            "new_alerts": alert_details(assessments, new, channels.loc[channels["day"].eq(pd.Timestamp(day))]),
        }
        log.info("Отчёт за %s рассчитан по %d дням витрины", report_date, len(daily))
        for section in ("day_metrics", "week_metrics", "factors"):
            if row[section]["issues"]:
                log.warning("Отчёт за %s, %s: %s", report_date, section, row[section]["issues"])
        if by_channel["reason"] == "channel_totals_mismatch":
            log.warning("Отчёт за %s: итоги по каналам не сходятся с выручкой", report_date)
        yield row, incidents


def _value(value: float | None, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "percent":
        return f"{value:.2%}".replace(".", ",")
    if kind == "money":
        return f"{value:,.2f} ₽".replace(",", " ").replace(".", ",")
    return f"{value:,}".replace(",", " ")


def _issue_lines(issues: dict, labels: dict[str, str]) -> list[str]:
    grouped = {}
    for key, details in issues.items():
        items = tuple(details.items())
        grouped.setdefault(items, []).append(labels[key])
    lines = []
    for details, names in grouped.items():
        notes = [
            f"{day:%d.%m} — {REASONS.get(reason, reason)}"
            if isinstance(day, date) else REASONS.get(reason, reason)
            for day, reason in details
        ]
        lines.append(f"  {', '.join(names)}: {'; '.join(notes)}")
    return lines


def format_report(row: dict) -> str:
    """Подготовить текст отчёта для терминала."""
    day = row["day"]
    lines = [
        f"ПРАЙМ · отчёт на {row['report_date']:%d.%m.%Y} · UTC",
        f"Данные за {day:%d.%m.%Y} и {row['week_start']:%d.%m}–{day:%d.%m}",
        "",
        f"{'Метрика':37} {'День':>15} {'7 дней':>15}",
    ]
    labels = {key: name for key, (name, _) in METRICS.items()}
    for key, (name, kind) in METRICS.items():
        day_value = _value(row["day_metrics"][key], kind)
        week_value = _value(row["week_metrics"][key], kind)
        lines.append(f"{name:37} {day_value:>15} {week_value:>15}")
    for key, name in (("day_metrics", "День"), ("week_metrics", "7 дней")):
        issues = {metric: details for metric, details in row[key]["issues"].items() if metric in METRICS}
        if issues:
            lines.extend(["", f"{name} · причины пропусков", *_issue_lines(issues, labels)])

    factors = row["factors"]
    lines.extend([
        "",
        f"Выручка: {factors['previous_start']:%d.%m}–"
        f"{factors['current_start'] - timedelta(days=1):%d.%m} → "
        f"{factors['current_start']:%d.%m}–{day:%d.%m}",
    ])
    if factors["issues"]:
        factor_labels = {"previous_week": "Предыдущие 7 дней", "current_week": "Последние 7 дней"}
        lines.extend(["Разложение недоступно.", *_issue_lines(factors["issues"], factor_labels)])
    else:
        delta = factors["current_revenue"] - factors["previous_revenue"]
        lines.extend([
            f"  Изменение выручки: {_value(delta, 'money')}",
            f"  Вклад числа платежей: {_value(factors['payment_count_effect'], 'money')}",
            f"  Вклад среднего платежа: {_value(factors['average_payment_effect'], 'money')}",
        ])
    channel_factors = row.get("channel_factors")
    if channel_factors is not None:
        lines.extend(["", "Изменение выручки по каналам привлечения"])
        reason = channel_factors["reason"]
        if reason == "incomplete_windows":
            lines.append("  Недоступно: есть неполное окно.")
        elif reason == "channel_totals_mismatch":
            lines.append("  Недоступно: итоги по каналам не сходятся с общей выручкой.")
        else:
            for channel in channel_factors["channels"]:
                lines.append(f"  {channel['channel']}: {_value(channel['delta'], 'money')}")
            leading = channel_factors["leading"]
            if leading is not None:
                lines.append(f"  Наибольший вклад в изменение: {leading['channel']}.")
                if leading["payment_count_effect"] is None:
                    lines.append("    В одной из недель у канала не было успешных платежей.")
                else:
                    lines.append(f"    Число платежей: {_value(leading['payment_count_effect'], 'money')}")
                    lines.append(f"    Средний платёж: {_value(leading['average_payment_effect'], 'money')}")

    lines.extend(["", "Бизнес-алёрты"])
    if not row.get("new_alerts"):
        lines.append("  Новых алёртов нет.")
    for alert in row.get("new_alerts", []):
        metric = alert["metric"]
        name, kind = (
            ("Всплеск завершений подписок", "count")
            if metric == "ended_subscriptions" else METRICS[metric]
        )
        observed = int(alert["observed"]) if kind == "count" else alert["observed"]
        expected = f"{alert['expected']:g}" if kind == "count" else _value(alert["expected"], kind)
        lines.append(f"  {name}: {_value(observed, kind)}, обычное значение {expected}")
        if "channel" in alert:
            lines.append(f"    Ведущий канал: {alert['channel']} ({alert['channel_count']} завершений)")

    lines.extend(["", "Рыночный контекст"])
    rate = row.get("cbr_rate")
    if rate is None:
        lines.append("  USD/RUB · Банк России: курс недоступен.")
    else:
        rate_value = f"{rate.rubles_per_usd:.4f}".replace(".", ",")
        lines.append(f"  USD/RUB · Банк России: {rate_value} ₽, действует с {rate.effective_date:%d.%m.%Y}")

    index = row.get("imoex")
    if index is None:
        lines.append("  IMOEX · Мосбиржа ISS: индекс недоступен.")
    else:
        closed = f"{index.latest.value:,.2f}".replace(",", " ").replace(".", ",")
        lines.append(f"  IMOEX · Мосбиржа ISS: {closed} п. на {index.latest.trade_date:%d.%m.%Y}")
        change = index.change
        if change is not None:
            week_change = f"{change:+.2%}".replace(".", ",")
            lines.append(f"    За неделю: {week_change} к закрытию {index.previous.trade_date:%d.%m.%Y}")
        else:
            lines.append("    Изменение за неделю недоступно.")
    return "\n".join(lines)
