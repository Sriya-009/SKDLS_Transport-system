import logging
import os
from logging.handlers import RotatingFileHandler


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
LOGGER_NAME = "transport_app"
MAX_LOG_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


class MaxLevelFilter(logging.Filter):
    def __init__(self, max_level):
        super().__init__()
        self.max_level = max_level

    def filter(self, record):
        return record.levelno < self.max_level


def setup_logger():
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_transport_logger_configured", False):
        return logger

    logs_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(logs_dir, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    application_handler = RotatingFileHandler(
        os.path.join(logs_dir, "application.log"),
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    application_handler.setLevel(logging.INFO)
    application_handler.addFilter(MaxLevelFilter(logging.ERROR))
    application_handler.setFormatter(formatter)

    error_handler = RotatingFileHandler(
        os.path.join(logs_dir, "error.log"),
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(application_handler)
    logger.addHandler(error_handler)
    logger.addHandler(console_handler)
    logger._transport_logger_configured = True
    return logger


logger = setup_logger()


def log_print(*args, **kwargs):
    message = " ".join(str(arg) for arg in args)
    level = kwargs.pop("level", None)
    exc_info = kwargs.pop("exc_info", None)
    stack_info = kwargs.pop("stack_info", False)

    if message.startswith("[GPS Simulator]"):
        message = message.replace("[GPS Simulator]", "[gps]", 1)

    if level:
        getattr(logger, level)(message, exc_info=exc_info, stack_info=stack_info)
        return

    lowered = message.lower()
    if "[error]" in lowered or " error" in lowered or "failed" in lowered or "exception" in lowered:
        logger.error(message, exc_info=exc_info, stack_info=stack_info)
    elif "[warning]" in lowered or " warning" in lowered:
        logger.warning(message)
    else:
        logger.info(message)
