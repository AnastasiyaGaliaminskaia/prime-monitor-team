"""Дневные агрегаты учебной базы для отчёта и аналитических ноутбуков ПРАЙМ."""
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import date

import pandas as pd
from sqlalchemy import text

from .aggregate_cache import AggregateCache
from .config import get_engine

FIRST_DAY = date(2025, 1, 6)
AFTER_LAST_DAY = date(2026, 8, 31)


def _payment_quality(daily: pd.DataFrame) -> pd.Series:
    status = pd.Series("ok", index=daily.index, dtype="str")
    status.loc[~daily["bad_amounts"].eq(0)] = "invalid_amount"
    covered = daily["payment_hours"].eq(24) & daily["has_sessions"].eq(True)
    status.loc[~covered.fillna(False)] = "incomplete_coverage"
    status.loc[daily["payment_hours"].isna() | daily["payment_hours"].eq(0)] = "no_payments_in_source"
    return status


@contextmanager
def _read_aggregates(
    cache: AggregateCache | None,
) -> Iterator[Callable[[str, str, dict], pd.DataFrame]]:
    """Открыть соединение только при первом запросе, которого нет в кэше."""
    with ExitStack() as stack:
        conn = None

        def read(name: str, sql: str, params: dict) -> pd.DataFrame:
            def fetch() -> pd.DataFrame:
                nonlocal conn
                if conn is None:
                    engine = get_engine()
                    stack.callback(engine.dispose)
                    conn = stack.enter_context(engine.connect())
                    conn.execute(text("SET TIME ZONE 'UTC'"))
                return pd.read_sql(text(sql), conn, params=params)

            return fetch() if cache is None else cache.read(name, params, fetch)

        yield read


def load_daily(
    start: date, end: date, *, subscriptions: bool = False, cache: AggregateCache | None = None,
):
    """Вернуть дневную витрину и разбивку завершений за интервал [start, end) UTC."""
    if not FIRST_DAY <= start < end <= AFTER_LAST_DAY:
        raise ValueError("Интервал должен лежать внутри истории ПРАЙМ")
    bounds = {"start": start, "end": end}
    with _read_aggregates(cache) as read:
        payments = read("daily_payments", """
            WITH daily_payments AS (
                SELECT CAST(p.paid_at AS date) AS day,
                       count(*) FILTER (WHERE p.status = 'success') AS payments_count,
                       coalesce(sum(p.amount) FILTER (WHERE p.status = 'success'), 0) AS total_revenue,
                       count(*) FILTER (WHERE p.status = 'failed') AS failed_attempts,
                       count(*) AS completed_attempts,
                       count(*) FILTER (
                           WHERE p.status = 'success' AND (p.amount IS NULL OR p.amount <= 0)
                       ) AS bad_amounts
                FROM prime.payments p
                WHERE p.paid_at >= :start AND p.paid_at < :end
                  AND p.status IN ('success', 'failed', 'refunded')
                GROUP BY 1
            ),
            first_payers AS (
                SELECT s.owner_client_id, min(p.paid_at) AS first_paid_at
                FROM prime.payments p
                JOIN prime.subscriptions s USING (subscription_id)
                WHERE p.status = 'success' AND p.paid_at < :end
                  AND s.owner_client_id IS NOT NULL
                GROUP BY s.owner_client_id
                HAVING min(p.paid_at) >= :start
            ),
            new_payers AS (
                SELECT CAST(first_paid_at AS date) AS day, count(*) AS new_paying_clients
                FROM first_payers
                GROUP BY 1
            ),
            coverage AS (
                SELECT CAST(paid_at AS date) AS day,
                       count(DISTINCT extract(hour FROM paid_at)) AS payment_hours
                FROM prime.payments
                WHERE paid_at >= :start AND paid_at < :end
                GROUP BY 1
            ),
            sessions AS (
                SELECT DISTINCT CAST(started_at AS date) AS day
                FROM prime.service_usage
                WHERE started_at >= :start AND started_at < :end
            )
            SELECT coverage.*, daily_payments.payments_count, daily_payments.total_revenue,
                   daily_payments.failed_attempts, daily_payments.completed_attempts,
                   new_payers.new_paying_clients, coalesce(daily_payments.bad_amounts, 0) AS bad_amounts,
                   sessions.day IS NOT NULL AS has_sessions
            FROM coverage
            LEFT JOIN daily_payments USING (day)
            LEFT JOIN sessions USING (day)
            LEFT JOIN new_payers USING (day)
        """, bounds)

        ended_by_channel = pd.DataFrame(columns=["day", "channel", "ended_subs"])
        if subscriptions:
            movements = read("subscription_movements", """
                WITH relevant_subscriptions AS (
                    SELECT started_at, ended_at
                    FROM prime.subscriptions
                    WHERE started_at < :end
                      AND (ended_at IS NULL OR ended_at >= :start)
                ),
                baseline AS (
                    SELECT count(*) AS active_at_start FROM relevant_subscriptions WHERE started_at < :start
                ),
                starts AS (
                    SELECT CAST(started_at AS date) AS day, count(*) AS starts
                    FROM relevant_subscriptions WHERE started_at >= :start AND started_at < :end GROUP BY 1
                ),
                endings AS (
                    SELECT CAST(ended_at AS date) AS day, count(*) AS endings
                    FROM relevant_subscriptions WHERE ended_at >= :start AND ended_at < :end GROUP BY 1
                ),
                source_end_days AS (
                    SELECT DISTINCT CAST(ended_at AS date) AS day
                    FROM prime.subscriptions WHERE ended_at >= :start AND ended_at < :end
                )
                SELECT CAST(g.day AS date) AS day, baseline.active_at_start,
                       source_end_days.day IS NOT NULL AS source_has_endings,
                       coalesce(starts.starts, 0) AS starts, coalesce(endings.endings, 0) AS ended_subs
                FROM generate_series(
                     CAST(:start AS timestamp), CAST(:end AS timestamp) - interval '1 day', interval '1 day'
                ) AS g(day)
                CROSS JOIN baseline
                LEFT JOIN starts ON starts.day = CAST(g.day AS date)
                LEFT JOIN endings ON endings.day = CAST(g.day AS date)
                LEFT JOIN source_end_days ON source_end_days.day = CAST(g.day AS date)
                ORDER BY g.day
            """, bounds)
            ended_by_channel = read("subscription_endings_by_channel", """
                SELECT CAST(s.ended_at AS date) AS day,
                       coalesce(nullif(trim(c.acquisition_channel), ''), 'без канала') AS channel,
                       count(*) AS ended_subs
                FROM prime.subscriptions s
                LEFT JOIN prime.clients c ON c.client_id = s.owner_client_id
                WHERE s.started_at < :end
                  AND s.ended_at >= :start AND s.ended_at < :end
                GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2
            """, bounds)

    calendar = pd.date_range(start, pd.Timestamp(end) - pd.Timedelta(days=1), name="day")
    payments["day"] = pd.to_datetime(payments["day"])
    daily = payments.set_index("day").reindex(calendar)
    daily["payment_quality"] = _payment_quality(daily)
    daily["payments_complete"] = daily["payment_quality"].eq("ok")
    columns = ["payments_count", "total_revenue", "failed_attempts", "completed_attempts", "new_paying_clients"]
    daily[columns] = daily[columns].fillna(0).where(daily["payments_complete"], axis=0)
    daily["revenue"] = daily["total_revenue"]
    daily["average_payment"] = daily["total_revenue"].div(daily["payments_count"].where(daily["payments_count"].gt(0)))

    if subscriptions:
        movements["day"] = pd.to_datetime(movements["day"])
        movements = movements.set_index("day").reindex(calendar)
        daily["new_subscriptions"] = movements["starts"]
        daily["ended_subs"] = movements["ended_subs"]
        daily["ended_subscriptions"] = daily["ended_subs"]
        daily["active_subs_eod"] = movements["active_at_start"] + (movements["starts"] - movements["ended_subs"]).cumsum()
        # Календарь и сверка двух независимых агрегаций завершений.
        ended_by_channel["day"] = pd.to_datetime(ended_by_channel["day"])
        channel_totals = ended_by_channel.groupby("day")["ended_subs"].sum().reindex(calendar, fill_value=0)
        daily["subscriptions_complete"] = movements["ended_subs"].eq(channel_totals)
        # Сверка агрегаций не подтверждает наличие событий в исходной выгрузке.
        daily["subscription_quality"] = "reconciled"
        daily.loc[~movements["source_has_endings"].eq(True), "subscription_quality"] = "no_endings_in_source"
        daily.loc[~daily["subscriptions_complete"], "subscription_quality"] = "reconciliation_failed"
    return daily, ended_by_channel


def load_churn_cohorts(
    periods: list[tuple[date, date]], *, cache: AggregateCache | None = None,
) -> dict[tuple[date, date], tuple[int, int]]:
    """Посчитать стартовую когорту и её завершения для каждого периода UTC."""
    if not periods:
        return {}
    if any(not FIRST_DAY <= start < end <= AFTER_LAST_DAY for start, end in periods):
        raise ValueError("Интервал должен лежать внутри истории ПРАЙМ")

    starts, ends = zip(*dict.fromkeys(periods))
    with _read_aggregates(cache) as read:
        rows = read("churn_cohorts", """
            WITH periods AS (
                SELECT * FROM unnest(CAST(:starts AS date[]), CAST(:ends AS date[]))
                AS bounds(start_day, end_day)
            ),
            relevant_subscriptions AS (
                SELECT s.started_at, s.ended_at
                FROM prime.subscriptions s
                WHERE s.started_at < :latest_start
                  AND (s.ended_at IS NULL OR s.ended_at >= :earliest_start)
            )
            SELECT p.start_day, p.end_day, count(s.started_at) AS active,
                   count(*) FILTER (WHERE s.ended_at < p.end_day) AS ended
            FROM periods p
            LEFT JOIN relevant_subscriptions s ON s.started_at < p.start_day
                AND (s.ended_at IS NULL OR s.ended_at >= p.start_day)
            GROUP BY p.start_day, p.end_day
        """, {
            "starts": list(starts),
            "ends": list(ends),
            "latest_start": max(starts),
            "earliest_start": min(starts),
        })

    return {(start, end): (active, ended) for start, end, active, ended in rows.itertuples(index=False, name=None)}


def load_payment_channels(
    start: date, end: date, *, cache: AggregateCache | None = None,
) -> pd.DataFrame:
    """Вернуть дневную выручку и число успешных платежей по каналам UTC."""
    if not FIRST_DAY <= start < end <= AFTER_LAST_DAY:
        raise ValueError("Интервал должен лежать внутри истории ПРАЙМ")

    with _read_aggregates(cache) as read:
        channels = read("payments_by_channel", """
            SELECT CAST(p.paid_at AS date) AS day,
                   coalesce(nullif(trim(c.acquisition_channel), ''), 'без канала') AS channel,
                   count(*) AS payments_count, sum(p.amount) AS total_revenue
            FROM prime.payments p
            LEFT JOIN prime.subscriptions s USING (subscription_id)
            LEFT JOIN prime.clients c ON c.client_id = s.owner_client_id
            WHERE p.paid_at >= :start AND p.paid_at < :end AND p.status = 'success'
            GROUP BY 1, 2
        """, {"start": start, "end": end})

    channels["day"] = pd.to_datetime(channels["day"])
    return channels
