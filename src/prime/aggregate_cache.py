"""Локальный Parquet-кэш SQL-агрегатов отчёта."""

import json
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import date
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd

log = logging.getLogger(__name__)
DEFAULT_CACHE_DIR = Path("data/analytics_global_v1")


class CacheRefreshError(RuntimeError):
    """Запрошенное обновление Parquet-кэша не удалось."""


class AggregateCache:
    def __init__(self, directory: Path = DEFAULT_CACHE_DIR, *, refresh: bool = False):
        self.directory = directory
        self.refresh = refresh

    def read(self, name: str, params: dict, fetch: Callable[[], pd.DataFrame]) -> pd.DataFrame:
        key = json.dumps(params, sort_keys=True, default=date.isoformat)
        digest = sha256(key.encode("utf-8")).hexdigest()
        path = self.directory / f"{name}_{digest}.parquet"

        if not self.refresh:
            try:
                frame = pd.read_parquet(path)
            except FileNotFoundError:
                pass
            except (OSError, ValueError):
                log.warning("Не удалось прочитать кэш %s, загружаем из БД", path.name)
            else:
                log.info("Кэш: %s", path.name)
                return frame

        frame = fetch()
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
            frame.to_parquet(temporary, index=False)
            temporary.replace(path)
        except (OSError, ValueError) as error:
            if self.refresh:
                raise CacheRefreshError(
                    f"Не удалось обновить Parquet-кэш: {path}. "
                    "Если файл открыт, закройте его и повторите backfill."
                ) from error
            log.warning("Не удалось записать кэш %s", path.name)
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
        return frame
