from datetime import date
import logging
import os
import uuid
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import text

from .config import get_engine
from .market_data import load_cbr_rate
import os

logger = logging.getLogger(__name__)

# Версия кеша намеренно изменена: старые файлы содержат результаты
# с CLIENT_FILTER и не должны использоваться в глобальной аналитике.
CACHE_DIR = Path("data")
CACHE_VERSION = "global_v2"


def _read_sql(query: str, report_date: str) -> pd.DataFrame:
    """Выполняет параметризованный запрос за дату в часовом поясе UTC."""
    engine = get_engine()

    try:
        with engine.connect() as conn:
            conn.execute(text("SET TIME ZONE 'UTC'"))
            return pd.read_sql(
                text(query),
                conn,
                params={"date": report_date},
            )
    except Exception:
        logger.exception("Ошибка SQL-загрузки за %s", report_date)
        raise


def fetch_daily_revenue(report_date: str) -> pd.DataFrame:
    """Глобальная дневная выручка и количество успешных платежей."""
    query = """
        SELECT
            CAST(p.paid_at AS DATE) AS payment_date,
            COUNT(p.payment_id) AS payments_count,
            SUM(p.amount) AS total_revenue
        FROM prime.payments AS p
        JOIN prime.subscriptions AS s
            ON s.subscription_id = p.subscription_id
        WHERE p.status = 'success'
          AND p.paid_at >= CAST(:date AS DATE)
          AND p.paid_at < CAST(:date AS DATE) + INTERVAL '1 day'
        GROUP BY CAST(p.paid_at AS DATE)
    """
    logger.info("Загрузка глобальной выручки за %s", report_date)
    return _read_sql(query, report_date)


def fetch_daily_new_clients(report_date: str) -> pd.DataFrame:
    """Глобальное количество новых клиентов за день."""
    query = """
        SELECT
            CAST(registered_at AS DATE) AS registration_date,
            COUNT(client_id) AS new_clients_count
        FROM prime.clients
        WHERE registered_at >= CAST(:date AS DATE)
          AND registered_at < CAST(:date AS DATE) + INTERVAL '1 day'
        GROUP BY CAST(registered_at AS DATE)
    """
    logger.info("Загрузка глобальных новых клиентов за %s", report_date)
    return _read_sql(query, report_date)


def fetch_new_clients(report_date: str) -> pd.DataFrame:
    """
    Совместимость со старым именем функции.
    Возвращает колонку new_clients, как в прежней реализации.
    """
    df = fetch_daily_new_clients(report_date)
    if "new_clients_count" in df.columns:
        return df.rename(columns={"new_clients_count": "new_clients"})
    return df.rename(columns={"new_clients_count": "new_clients"})


def fetch_daily_sessions(report_date: str) -> pd.DataFrame:
    """Глобальное количество сессий за день."""
    query = """
        SELECT
            CAST(started_at AS DATE) AS session_date,
            COUNT(*) AS sessions_count
        FROM prime.service_usage
        WHERE started_at >= CAST(:date AS DATE)
          AND started_at < CAST(:date AS DATE) + INTERVAL '1 day'
        GROUP BY CAST(started_at AS DATE)
    """
    logger.info("Загрузка глобальных сессий за %s", report_date)
    return _read_sql(query, report_date)



def fetch_active_subscriptions(report_date: str) -> pd.DataFrame:
    """Глобальное количество подписок, активных хотя бы часть дня."""

    query = """
        SELECT
            CAST(:date AS DATE) AS report_date,
            COUNT(subscription_id) AS active_subs
        FROM prime.subscriptions
        WHERE started_at < CAST(:date AS DATE) + INTERVAL '1 day'
          AND (
                ended_at IS NULL
                OR ended_at >= CAST(:date AS DATE)
          )
    """

    logger.info(
        "Загрузка глобальных активных подписок за %s",
        report_date
    )

    return _read_sql(query, report_date)



def fetch_churn(report_date: str) -> pd.DataFrame:
    """Глобальное количество отмен за день; сохраняет прежнюю форму результата."""
    query = """
        SELECT
            CAST(ended_at AS DATE) AS churn_date,
            COUNT(DISTINCT subscription_id) AS churned_subs
        FROM prime.subscriptions
        WHERE ended_at >= CAST(:date AS DATE)
          AND ended_at < CAST(:date AS DATE) + INTERVAL '1 day'
        GROUP BY CAST(ended_at AS DATE)
    """
    logger.info("Загрузка глобального оттока за %s", report_date)
    return _read_sql(query, report_date)


def fetch_churn_by_channel(report_date: str) -> pd.DataFrame:
    """
    Глобальный отток по каналам привлечения.
    NULL-каналы сохраняются как отдельная категория.
    """
    query = """
        SELECT
            CAST(s.ended_at AS DATE) AS churn_date,
            COALESCE(c.acquisition_channel, 'без канала')
                AS acquisition_channel,
            COUNT(DISTINCT s.subscription_id) AS churned_subs
        FROM prime.subscriptions AS s
        JOIN prime.clients AS c
            ON c.client_id = s.owner_client_id
        WHERE s.ended_at >= CAST(:date AS DATE)
          AND s.ended_at < CAST(:date AS DATE) + INTERVAL '1 day'
        GROUP BY
            CAST(s.ended_at AS DATE),
            COALESCE(c.acquisition_channel, 'без канала')
        ORDER BY churned_subs DESC
    """
    logger.info("Загрузка глобального оттока по каналам за %s", report_date)
    return _read_sql(query, report_date)


def fetch_daily_payments_by_method(report_date: str) -> pd.DataFrame:
    """Глобальные успешные платежи в разрезе метода оплаты."""
    query = """
        SELECT
            p.payment_method,
            COUNT(p.payment_id) AS payments_count,
            SUM(p.amount) AS total_amount
        FROM prime.payments AS p
        JOIN prime.subscriptions AS s
            ON s.subscription_id = p.subscription_id
        WHERE p.status = 'success'
          AND p.paid_at >= CAST(:date AS DATE)
          AND p.paid_at < CAST(:date AS DATE) + INTERVAL '1 day'
        GROUP BY p.payment_method
        ORDER BY total_amount DESC
    """
    logger.info("Загрузка глобальных платежей по методам за %s", report_date)
    return _read_sql(query, report_date)


def fetch_cbr_currency_rate(report_date: str) -> pd.DataFrame:
    """Исторический USD/RUB через официальный API Банка России."""
    columns = ["date", "currency", "rate", "effective_date"]
    rate = load_cbr_rate(date.fromisoformat(report_date))
    if rate is None:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([{
        "date": report_date,
        "currency": "USD",
        "rate": rate.rubles_per_usd,
        "effective_date": rate.effective_date.isoformat(),
    }], columns=columns)


def _get_cached_or_fetch(report_date: str, metric_name: str, fetch_func, *, cache_empty=True, **kwargs) -> pd.DataFrame:
    """
    Читает или создаёт дневной Parquet-кеш.

    В имени файла есть версия global_v2, поэтому старые кеши,
    созданные с CLIENT_FILTER, не будут случайно использованы.
    Запись сначала идёт во временный файл, затем атомарно переименовывается.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    file_path = CACHE_DIR / (
        f"{CACHE_VERSION}_{metric_name}_{report_date}.parquet"
    )

    if file_path.exists():
        logger.info("[CACHE] Чтение %s", file_path)
        return pd.read_parquet(file_path)

    logger.info("[DB] Загрузка %s за %s", metric_name, report_date)
    df = fetch_func(report_date, **kwargs)
    if df.empty and not cache_empty:
        return df
    
    # 3. Сохраняем в кэш
    temp_path = CACHE_DIR / (
        f".{CACHE_VERSION}_{metric_name}_{report_date}_"
        f"{uuid.uuid4().hex}.tmp.parquet"
    )

    try:
        df.to_parquet(temp_path, index=False)
        os.replace(temp_path, file_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    logger.info("[CACHE] Сохранено %s", file_path)

    return df

# Публичные функции кеширования — сохраняют привычные имена,
# чтобы не пришлось менять импорты в остальных модулях проекта.

def get_daily_revenue_cached(report_date: str) -> pd.DataFrame:
    return _get_cached_or_fetch(report_date, "revenue", fetch_daily_revenue)


def get_active_subscriptions_cached(report_date: str) -> pd.DataFrame:
    return _get_cached_or_fetch(
        report_date, "active_subs", fetch_active_subscriptions
    )


def get_churn_cached(report_date: str) -> pd.DataFrame:
    return _get_cached_or_fetch(report_date, "churn", fetch_churn)


def get_churn_by_channel_cached(report_date: str) -> pd.DataFrame:
    return _get_cached_or_fetch(
        report_date, "churn_by_channel", fetch_churn_by_channel
    )


def get_new_clients_cached(report_date: str) -> pd.DataFrame:
    return _get_cached_or_fetch(report_date, "new_clients", fetch_new_clients)


def get_sessions_cached(report_date: str) -> pd.DataFrame:
    return _get_cached_or_fetch(
        report_date, "sessions", fetch_daily_sessions
    )


def get_daily_payments_by_method_cached(report_date: str) -> pd.DataFrame:
    return _get_cached_or_fetch(
        report_date, "payments_by_method", fetch_daily_payments_by_method
    )


def get_cbr_rate_cached(report_date: str) -> pd.DataFrame:
    return _get_cached_or_fetch(report_date, "cbr_official_rate", fetch_cbr_currency_rate, cache_empty=False)
