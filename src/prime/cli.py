"""Команда запуска отчёта ПРАЙМ."""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from .report import build_day, format_report

log = logging.getLogger("prime")


def main() -> int:
    parser = argparse.ArgumentParser(prog="prime")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="отчёт ПРАЙМ за дату")
    report.add_argument("--date", type=date.fromisoformat, required=True, help="ГГГГ-ММ-ДД")
    args = parser.parse_args()

    try:
        path = Path("logs/prime.log")
        path.parent.mkdir(exist_ok=True)
        logging.basicConfig(
            filename=path,
            encoding="utf-8",
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        log.info("Запуск отчёта за %s", args.date)
        row = build_day(args.date)
    except ValueError as error:
        log.error("Некорректный ввод или данные для даты %s", args.date)
        # Сообщение нашего ограничения диапазона не содержит реквизитов подключения.
        if str(error).startswith("Дата отчёта вне диапазона"):
            print(error, file=sys.stderr)
        else:
            print("Некорректные данные для расчёта отчёта.", file=sys.stderr)
        return 1
    except Exception as error:
        log.error("Отчёт за %s не построен (%s)", args.date, type(error).__name__)
        print(f"Не удалось построить отчёт ({type(error).__name__}).", file=sys.stderr)
        return 1

    print(format_report(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
