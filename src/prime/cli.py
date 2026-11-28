"""Команды отчёта и повторного прогона ПРАЙМ."""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from .config import load_replay_start
from .replay import DEFAULT_STORE, run_backfill, run_next_day
from .report import ReportDateError, build_day, format_report

log = logging.getLogger("prime")


def main() -> int:
    parser = argparse.ArgumentParser(prog="prime")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="отчёт ПРАЙМ за дату")
    report.add_argument("--date", type=date.fromisoformat, required=True, help="ГГГГ-ММ-ДД")
    commands.add_parser("replay", help="обработать следующую дату отчёта")
    backfill = commands.add_parser("backfill", help="пересчитать диапазон дат отчёта включительно")
    backfill.add_argument("--start", type=date.fromisoformat, required=True, help="ГГГГ-ММ-ДД")
    backfill.add_argument("--end", type=date.fromisoformat, required=True, help="ГГГГ-ММ-ДД")
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
        log.info("Запуск: %s", args)
        if args.command == "report":
            print(format_report(build_day(args.date)))
        elif args.command == "replay":
            result = run_next_day(load_replay_start())
            if result:
                day, report_text = result
                log.info("Replay: отчёт сохранён за %s", day)
                print(report_text)
            else:
                print("История ПРАЙМ обработана до конца.")
        else:
            saved = run_backfill(args.start, args.end)
            log.info("Backfill: сохранено отчётов: %d", saved)
            print(f"Пересчитано отчётов: {saved}. Результаты сохранены в {DEFAULT_STORE}.")
    except ReportDateError as error:
        log.error("%s", error)
        print(error, file=sys.stderr)
        return 1
    except Exception as error:
        log.error("Команда %s завершилась с ошибкой (%s)", args.command, type(error).__name__)
        print(f"Не удалось выполнить команду ({type(error).__name__}).", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
