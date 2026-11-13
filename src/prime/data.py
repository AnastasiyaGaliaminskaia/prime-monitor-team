import logging
import pandas as pd
from sqlalchemy import text
from .config import get_engine

logger = logging.getLogger(__name__)


def _get_base_filter() -> str:
   
    engine = get_engine()
    query = text("SELECT client_filter FROM prime.assignments WHERE login = current_user")
    
    try:
        with engine.connect() as conn:
            result = conn.execute(query).fetchone()
            if result:
                return result[0]
            else:
                raise ValueError("Не удалось получить фильтр клиентов для текущего пользователя.")
    except Exception as e:
        logger.error(f"Ошибка при получении базового фильтра: {e}")
        raise


def fetch_daily_revenue(report_date: str) -> pd.DataFrame:
    
    engine = get_engine()
    base_filter = _get_base_filter()
    
    # Мы используем :date как параметр запроса
    query = text(f"""
        SELECT 
            DATE(p.paid_at) as payment_date,
            COUNT(p.payment_id) as payments_count,
            SUM(p.amount) as total_revenue
        FROM prime.payments p
        JOIN prime.subscriptions s ON s.subscription_id = p.subscription_id
        WHERE s.owner_client_id IN (SELECT client_id FROM prime.clients WHERE {base_filter})
          AND p.status = 'success'
          AND DATE(p.paid_at) = :date
        GROUP BY DATE(p.paid_at)
    """)
    
    logger.info(f"Загрузка выручки за дату: {report_date}")
    
    try:
        with engine.connect() as conn:
            # Устанавливаем часовой пояс, как в вашем ноутбуке
            conn.execute(text("set time zone 'UTC'"))
            df = pd.read_sql(query, conn, params={"date": report_date})
            return df
    except Exception as e:
        logger.error(f"Ошибка при загрузке выручки за {report_date}: {e}")
        raise


def fetch_daily_new_clients(report_date: str) -> pd.DataFrame:
    
    engine = get_engine()
    base_filter = _get_base_filter()
    
    query = text(f"""
        SELECT 
            DATE(registered_at) as registration_date,
            COUNT(client_id) as new_clients_count
        FROM prime.clients
        WHERE {base_filter}
          AND DATE(registered_at) = :date
        GROUP BY DATE(registered_at)
    """)
    
    logger.info(f"Загрузка новых клиентов за дату: {report_date}")
    
    try:
        with engine.connect() as conn:
            conn.execute(text("set time zone 'UTC'"))
            df = pd.read_sql(query, conn, params={"date": report_date})
            return df
    except Exception as e:
        logger.error(f"Ошибка при загрузке новых клиентов за {report_date}: {e}")
        raise


def fetch_daily_sessions(report_date: str) -> pd.DataFrame:
    
    engine = get_engine()
    base_filter = _get_base_filter()
    
    query = text(f"""
        SELECT 
            DATE(started_at) as session_date,
            COUNT(*) as sessions_count
        FROM prime.service_usage
        WHERE {base_filter}
          AND DATE(started_at) = :date
        GROUP BY DATE(started_at)
    """)
    
    logger.info(f"Загрузка сессий за дату: {report_date}")
    
    try:
        with engine.connect() as conn:
            conn.execute(text("set time zone 'UTC'"))
            df = pd.read_sql(query, conn, params={"date": report_date})
            return df
    except Exception as e:
        logger.error(f"Ошибка при загрузке сессий за {report_date}: {e}")
        raise




def fetch_active_subscriptions(report_date: str) -> pd.DataFrame:
    
    engine = get_engine()
    base_filter = _get_base_filter()
    
    # Мы прибавляем 1 день к report_date, чтобы включить сам день отчета.
    # Например, если отчет за 24 августа, мы смотрим подписки, активные на 25 августа 00:00.
    query = text(f"""
        SELECT
    CAST(:date AS DATE) as report_date,
    COUNT(subscription_id) as active_subs
FROM prime.subscriptions
WHERE owner_client_id IN (SELECT client_id FROM prime.clients WHERE {base_filter})
  AND CAST(started_at AS DATE) < CAST(:date AS DATE) + INTERVAL '1 day'
  AND (ended_at IS NULL OR CAST(ended_at AS DATE) >= CAST(:date AS DATE))
    """)
    
    logger.info(f"Загрузка активных подписок на дату: {report_date}")
    
    try:
        with engine.connect() as conn:
            conn.execute(text("set time zone 'UTC'"))
            df = pd.read_sql(query, conn, params={"date": report_date})
            return df
    except Exception as e:
        logger.error(f"Ошибка при загрузке активных подписок на {report_date}: {e}")
        raise


def fetch_churn(report_date: str) -> pd.DataFrame:
    
    engine = get_engine()
    base_filter = _get_base_filter()
    
    query = text(f"""
        SELECT 
            CAST(ended_at AS DATE) as churn_date,
            COUNT(subscription_id) as churned_subs
        FROM prime.subscriptions
        WHERE owner_client_id IN (SELECT client_id FROM prime.clients WHERE {base_filter})
          AND CAST(ended_at AS DATE) = CAST(:date AS DATE)
        GROUP BY CAST(ended_at AS DATE)  
    """)
    
    logger.info(f"Загрузка оттока за дату: {report_date}")
    
    try:
        with engine.connect() as conn:
            conn.execute(text("set time zone 'UTC'"))
            df = pd.read_sql(query, conn, params={"date": report_date})
            return df
    except Exception as e:
        logger.error(f"Ошибка при загрузке оттока за {report_date}: {e}")
        raise


def fetch_new_clients(report_date: str) -> pd.DataFrame:
    
    engine = get_engine()
    base_filter = _get_base_filter()
    
    query = text(f"""
        SELECT 
            CAST(registered_at AS DATE) as registration_date,
            COUNT(client_id) as new_clients
        FROM prime.clients
        WHERE {base_filter}
          AND CAST(registered_at AS DATE) = CAST(:date AS DATE)
    """)
    
    logger.info(f"Загрузка новых клиентов за дату: {report_date}")
    
    try:
        with engine.connect() as conn:
            conn.execute(text("set time zone 'UTC'"))
            df = pd.read_sql(query, conn, params={"date": report_date})
            return df
    except Exception as e:
        logger.error(f"Ошибка при загрузке новых клиентов за {report_date}: {e}")
        raise


def fetch_daily_payments_by_method(report_date: str) -> pd.DataFrame:
    
    engine = get_engine()
    base_filter = _get_base_filter()
    
    query = text(f"""
        SELECT 
            p.payment_method,
            COUNT(p.payment_id) as payments_count,
            SUM(p.amount) as total_amount
        FROM prime.payments p
        JOIN prime.subscriptions s ON s.subscription_id = p.subscription_id
        WHERE s.owner_client_id IN (SELECT client_id FROM prime.clients WHERE {base_filter})
          AND p.status = 'success'
          AND CAST(p.paid_at AS DATE) = CAST(:date AS DATE)
        GROUP BY p.payment_method
    """)
    
    logger.info(f"Загрузка платежей по методам за дату: {report_date}")
    
    try:
        with engine.connect() as conn:
            conn.execute(text("set time zone 'UTC'"))
            df = pd.read_sql(query, conn, params={"date": report_date})
            return df
    except Exception as e:
        logger.error(f"Ошибка при загрузке платежей по методам за {report_date}: {e}")
        raise