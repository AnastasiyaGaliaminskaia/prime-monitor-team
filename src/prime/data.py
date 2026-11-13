import logging
import pandas as pd
from sqlalchemy import text
from .config import get_engine

# Настраиваем логгер для этого модуля
logger = logging.getLogger(__name__)


def _get_base_filter() -> str:
    """
    Вспомогательная функция: получает фильтр сегмента (ваш регион) из базы.
    Так как база общая, мы должны фильтровать данные.
    """
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
    """
    Загружает агрегированную выручку и количество платежей за конкретный день.
    report_date: строка в формате 'YYYY-MM-DD'
    """
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
    """
    Загружает количество новых зарегистрированных клиентов за конкретный день.
    """
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
    """
    Загружает количество сессий (использований сервисов) за конкретный день.
    """
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