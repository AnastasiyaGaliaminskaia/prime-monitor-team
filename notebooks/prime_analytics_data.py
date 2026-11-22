"""Дневные агрегаты учебной базы для аналитических ноутбуков ПРАЙМ."""
from datetime import date

import pandas as pd
from sqlalchemy import text

from prime.config import get_engine

FIRST_DAY = date(2025, 1, 6)
AFTER_LAST_DAY = date(2026, 8, 31)


def load_daily(start: date, end: date, *, subscriptions: bool = False):
    """Вернуть дневную витрину и разбивку завершений за интервал [start, end) UTC."""
    if not FIRST_DAY <= start < end <= AFTER_LAST_DAY:
        raise ValueError("Интервал должен лежать внутри истории ПРАЙМ")
    bounds = {"start": start, "end": end}
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TIME ZONE 'UTC'"))
            scope = conn.execute(text(
                "SELECT client_filter FROM prime.assignments WHERE login = current_user"
            )).scalar_one()
            # Условие назначения берём из той же таблицы, что и загрузчик команды.
            clients = f"""
                SELECT client_id, acquisition_channel
                FROM prime.clients WHERE ({scope})
            """
            payments = pd.read_sql(text(f"""
                WITH scoped_clients AS ({clients}),
                scoped_payments AS (
                    SELECT CAST(p.paid_at AS date) AS day,
                           count(*) AS payments_count, sum(p.amount) AS total_revenue,
                           count(*) FILTER (WHERE p.amount IS NULL OR p.amount <= 0) AS bad_amounts
                    FROM prime.payments p
                    JOIN prime.subscriptions s USING (subscription_id)
                    JOIN scoped_clients c ON c.client_id = s.owner_client_id
                    WHERE p.paid_at >= :start AND p.paid_at < :end AND p.status = 'success'
                    GROUP BY 1
                ),
                coverage AS (
                    SELECT CAST(paid_at AS date) AS day,
                           extract(hour FROM min(paid_at)) AS first_hour,
                           extract(hour FROM max(paid_at)) AS last_hour
                    FROM prime.payments
                    WHERE paid_at >= :start AND paid_at < :end
                    GROUP BY 1
                ),
                sessions AS (
                    SELECT DISTINCT CAST(started_at AS date) AS day
                    FROM prime.service_usage
                    WHERE started_at >= :start AND started_at < :end
                )
                SELECT coverage.*, scoped_payments.payments_count, scoped_payments.total_revenue,
                       coalesce(scoped_payments.bad_amounts, 0) AS bad_amounts,
                       sessions.day IS NOT NULL AS has_sessions
                FROM coverage
                LEFT JOIN scoped_payments USING (day)
                LEFT JOIN sessions USING (day)
            """), conn, params=bounds)

            ended_by_channel = pd.DataFrame(columns=["day", "channel", "ended_subs"])
            if subscriptions:
                scoped_subs = f"""
                    SELECT s.started_at, s.ended_at,
                           coalesce(c.acquisition_channel, 'без канала') AS channel
                    FROM prime.subscriptions s
                    JOIN ({clients}) c ON c.client_id = s.owner_client_id
                    WHERE s.started_at < :end
                      AND (s.ended_at IS NULL OR s.ended_at >= :start)
                """
                movements = pd.read_sql(text(f"""
                    WITH scoped_subs AS ({scoped_subs}),
                    baseline AS (
                        SELECT count(*) AS active_at_start FROM scoped_subs WHERE started_at < :start
                    ),
                    starts AS (
                        SELECT CAST(started_at AS date) AS day, count(*) AS starts
                        FROM scoped_subs WHERE started_at >= :start AND started_at < :end GROUP BY 1
                    ),
                    endings AS (
                        SELECT CAST(ended_at AS date) AS day, count(*) AS endings
                        FROM scoped_subs WHERE ended_at >= :start AND ended_at < :end GROUP BY 1
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
                """), conn, params=bounds)
                ended_by_channel = pd.read_sql(text(f"""
                    WITH scoped_subs AS ({scoped_subs})
                    SELECT CAST(ended_at AS date) AS day, channel, count(*) AS ended_subs
                    FROM scoped_subs WHERE ended_at >= :start AND ended_at < :end
                    GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2
                """), conn, params=bounds)
    finally:
        engine.dispose()

    calendar = pd.date_range(start, pd.Timestamp(end) - pd.Timedelta(days=1), name="day")
    payments["day"] = pd.to_datetime(payments["day"])
    daily = payments.set_index("day").reindex(calendar)
    # Грубая проверка покрытия всего источника, независимая от объёма личного сегмента.
    early = daily["first_hour"].le(2)
    late = daily["last_hour"].ge(22)
    daily["payment_quality"] = "ok"
    daily.loc[~daily["bad_amounts"].eq(0), "payment_quality"] = "invalid_amount"
    daily.loc[~(early & late & daily["has_sessions"].eq(True)), "payment_quality"] = "incomplete_coverage"
    daily.loc[daily["first_hour"].isna(), "payment_quality"] = "no_payments_in_source"
    daily["payments_complete"] = daily["payment_quality"].eq("ok")
    columns = ["payments_count", "total_revenue"]
    daily[columns] = daily[columns].fillna(0).where(daily["payments_complete"], axis=0)
    daily["revenue"] = daily["total_revenue"]
    daily["average_payment"] = daily["total_revenue"].div(daily["payments_count"].where(daily["payments_count"].gt(0)))

    if subscriptions:
        movements["day"] = pd.to_datetime(movements["day"])
        movements = movements.set_index("day").reindex(calendar)
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
