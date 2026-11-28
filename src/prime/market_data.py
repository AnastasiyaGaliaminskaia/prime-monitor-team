"""Исторический рыночный контекст для отчёта ПРАЙМ."""

import logging
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from time import sleep
from xml.etree import ElementTree

import requests

log = logging.getLogger(__name__)

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
IMOEX_URL = "https://iss.moex.com/iss/history/engines/stock/markets/index/securities/IMOEX.json"
HTTP_TIMEOUT = (5, 8)


@dataclass(frozen=True)
class ExchangeRate:
    requested_date: date
    effective_date: date
    rubles_per_usd: float


@dataclass(frozen=True)
class IndexClose:
    trade_date: date
    value: float


@dataclass(frozen=True)
class IndexChange:
    latest: IndexClose
    previous: IndexClose | None

    @property
    def change(self) -> float | None:
        if self.previous is None or self.previous.trade_date == self.latest.trade_date:
            return None
        return self.latest.value / self.previous.value - 1


def _get(url: str, params: dict, source: str, *, verify: bool | str = True) -> requests.Response | None:
    for attempt in (1, 2):
        try:
            response = requests.get(url, params=params, timeout=HTTP_TIMEOUT, verify=verify)
            response.raise_for_status()
            return response
        except requests.exceptions.SSLError:
            log.warning("%s: не удалось проверить TLS-сертификат", source)
            return None
        except requests.exceptions.HTTPError as error:
            status = error.response.status_code
            log.warning("%s: попытка %d, HTTP %d", source, attempt, status)
            if status != 429 and not 500 <= status < 600:
                return None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as error:
            log.warning("%s: попытка %d, %s", source, attempt, type(error).__name__)
        except (requests.exceptions.RequestException, OSError) as error:
            log.warning("%s: запрос не выполнен (%s)", source, type(error).__name__)
            return None
        if attempt == 1:
            sleep(1)
    return None


def load_cbr_rate(report_date: date) -> ExchangeRate | None:
    """Курс USD/RUB, действовавший на дату отчёта."""
    source = "Банк России"
    response = _get(CBR_URL, {"date_req": report_date.strftime("%d/%m/%Y")}, source)
    if response is None:
        return None

    try:
        root = ElementTree.fromstring(response.content)
        effective_date = datetime.strptime(root.attrib["Date"], "%d.%m.%Y").date()
        usd = next(row for row in root.findall("Valute") if row.findtext("CharCode") == "USD")
        rate = float(usd.findtext("Value", "").replace(",", ".")) / int(usd.findtext("Nominal", ""))
        if effective_date > report_date or not math.isfinite(rate) or rate <= 0:
            raise ValueError("Курс не соответствует дате отчёта")
    except (ElementTree.ParseError, KeyError, StopIteration, ValueError, ZeroDivisionError):
        log.warning("%s: нет корректного USD/RUB на %s", source, report_date)
        return None

    return ExchangeRate(report_date, effective_date, rate)


def load_imoex(report_date: date) -> IndexChange | None:
    """Закрытия IMOEX на D−1 и D−8 с учётом доступных торговых дней."""
    source = "Мосбиржа ISS"
    latest_day = report_date - timedelta(days=1)
    previous_day = report_date - timedelta(days=8)
    # Две недели запаса до предыдущего среза покрывают обычные праздничные разрывы.
    first_day = previous_day - timedelta(days=14)
    local_ca = Path("data/moex-ca.pem")
    ca_bundle = os.getenv("PRIME_MOEX_CA_BUNDLE")
    if not ca_bundle and local_ca.is_file():
        ca_bundle = str(local_ca)
    response = _get(IMOEX_URL, {
        "from": first_day.isoformat(),
        "till": latest_day.isoformat(),
        "iss.meta": "off",
        "iss.only": "history",
        "history.columns": "TRADEDATE,CLOSE",
    }, source, verify=ca_bundle or True)
    if response is None:
        return None

    try:
        history = response.json()["history"]
        date_index = history["columns"].index("TRADEDATE")
        close_index = history["columns"].index("CLOSE")
        by_day = {}
        for row in history["data"]:
            if row[close_index] is None:
                continue
            trade_date = date.fromisoformat(row[date_index])
            close = float(row[close_index])
            if first_day <= trade_date <= latest_day and math.isfinite(close) and close > 0:
                if trade_date in by_day and by_day[trade_date] != close:
                    raise ValueError("Несколько значений закрытия за дату")
                by_day[trade_date] = close
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        log.warning("%s: некорректная история IMOEX на %s", source, report_date)
        return None

    latest = max(by_day, default=None)
    if latest is None:
        log.warning("%s: нет закрытий IMOEX к %s", source, latest_day)
        return None
    previous = max((day for day in by_day if day <= previous_day), default=None)
    return IndexChange(
        latest=IndexClose(latest, by_day[latest]),
        previous=IndexClose(previous, by_day[previous]) if previous is not None else None,
    )
