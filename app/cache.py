"""
Redis кэширование для FastAPI приложения
С автоматическим fallback на БД при недоступности Redis
"""

import json
import logging
from typing import Optional, Any, Callable
from functools import wraps
import asyncio

try:
    from redis.asyncio import Redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    Redis = None

logger = logging.getLogger(__name__)

# Глобальный клиент Redis
_redis_client: Optional[Redis] = None
_redis_enabled = REDIS_AVAILABLE


async def init_redis(host: str = 'localhost', port: int = 6379, db: int = 0) -> bool:
    """
    Инициализация Redis клиента
    Возвращает True если успешно, False если Redis недоступен
    """
    global _redis_client, _redis_enabled
    
    if not REDIS_AVAILABLE:
        logger.warning("Redis library not installed. Caching disabled.")
        _redis_enabled = False
        return False
    
    try:
        _redis_client = Redis(
            host=host,
            port=port,
            db=db,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2
        )
        
        # Проверка подключения
        await _redis_client.ping()
        logger.info(f"Redis connected successfully at {host}:{port}")
        _redis_enabled = True
        return True
        
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}. Caching disabled, using database fallback.")
        _redis_client = None
        _redis_enabled = False
        return False


async def get_redis() -> Optional[Redis]:
    """Получить Redis клиент если доступен"""
    return _redis_client if _redis_enabled else None


def is_redis_available() -> bool:
    """Проверить доступность Redis"""
    return _redis_enabled and _redis_client is not None


async def cache_get(key: str) -> Optional[Any]:
    """
    Получить значение из кэша
    Возвращает None если ключ не найден или Redis недоступен
    """
    if not is_redis_available():
        return None
    
    try:
        value = await _redis_client.get(key)
        if value:
            return json.loads(value)
        return None
    except Exception as e:
        logger.error(f"Redis GET error for key '{key}': {e}")
        return None


async def cache_set(key: str, value: Any, ttl: int = 300) -> bool:
    """
    Сохранить значение в кэш
    
    Args:
        key: ключ
        value: значение (будет сериализовано в JSON)
        ttl: время жизни в секундах (по умолчанию 5 минут)
    
    Returns:
        True если успешно, False если ошибка
    """
    if not is_redis_available():
        return False
    
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        await _redis_client.setex(key, ttl, serialized)
        return True
    except Exception as e:
        logger.error(f"Redis SET error for key '{key}': {e}")
        return False


async def cache_delete(key: str) -> bool:
    """Удалить ключ из кэша"""
    if not is_redis_available():
        return False
    
    try:
        await _redis_client.delete(key)
        return True
    except Exception as e:
        logger.error(f"Redis DELETE error for key '{key}': {e}")
        return False


async def cache_delete_pattern(pattern: str) -> int:
    """
    Удалить все ключи по шаблону
    Например: "employees:*" удалит все ключи начинающиеся с "employees:"
    
    Returns:
        Количество удаленных ключей
    """
    if not is_redis_available():
        return 0
    
    try:
        keys = []
        async for key in _redis_client.scan_iter(match=pattern):
            keys.append(key)
        
        if keys:
            deleted = await _redis_client.delete(*keys)
            logger.info(f"Deleted {deleted} keys matching pattern '{pattern}'")
            return deleted
        return 0
    except Exception as e:
        logger.error(f"Redis DELETE PATTERN error for '{pattern}': {e}")
        return 0


async def cache_clear() -> bool:
    """Очистить весь кэш (использовать осторожно!)"""
    if not is_redis_available():
        return False
    
    try:
        await _redis_client.flushdb()
        logger.info("Redis cache cleared")
        return True
    except Exception as e:
        logger.error(f"Redis FLUSHDB error: {e}")
        return False


def cached(
    key_prefix: str,
    ttl: int = 300,
    key_builder: Optional[Callable] = None
):
    """
    Декоратор для кэширования результатов функций
    
    Args:
        key_prefix: префикс для ключа кэша
        ttl: время жизни в секундах
        key_builder: функция для построения ключа из аргументов
    
    Пример:
        @cached("employees", ttl=300)
        async def get_employees(company_id: int):
            # ключ будет: "employees:{company_id}"
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Построить ключ кэша
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                # Простое построение ключа из аргументов
                key_parts = [key_prefix]
                
                # Добавить позиционные аргументы (кроме self)
                for arg in args:
                    if not (hasattr(arg, '__class__') and arg.__class__.__name__ in ['AsyncSession', 'Request']):
                        key_parts.append(str(arg))
                
                # Добавить именованные аргументы
                for k, v in sorted(kwargs.items()):
                    if k not in ['db', 'request']:  # Пропустить служебные аргументы
                        key_parts.append(f"{k}:{v}")
                
                cache_key = ":".join(key_parts)
            
            # Попытка получить из кэша
            cached_value = await cache_get(cache_key)
            if cached_value is not None:
                logger.debug(f"Cache HIT: {cache_key}")
                return cached_value
            
            # Вызвать оригинальную функцию
            logger.debug(f"Cache MISS: {cache_key}")
            result = await func(*args, **kwargs)
            
            # Сохранить в кэш
            await cache_set(cache_key, result, ttl)
            
            return result
        
        return wrapper
    return decorator


async def get_cache_stats() -> dict:
    """Получить статистику кэша"""
    if not is_redis_available():
        return {
            "enabled": False,
            "error": "Redis not available"
        }
    
    try:
        info = await _redis_client.info()
        return {
            "enabled": True,
            "connected_clients": info.get("connected_clients", 0),
            "used_memory_human": info.get("used_memory_human", "0"),
            "total_keys": await _redis_client.dbsize(),
            "hits": info.get("keyspace_hits", 0),
            "misses": info.get("keyspace_misses", 0),
            "hit_rate": (
                info.get("keyspace_hits", 0) / 
                max(info.get("keyspace_hits", 0) + info.get("keyspace_misses", 0), 1) * 100
            )
        }
    except Exception as e:
        logger.error(f"Error getting cache stats: {e}")
        return {"enabled": False, "error": str(e)}


# Закрытие соединения при завершении приложения
async def close_redis():
    """Закрыть соединение с Redis"""
    global _redis_client, _redis_enabled
    
    if _redis_client:
        try:
            await _redis_client.close()
            logger.info("Redis connection closed")
        except Exception as e:
            logger.error(f"Error closing Redis: {e}")
        finally:
            _redis_client = None
            _redis_enabled = False
