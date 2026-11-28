"""Собрать дневной отчёт ПРАЙМ и подготовить его для вывода."""

import logging
from datetime import date, timedelta

from .analytics_data import AFTER_LAST_DAY, FIRST_DAY, load_churn_cohorts, load_daily
from .factors import weekly_revenue_factors
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


def build_day(report_date: date) -> dict:
    """Собрать метрики за вчера, семь дней и разложение к предыдущей неделе UTC."""
    if not FIRST_DAY < report_date <= AFTER_LAST_DAY:
        raise ValueError(
            f"Дата отчёта вне диапазона {(FIRST_DAY + timedelta(days=1)):%d.%m.%Y}"
            f"–{AFTER_LAST_DAY:%d.%m.%Y}"
        )

    day = report_date - timedelta(days=1)
    week_start = report_date - timedelta(days=7)
    load_start = max(FIRST_DAY, report_date - timedelta(days=14))
    daily, _ = load_daily(load_start, report_date, subscriptions=True)
    periods = [(start, report_date) for start in (day, week_start) if start >= FIRST_DAY]
    cohorts = load_churn_cohorts(periods)
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
        "factors": weekly_revenue_factors(daily, report_date),
        "cbr_rate": load_cbr_rate(report_date),
        "imoex": load_imoex(report_date),
    }
    log.info("Отчёт за %s рассчитан по %d дням витрины", report_date, len(daily))
    for section in ("day_metrics", "week_metrics", "factors"):
        if row[section]["issues"]:
            log.warning("Отчёт за %s, %s: %s", report_date, section, row[section]["issues"])
    return row


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
