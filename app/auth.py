from datetime import datetime, timedelta, timezone
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import jwt, JWTError
from passlib.hash import argon2
from pydantic import BaseModel
from sqlalchemy import select
from .db import SessionLocal
from .models_auth import User
from .settings import get_settings
from .audit import (
    log_action, 
    record_login_attempt, 
    check_rate_limit, 
    get_client_ip
)

logger = logging.getLogger(__name__)

settings = get_settings()
router = APIRouter(prefix="/api/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Используем secret_key для JWT, если доступен, иначе fallback на sign_secret
SECRET_KEY = settings.secret_key if hasattr(settings, 'secret_key') else settings.sign_secret
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str  # Добавляем роль для редиректа на фронте

async def get_db():
    async with SessionLocal() as s:
        yield s

@router.post("/login", response_model=Token)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(), 
    db=Depends(get_db)
):
    ip_address = get_client_ip(request)
    
    # Проверка rate limiting
    is_blocked, blocked_until = await check_rate_limit(db, form_data.username, ip_address)
    if is_blocked:
        minutes_left = int((blocked_until - datetime.utcnow()).total_seconds() / 60)
        logger.warning(f"Login blocked for user={form_data.username}, ip={ip_address}, blocked_until={blocked_until}")
        await log_action(
            db=db,
            action="LOGIN_BLOCKED",
            request=request,
            emp_no=form_data.username,
            details={"reason": "rate_limit", "blocked_until": blocked_until.isoformat()},
            severity="WARNING"
        )
        raise HTTPException(
            429, 
            f"Слишком много попыток входа. Попробуйте через {minutes_left} минут."
        )
    
    # Поиск пользователя
    q = select(User).where(User.login == form_data.username)
    user = (await db.execute(q)).scalar_one_or_none()
    
    # Проверка credentials
    if not user or not user.active or not argon2.verify(form_data.password, user.pass_hash):
        # Записываем неудачную попытку
        await record_login_attempt(db, form_data.username, ip_address, success=False)
        logger.warning(f"Login failed for user={form_data.username}, ip={ip_address}")
        await log_action(
            db=db,
            action="LOGIN_FAILED",
            request=request,
            emp_no=form_data.username,
            details={"reason": "invalid_credentials"},
            severity="WARNING"
        )
        raise HTTPException(401, "Неверный логин или пароль")
    
    # Успешный вход
    await record_login_attempt(db, form_data.username, ip_address, success=True)
    logger.info(f"Login successful for user={user.login}, role={user.role}, company_id={user.scope_company_id}, ip={ip_address}")
    await log_action(
        db=db,
        action="LOGIN_SUCCESS",
        request=request,
        user_id=user.id,
        emp_no=user.login,
        company_id=user.scope_company_id,
        details={"role": user.role},
        severity="INFO"
    )
    
    to_encode = {
        "sub": user.login,
        "uid": user.id,
        "role": user.role,
        "scope_company_id": user.scope_company_id,
        "scope_orgunit_id": user.scope_orgunit_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    token = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return Token(access_token=token, role=user.role)

async def get_current_user(token: str = Depends(oauth2_scheme), db=Depends(get_db)) -> User:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        login = payload.get("sub")
        if not login:
            raise HTTPException(401, "Bad token")
    except JWTError:
        raise HTTPException(401, "Bad token")
    user = (await db.execute(select(User).where(User.login == login))).scalar_one_or_none()
    if not user or not user.active:
        raise HTTPException(401, "Inactive user")
    return user
