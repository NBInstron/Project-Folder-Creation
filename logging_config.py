import logging
import datetime
from pathlib import Path
from typing import Optional


class LevelFilter(logging.Filter):
    def __init__(self, min_level: int = 0, max_level: Optional[int] = None):
        super().__init__()
        self.min_level = min_level
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < self.min_level:
            return False
        if self.max_level is not None and record.levelno > self.max_level:
            return False
        return True


class DailyFileHandler(logging.Handler):
    """Handler that writes logs to a file named by date (YYYY-MM-DD.log).

    It will automatically switch files when the date changes.
    """

    def __init__(self, folder: Path, level: int = logging.NOTSET, encoding: str = "utf-8"):
        super().__init__(level)
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.encoding = encoding
        self.current_date: Optional[datetime.date] = None
        self._file_handler: Optional[logging.FileHandler] = None
        self._open_for_date(datetime.date.today())

    def _filename_for_date(self, date: datetime.date) -> Path:
        return self.folder / f"{date.strftime('%Y-%m-%d')}.log"

    def _open_for_date(self, date: datetime.date) -> None:
        fname = self._filename_for_date(date)
        fh = logging.FileHandler(fname, encoding=self.encoding)
        fh.setLevel(self.level)
        if self.formatter:
            fh.setFormatter(self.formatter)
        self._file_handler = fh
        self.current_date = date

    def emit(self, record: logging.LogRecord) -> None:
        try:
            record_date = datetime.date.fromtimestamp(record.created)
            if self.current_date is None or record_date != self.current_date:
                if self._file_handler:
                    try:
                        self._file_handler.close()
                    except Exception:
                        pass
                self._open_for_date(record_date)
            if self._file_handler:
                self._file_handler.emit(record)
        except Exception:
            self.handleError(record)

    def setFormatter(self, fmt: logging.Formatter) -> None:
        super().setFormatter(fmt)
        if self._file_handler:
            self._file_handler.setFormatter(fmt)

    def close(self) -> None:
        try:
            if self._file_handler:
                self._file_handler.close()
        finally:
            super().close()


def configure_logging(log_file: Path | str = "logs/app.log") -> None:
    base_path = Path(log_file)
    base_dir = base_path.parent if base_path.parent != Path('.') else Path("logs")

    event_dir = base_dir / "event_logs"
    error_dir = base_dir / "error_logs"

    event_dir.mkdir(parents=True, exist_ok=True)
    error_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Event handler: DEBUG and INFO
    event_handler = DailyFileHandler(event_dir, level=logging.DEBUG)
    event_handler.setFormatter(formatter)
    event_handler.addFilter(LevelFilter(min_level=logging.DEBUG, max_level=logging.INFO))

    # Error handler: ERROR and above
    error_handler = DailyFileHandler(error_dir, level=logging.ERROR)
    error_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Avoid adding duplicate handlers
    has_console = any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers)
    has_event = any(isinstance(h, DailyFileHandler) and getattr(h, "folder", None) == event_dir for h in root_logger.handlers)
    has_error = any(isinstance(h, DailyFileHandler) and getattr(h, "folder", None) == error_dir for h in root_logger.handlers)

    if not has_console:
        root_logger.addHandler(console_handler)
    if not has_event:
        root_logger.addHandler(event_handler)
    if not has_error:
        root_logger.addHandler(error_handler)

