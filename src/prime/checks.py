import logging
import pandas as pd
from .data import (
    fetch_daily_revenue, 
    fetch_daily_sessions, 
    fetch_churn
)

logger = logging.getLogger(__name__)

def check_data_not_empty(report_date: str, df: pd.DataFrame, data_name: str) -> bool:
    """
    Проверка 1: Данные вообще есть?
    Если дата в прошлом, но данных нет — это проблема выгрузки.
    """
    if df.empty:
        logger.warning(f"[ПРОВЕРКА ДАННЫХ] Данные '{data_name}' за {report_date} пусты!")
        return False
    return True

def check_negative_revenue(report_date: str, df_revenue: pd.DataFrame) -> bool:
    """
    Проверка 2: Выручка не может быть отрицательной.
    """
    if not df_revenue.empty and (df_revenue['total_revenue'] < 0).any():
        logger.error(f"[ПРОВЕРКА ДАННЫХ] Отрицательная выручка за {report_date}!")
        return False
    return True

def check_sessions_drop(report_date: str, df_sessions: pd.DataFrame) -> bool:
    """
    Проверка 3: Резкое падение сессий (пример проверки на аномалию в данных).
    Если сессий ровно 0 в рабочий день — скорее всего, сломался трекер.
    """
    if not df_sessions.empty and df_sessions['sessions_count'].iloc[0] == 0:
        # Здесь можно добавить проверку на выходной день, но для простоты пока так
        logger.warning(f"[ПРОВЕРКА ДАННЫХ] Нулевое количество сессий за {report_date}. Возможен сбой трекера.")
        return False
    return True

def check_churn_spike(report_date: str, df_churn: pd.DataFrame) -> bool:
    """
    Проверка 4: Аномально высокий отток (например, больше 1000 за день).
    Порог нужно будет обосновать в README.
    """
    if not df_churn.empty and df_churn['churned_subs'].iloc[0] > 1000:
        logger.warning(f"[ПРОВЕРКА ДАННЫХ] Аномальный отток ({df_churn['churned_subs'].iloc[0]}) за {report_date}!")
        return False
    return True



def check_sessions_not_zero(report_date: str, df_sessions: pd.DataFrame) -> bool:
    """
    Проверка 3: Резкое падение сессий.
    Если сессий ровно 0, возможно, сломался трекер.
    """
    if not df_sessions.empty and df_sessions['sessions_count'].iloc[0] == 0:
        logger.warning(f"[ПРОВЕРКА ДАННЫХ] Нулевое количество сессий за {report_date}. Возможен сбой трекера.")
        return False
    return True

def check_churn_spike(report_date: str, df_churn: pd.DataFrame) -> bool:
    """
    Проверка 4: Аномально высокий отток.
    Порог 1000 - это пример. На защите нужно будет обосновать его.
    """
    if not df_churn.empty and df_churn['churned_subs'].iloc[0] > 1000:
        logger.warning(f"[ПРОВЕРКА ДАННЫХ] Аномальный отток ({df_churn['churned_subs'].iloc[0]}) за {report_date}!")
        return False
    return True

def check_new_clients_spike(report_date: str, df_clients: pd.DataFrame) -> bool:
    """
    Проверка 5: Всплеск регистраций.
    Порог 10000 для примера. Если больше - возможно, дубликаты или сбой.
    """
    if not df_clients.empty and df_clients['new_clients'].iloc[0] > 10000:
        logger.warning(f"[ПРОВЕРКА ДАННЫХ] Аномальный всплеск регистраций ({df_clients['new_clients'].iloc[0]}) за {report_date}!")
        return False
    return True