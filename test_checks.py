from prime.data import get_daily_revenue_cached

test_date = "2026-08-03"

print("Первый запуск (должен пойти в базу):")
print(get_daily_revenue_cached(test_date))

print("\nВторой запуск (должен прочитать из кэша):")
print(get_daily_revenue_cached(test_date))