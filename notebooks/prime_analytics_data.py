"""Импорт витрины ПРАЙМ из аналитических ноутбуков."""

from prime.analytics_data import load_churn_cohorts, load_daily

__all__ = ["load_daily", "load_churn_cohorts"]
