# Утилиты для аудит-логирования и rate limiting

from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.exc import DBAPIError
from fastapi import Request
from .models import AuditLog, LoginAttempt, UserBlock
import asyncio

# Константы для rate limiting
MAX_LOGIN_ATTEMPTS = 5  # максимум попыток входа
ATTEMPT_WINDOW_MINUTES = 15  # за период в минутах
BLOCK_DURATION_MINUTES = 30  # длительность блокировки

def get_client_ip(request: Request) -> str:
    """Получить IP адрес клиента с учётом прокси"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"

def get_user_agent(request: Request) -> str:
    """Получить User-Agent клиента"""
    return request.headers.get("User-Agent", "unknown")

async def log_action(
    db: AsyncSession,
    action: str,
    request: Request = None,
    user_id: Optional[int] = None,
    emp_no: Optional[str] = None,
    company_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
    severity: str = "INFO"
):
    """
    Записать действие в аудит-лог
    
    Args:
        db: сессия БД
        action: тип действия (LOGIN, LOGOUT, PASS_INSTRUCTION, FAIL_LOGIN, etc.)
        request: объект запроса FastAPI
        user_id: ID пользователя
        emp_no: табельный номер
        company_id: ID компании
        details: дополнительная информация (dict)
        severity: уровень критичности (INFO, WARNING, ERROR, CRITICAL)
    """
    ip_address = None
    user_agent = None
    
    if request:
        ip_address = get_client_ip(request)
        user_agent = get_user_agent(request)
    
    log_entry = AuditLog(
        user_id=user_id,
        emp_no=emp_no,
        company_id=company_id,
        action=action,
        ip_address=ip_address,
        user_agent=user_agent,
        details=details,
        severity=severity
    )
    
    db.add(log_entry)
    await db.commit()

async def record_login_attempt(
    db: AsyncSession,
    emp_no: str,
    ip_address: str,
    success: bool
) -> None:
    """Записать попытку входа"""
    attempt = LoginAttempt(
        emp_no=emp_no,
        ip_address=ip_address,
        success=success
    )
    db.add(attempt)
    await db.commit()

async def check_rate_limit(db: AsyncSession, emp_no: str, ip_address: str) -> tuple[bool, Optional[datetime]]:
    """
    Проверить, не превышен ли лимит попыток входа
    
    Returns:
        (is_blocked, blocked_until): флаг блокировки и время до которого заблокирован
    """
    now = datetime.utcnow()
    
    # Проверяем активную блокировку с retry при connection error
    for attempt in range(3):
        try:
            result = await db.execute(
                select(UserBlock).where(
                    UserBlock.emp_no == emp_no,
                    UserBlock.blocked_until > now
                )
            )
            block = result.scalar_one_or_none()
            break
        except DBAPIError as e:
            if "connection is closed" in str(e) and attempt < 2:
                await asyncio.sleep(0.1 * (attempt + 1))  # Exponential backoff
                await db.rollback()
                continue
            raise
    
    if block:
        return True, block.blocked_until
    
    # Если блокировка истекла, удаляем запись
    if block and block.blocked_until <= now:
        await db.delete(block)
        await db.commit()
    
    # Считаем неудачные попытки за последние N минут
    window_start = now - timedelta(minutes=ATTEMPT_WINDOW_MINUTES)
    
    result = await db.execute(
        select(LoginAttempt).where(
            LoginAttempt.emp_no == emp_no,
            LoginAttempt.attempt_time >= window_start,
            LoginAttempt.success == False
        )
    )
    failed_attempts = len(result.scalars().all())
    
    # Если превышен лимит - создаём блокировку
    if failed_attempts >= MAX_LOGIN_ATTEMPTS:
        blocked_until = now + timedelta(minutes=BLOCK_DURATION_MINUTES)
        
        new_block = UserBlock(
            emp_no=emp_no,
            blocked_until=blocked_until,
            reason=f"Превышен лимит попыток входа ({MAX_LOGIN_ATTEMPTS} за {ATTEMPT_WINDOW_MINUTES} минут)"
        )
        
        db.add(new_block)
        await db.commit()
        
        return True, blocked_until
    
    return False, None

async def cleanup_old_attempts(db: AsyncSession, days: int = 30):
    """Очистка старых записей попыток входа"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    await db.execute(
        delete(LoginAttempt).where(LoginAttempt.attempt_time < cutoff)
    )
    
    await db.commit()

async def get_user_activity(db: AsyncSession, emp_no: str, limit: int = 100):
    """Получить последние действия пользователя"""
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.emp_no == emp_no)
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
    )
    return result.scalars().all()

async def get_suspicious_activity(db: AsyncSession, hours: int = 24):
    """Получить подозрительную активность за последние N часов"""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    
    # Поиск множественных неудачных попыток входа
    result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.timestamp >= cutoff,
            AuditLog.severity.in_(['WARNING', 'ERROR', 'CRITICAL'])
        )
        .order_by(AuditLog.timestamp.desc())
    )
    return result.scalars().all()
