import logging
import pandas as pd
from prime.data import fetch_daily_sessions, fetch_churn, fetch_new_clients
from prime.checks import check_sessions_not_zero, check_churn_spike, check_new_clients_spike

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

test_date = "2026-08-03"

print("--- Тестируем новые проверки ---")

# Загружаем данные
df_sessions = fetch_daily_sessions(test_date)
df_churn = fetch_churn(test_date)
df_new_clients = fetch_new_clients(test_date)

# Тестируем проверки на реальных данных
print(f"Сессии (ожидаем True): {check_sessions_not_zero(test_date, df_sessions)}")
print(f"Отток (ожидаем True): {check_churn_spike(test_date, df_churn)}")
print(f"Новые клиенты (ожидаем True): {check_new_clients_spike(test_date, df_new_clients)}")

print("\n--- Искусственно ломаем данные ---")
# Создаем фейковые данные с аномальным оттоком
fake_churn = pd.DataFrame({"churned_subs": [5000]})
print(f"Аномальный отток (ожидаем False): {check_churn_spike(test_date, fake_churn)}")