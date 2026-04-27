"""
Конфигурация логирования с ротацией файлов
"""
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

def setup_logging(
    log_dir: Optional[str] = None,
    log_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
    console_output: bool = True
) -> logging.Logger:
    """
    Настройка системы логирования с ротацией файлов
    
    Args:
        log_dir: Директория для логов (по умолчанию /var/log/instr или ./logs)
        log_level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        max_bytes: Максимальный размер файла лога (по умолчанию 10 MB)
        backup_count: Количество ротируемых файлов (по умолчанию 5)
        console_output: Выводить ли логи в консоль (по умолчанию True)
    
    Returns:
        Настроенный логгер
    """
    
    # Определяем директорию для логов
    if log_dir is None:
        # Пробуем /var/log/instr для production
        log_path = Path("/var/log/instr")
        if not log_path.exists():
            try:
                log_path.mkdir(parents=True, exist_ok=True)
            except PermissionError:
                # Fallback на локальную директорию
                log_path = Path(__file__).parent.parent / "logs"
                log_path.mkdir(parents=True, exist_ok=True)
    else:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
    
    # Файлы логов
    app_log_file = log_path / "app.log"
    error_log_file = log_path / "error.log"
    access_log_file = log_path / "access.log"
    
    # Формат логов
    log_format = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Формат для access логов (более краткий)
    access_format = logging.Formatter(
        fmt='%(asctime)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Очищаем существующие handlers
    root_logger.handlers.clear()
    
    # 1. Handler для всех логов (INFO и выше) -> app.log
    app_handler = RotatingFileHandler(
        app_log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(log_format)
    root_logger.addHandler(app_handler)
    
    # 2. Handler для ошибок (ERROR и выше) -> error.log
    error_handler = RotatingFileHandler(
        error_log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(log_format)
    root_logger.addHandler(error_handler)
    
    # 3. Handler для access логов (отдельный логгер)
    access_logger = logging.getLogger("access")
    access_logger.setLevel(logging.INFO)
    access_logger.propagate = False  # Не передавать в root logger
    
    access_handler = RotatingFileHandler(
        access_log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    access_handler.setLevel(logging.INFO)
    access_handler.setFormatter(access_format)
    access_logger.addHandler(access_handler)
    
    # 4. Console handler (опционально)
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(log_format)
        root_logger.addHandler(console_handler)
    
    # Логируем успешную инициализацию
    root_logger.info(f"Logging initialized: log_dir={log_path}, level={log_level}, max_size={max_bytes/1024/1024}MB, backups={backup_count}")
    
    return root_logger


def get_access_logger() -> logging.Logger:
    """Получить логгер для access логов"""
    return logging.getLogger("access")
