import os
import logging
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from itsdangerous import TimestampSigner, BadSignature
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from pathlib import Path
import time
import asyncio
import json
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from .settings import get_settings
from .logging_config import setup_logging, get_access_logger
from .db import engine, SessionLocal, Base
from .models import Session as Sess, Attendance
from .schemas import RecordIn
from .utils import save_dataurl_png
from .auth import router as auth_router, get_current_user, get_db
from .models_auth import User as UserModel
from .upload import router as upload_router
from .fcm import router as fcm_router
from .quiz import router as quiz_router
from .qr_files import router as qr_router
from .cache import init_redis, close_redis, get_cache_stats

class MeOut(BaseModel):
    login: str
    role: str
    scope_company_id: int | None = None
    scope_orgunit_id: int | None = None
    fio: str | None = None

class DashRecord(BaseModel):
    id: int
    session_id: int
    idnum: str
    fio: str
    signed_at: datetime | None = None
    type: str
    month: str
    file: str
    company_id: int | None = None
    company_name: str | None = None
    orgunit_id: int | None = None
    orgunit_name: str | None = None
    signatureLink: str | None = None
    instrSignatureLink: str | None = None
    birth_year: int | None = None
    birth_date: str | None = None
    instructorName: str | None = None


class SummaryItem(BaseModel):
    company_id: int | None = None
    company_name: str | None = None
    orgunit_id: int | None = None
    orgunit_name: str | None = None
    cnt: int

settings = get_settings()

# Инициализация логирования с ротацией файлов
logger = setup_logging(
    log_level=getattr(settings, 'log_level', 'INFO'),
    max_bytes=10 * 1024 * 1024,  # 10 MB
    backup_count=5,
    console_output=True
)
access_logger = get_access_logger()

# Инициализация Sentry (мониторинг ошибок)
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        integrations=[
            FastApiIntegration(),
            StarletteIntegration(),
        ],
        environment=getattr(settings, 'environment', 'production'),
        traces_sample_rate=1.0,
        send_default_pii=False,
        # Игнорируем ожидаемые ошибки FCM (устаревшие токены)
        before_send=lambda event, hint: None if (
            'exception' in event 
            and 'values' in event['exception'] 
            and any('UnregisteredError' in str(v.get('value', '')) or 'token invalid' in str(v.get('value', '')) 
                    for v in event['exception']['values'])
        ) else event,
    )
    logger.info(f"Sentry initialized for environment: {getattr(settings, 'environment', 'production')}")
else:
    logger.warning("Sentry DSN not configured, error monitoring disabled")

# Инициализация Rate Limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Instruktazh API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(auth_router)
app.include_router(upload_router)
app.include_router(fcm_router)
app.include_router(quiz_router)
app.include_router(qr_router)
app.mount("/web", StaticFiles(directory="web", html=True), name="web")


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# Middleware для заголовков безопасности и логирования
@app.middleware("http")
async def add_security_headers_and_logging(request: Request, call_next):
    global _request_count, _error_count
    start_time = time.time()
    
    # Увеличиваем счетчик запросов
    _request_count += 1
    
    # Логируем входящий запрос
    client_ip = request.client.host if request.client else "unknown"
    
    try:
        response = await call_next(request)
        
        # Добавляем заголовки безопасности
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        
        # Логируем успешный запрос
        process_time = time.time() - start_time
        access_logger.info(
            f"{client_ip} | {request.method} | {request.url.path} | "
            f"Status: {response.status_code} | Time: {process_time:.3f}s"
        )
        
        # Увеличиваем счетчик ошибок для 5xx
        if response.status_code >= 500:
            _error_count += 1
        
        return response
    
    except Exception as e:
        # Увеличиваем счетчик ошибок
        _error_count += 1
        
        # Логируем ошибку
        process_time = time.time() - start_time
        logger.error(
            f"Request failed: {client_ip} | {request.method} | {request.url.path} | "
            f"Error: {str(e)} | Time: {process_time:.3f}s",
            exc_info=True
        )
        raise

DATA_ROOT = os.getenv("DATA_ROOT", str((os.path.dirname(__file__)+"/../storage").replace("\\","/")))
SIG_DIR = os.path.abspath(os.path.join(DATA_ROOT, "signatures"))
DOC_DIR = os.path.abspath(os.path.join(DATA_ROOT, "instruktagi"))
INSTRUCTOR_SIG_DIR = os.path.abspath(os.path.join(DATA_ROOT, "instructor_signatures"))

app.mount("/instruktagi", StaticFiles(directory=DOC_DIR), name="instruktagi")
app.mount("/signatures", StaticFiles(directory=SIG_DIR), name="signatures")
app.mount("/instructor_signatures", StaticFiles(directory=INSTRUCTOR_SIG_DIR), name="instructor_signatures")

signer = TimestampSigner(settings.sign_secret)

# кэш на 30 секунд, чтобы не бомбить диск при частых кликах
_files_cache: dict[tuple[str,str], tuple[float, list[str]]] = {}
FILES_TTL = 30.0

# SSE для real-time обновлений дэшборда
_sse_clients: list[asyncio.Queue] = []

# Метрики для health check
_app_start_time = time.time()
_request_count = 0
_error_count = 0

async def get_db():
    async with SessionLocal() as s:
        yield s

@app.on_event("startup")
async def startup():
    global _app_start_time
    _app_start_time = time.time()
    
    # Инициализация базы данных
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Инициализация Redis (с fallback если недоступен)
    redis_ok = await init_redis()
    if redis_ok:
        logger.info("✅ Redis cache enabled")
    else:
        logger.warning("⚠️ Redis cache disabled, using database fallback")
    logger.info("Application startup complete")

@app.on_event("shutdown")
async def shutdown():
    """Закрытие Redis при остановке приложения"""
    from .cache import close_redis
    await close_redis()
    logger.info("Application shutdown complete")

@app.get("/")
async def root():
    return RedirectResponse(url="/web/login.html")

@app.get("/api/health")
async def health_check(db=Depends(get_db)):
    """
    Health check endpoint с метриками системы.
    Доступен без авторизации для мониторинга.
    """
    import psutil
    
    # Uptime
    uptime_seconds = time.time() - _app_start_time
    uptime_hours = uptime_seconds / 3600
    uptime_days = uptime_hours / 24
    
    # Проверка подключения к БД
    db_status = "unknown"
    db_error = None
    try:
        await db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        db_status = "error"
        db_error = str(e)
        logger.error(f"Database health check failed: {e}")
    
    # Метрики системы
    try:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        cpu_percent = psutil.cpu_percent(interval=0.1)
    except:
        memory = disk = None
        cpu_percent = None
    
    # Метрики приложения
    error_rate = (_error_count / _request_count * 100) if _request_count > 0 else 0
    
    health_data = {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "timestamp": datetime.now().isoformat(),
        "uptime": {
            "seconds": int(uptime_seconds),
            "hours": round(uptime_hours, 2),
            "days": round(uptime_days, 2),
            "human": f"{int(uptime_days)}д {int(uptime_hours % 24)}ч"
        },
        "database": {
            "status": db_status,
            "error": db_error
        },
        "requests": {
            "total": _request_count,
            "errors": _error_count,
            "error_rate": f"{error_rate:.2f}%"
        },
        "system": {
            "cpu_percent": cpu_percent,
            "memory_used_mb": int(memory.used / 1024 / 1024) if memory else None,
            "memory_total_mb": int(memory.total / 1024 / 1024) if memory else None,
            "memory_percent": memory.percent if memory else None,
            "disk_used_gb": round(disk.used / 1024 / 1024 / 1024, 2) if disk else None,
            "disk_total_gb": round(disk.total / 1024 / 1024 / 1024, 2) if disk else None,
            "disk_percent": disk.percent if disk else None
        },
        "environment": getattr(settings, 'environment', 'unknown'),
        "sentry_enabled": bool(settings.sentry_dsn)
    }
    
    logger.info(f"Health check: status={health_data['status']}, requests={_request_count}, errors={_error_count}, db={db_status}")
    
    return health_data

@app.get("/api/cache/stats")
async def cache_stats(user: UserModel = Depends(get_current_user)):
    """
    Статистика Redis кэша (только для ADMIN и GLOBAL).
    Показывает количество ключей, используемую память и другие метрики.
    """
    if user.role not in ("ADMIN", "GLOBAL"):
        raise HTTPException(403, "Access denied: ADMIN or GLOBAL role required")
    
    from .cache import get_cache_stats
    stats = await get_cache_stats()
    return stats

@app.get("/api/records")
async def get_records(user: UserModel = Depends(get_current_user), db=Depends(get_db)):
    q = select(Attendance, Sess).join(Sess, Attendance.session_id==Sess.id)

    role = user.role.upper()
    if role in ("EMP", "EMPLOYEE"):
        # Сотрудник видит только свои записи в своей компании
        tab_clean = user.login.split('@')[0] if '@' in user.login else user.login
        company_id = None
        org_code = user.login.split('@')[1] if '@' in user.login else None
        if org_code:
            code_row = (await db.execute(
                text("SELECT company_id FROM public.company_login_code WHERE code = :code LIMIT 1"),
                {"code": org_code}
            )).mappings().first()
            if code_row:
                company_id = code_row["company_id"]
        q = q.where(Attendance.idnum == tab_clean)
        if company_id:
            q = q.where(Attendance.company_id == company_id)
    elif role == "CHIEF":
        if user.scope_company_id:
            q = q.where(Attendance.company_id == user.scope_company_id)
        if user.scope_orgunit_id:
            q = q.where(Attendance.orgunit_id == user.scope_orgunit_id)
    elif role in ("ADMIN", "GLOBAL") and user.scope_company_id:
        q = q.where(Attendance.company_id == user.scope_company_id)
    # GLOBAL без scope_company_id видит все записи

    rows = (await db.execute(q)).all()
    out = []
    for att, sess in rows:
        incident = f"instruktagi/{sess.type}/{sess.month}/{sess.file}"
        sig_url = f"/protected/signature?path={signer.sign(att.worker_sig_path).decode()}" if att.worker_sig_path else None
        instr_url = f"/protected/signature?path={signer.sign(att.instr_sig_path).decode()}" if att.instr_sig_path else None
        out.append({
            "idnum": att.idnum, "fio": att.fio, "type": sess.type,
            "company_id": att.company_id, "orgunit_id": att.orgunit_id,
            "timestamp": int(att.signed_at.timestamp() * 1000) if att.signed_at else 0,
            "incident": incident, "signatureLink": sig_url, "instrSignatureLink": instr_url,
            "birthday": att.birthday,
            "profession": att.profession,
            "cex": att.cex,
            "instructorName": att.instructor_name,
        })
    return out


@app.get("/api/records/my")
async def get_my_records(
    type: str | None = None,
    month: str | None = None,
    user: UserModel = Depends(get_current_user),
    db=Depends(get_db),
):
    """Записи только текущего пользователя — для проверки пройденных инструктажей."""
    tab_clean = user.login.split('@')[0] if '@' in user.login else user.login
    role = user.role.upper()
    company_id: int | None = None
    if role in ("EMP", "EMPLOYEE"):
        org_code = user.login.split('@')[1] if '@' in user.login else None
        if org_code:
            code_row = (await db.execute(
                text("SELECT company_id FROM public.company_login_code WHERE code = :code LIMIT 1"),
                {"code": org_code}
            )).mappings().first()
            if code_row:
                company_id = code_row["company_id"]
    else:
        company_id = user.scope_company_id

    q = (
        select(Attendance, Sess)
        .join(Sess, Attendance.session_id == Sess.id)
        .where(Attendance.idnum == tab_clean)
    )
    if company_id:
        q = q.where(Attendance.company_id == company_id)
    if type:
        q = q.where(Sess.type == type)
    if month:
        q = q.where(Sess.month == month)

    rows = (await db.execute(q)).all()
    return [
        {
            "idnum": att.idnum,
            "type": sess.type,
            "month": sess.month,
            "file": sess.file,
            "incident": f"instruktagi/{sess.type}/{sess.month}/{sess.file}",
        }
        for att, sess in rows
    ]

@app.post("/api/records")
async def post_record(body: RecordIn, user: UserModel = Depends(get_current_user), db=Depends(get_db)):
    # Используем JWT Bearer авторизацию вместо query-параметра token
    parts = body.incident.split("/")
    if len(parts) < 4:
        raise HTTPException(400, "Bad incident path")
    file_name = parts[-1]; month = parts[-2]; type_slug = parts[-3]
    sess = (await db.execute(
        select(Sess).where(
            Sess.type == type_slug,
            Sess.month == month,
            Sess.file == file_name,
        )
    )).scalar_one_or_none()
    if not sess:
        sess = Sess(type=type_slug, month=month, file=file_name)
        db.add(sess); await db.flush()
    
    # Извлекаем табельный номер из body.idnum (формат может быть "3214" или "3214@uos2")
    tab_clean = body.idnum.split('@')[0] if '@' in body.idnum else body.idnum
    
    # Определяем company_id и orgunit_id в зависимости от роли
    logger.debug(f"post_record: role='{user.role}', login='{user.login}'")
    if user.role.upper() == "EMP" or user.role.upper() == "EMPLOYEE":
        # Для обычных сотрудников: извлекаем организацию из логина
        org_code = user.login.split('@')[1] if '@' in user.login else None
        if not org_code:
            raise HTTPException(400, "Логин должен содержать организацию (формат: табельный@организация)")
        
        # Получаем company_id из таблицы company_login_code
        code_row = (await db.execute(text("""
            SELECT company_id FROM public.company_login_code WHERE code = :code LIMIT 1
        """), {"code": org_code})).mappings().first()
        
        if not code_row:
            raise HTTPException(400, f"Организация '{org_code}' не найдена в справочнике")
        
        cid = code_row["company_id"]
        
        # Ищем сотрудника по табельному + company_id (без фильтра по orgunit_id)
        emp_row = (await db.execute(text("""
            SELECT "EmployeeID", "FamilyName", "GivenName", "Employer", "WorksIn"
            FROM public."Employee"
            WHERE "EmployeeNumber" = :tab AND "Employer" = :cid
            LIMIT 1
        """), {"tab": int(tab_clean), "cid": cid})).mappings().first()
        
    else:
        # Для руководителей/администраторов: используем scope из токена
        if user.scope_company_id is None or user.scope_orgunit_id is None:
            raise HTTPException(400, "Ваша роль не содержит company_id/orgunit_id. Обратитесь к администратору.")
        try:
            cid = int(user.scope_company_id)
            oid = int(user.scope_orgunit_id)
        except Exception:
            raise HTTPException(400, "company_id/orgunit_id должны быть числами")
        
        # Ищем сотрудника по табельному + company + orgunit
        emp_row = (await db.execute(text("""
            SELECT "EmployeeID", "FamilyName", "GivenName", "Employer", "WorksIn"
            FROM public."Employee"
            WHERE "EmployeeNumber" = :tab
              AND "Employer" = :cid
              AND "WorksIn" = :oid
            LIMIT 1
        """), {"tab": int(tab_clean), "cid": cid, "oid": oid})).mappings().first()
    
    if not emp_row:
        raise HTTPException(400, "Employee not found in your organization")

    company_id = emp_row["Employer"]
    orgunit_id = emp_row["WorksIn"]
    fio = f"{emp_row['FamilyName']} {emp_row['GivenName']}".strip()
    
    # Проверяем, не проходил ли уже этот сотрудник этот инструктаж (с учетом company_id)
    existing = (await db.execute(
        select(Attendance).where(
            Attendance.session_id == sess.id,
            Attendance.idnum == tab_clean,
            Attendance.company_id == company_id
        )
    )).scalar_one_or_none()
    
    if existing:
        # Теперь не обновляем повторно — запрещаем вторичное прохождение
        raise HTTPException(status_code=409, detail="already_completed")
    
    # Сохраняем подписи
    worker_sig_path = save_dataurl_png(body.signature, SIG_DIR, f"{tab_clean}_{company_id}") if body.signature else None
    instr_sig_path  = save_dataurl_png(body.instrSignature, SIG_DIR, f"instr_{tab_clean}_{company_id}") if body.instrSignature else None
    
    # Создаём новую запись с полными данными
    att = Attendance(
        session_id=sess.id,
        idnum=tab_clean,
        fio=fio,
        company_id=company_id,
        orgunit_id=orgunit_id,
        worker_sig_path=worker_sig_path,
        instr_sig_path=instr_sig_path,
        birthday=body.birthday,
        profession=body.profession,
        cex=body.cex,
        instructor_name=body.instructorName,
    )
    db.add(att); await db.commit()
    result = {"ok": True, "session_id": sess.id, "completed": True}
    
    # Отправляем событие всем подключённым клиентам дэшборда
    event_data = {
        "type": "record_added",
        "idnum": tab_clean,
        "fio": fio,
        "company_id": company_id,
        "session_type": type_slug,
        "month": month,
        "file": file_name
    }
    for client_queue in _sse_clients:
        try:
            await client_queue.put(event_data)
        except:
            pass
    
    return result

@app.get("/api/mobile/my-sessions")
async def my_sessions(idnum: str, month: str | None = None, type: str | None = None, db=Depends(get_db)):
    from sqlalchemy import select
    q = select(Sess)
    if month: q = q.where(Sess.month==month)
    if type:  q = q.where(Sess.type==type)
    sessions = (await db.execute(q)).scalars().all()
    done = set((await db.execute(select(Attendance.session_id).where(Attendance.idnum==idnum))).scalars().all())
    return [
        {"session_id": s.id, "type": s.type, "month": s.month, "file": s.file,
         "title": f"{s.type} · {s.month} · {s.file}", "status": ("completed" if s.id in done else "not_started")}
        for s in sessions
    ]

@app.get("/api/mobile/session-url")
async def session_url(session_id: int, db=Depends(get_db)):
    from sqlalchemy import select
    s = (await db.execute(select(Sess).where(Sess.id==session_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Not found")
    real = os.path.join(DOC_DIR, s.type, s.month, s.file)
    token = signer.sign(real).decode()
    return {"url": f"/protected/doc?path={token}"}

@app.get("/protected/doc")
async def protected_doc(path: str):
    try:
        real = signer.unsign(path, max_age=60*60*3).decode()
    except BadSignature:
        raise HTTPException(403, "Invalid/expired link")
    if not os.path.isfile(real):
        raise HTTPException(404, "File missing")
    return FileResponse(real, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'inline; filename="{os.path.basename(real)}"'})

@app.get("/protected/signature")
async def protected_sig(path: str):
    try:
        real = signer.unsign(path, max_age=60*60*24).decode()
    except BadSignature:
        raise HTTPException(403, "Invalid/expired link")
    if not os.path.isfile(real):
        raise HTTPException(404, "File missing")
    return FileResponse(real, media_type="image/png",
                        headers={"Content-Disposition": f'inline; filename="{os.path.basename(real)}"'})

@app.get("/api/dashboard/me", response_model=MeOut)
async def who_am_i(user: UserModel = Depends(get_current_user), db=Depends(get_db)):
    company_id = user.scope_company_id
    orgunit_id = user.scope_orgunit_id
    
    # Для роли EMP получаем company_id из логина через company_login_code
    if user.role.upper() == "EMP" and '@' in user.login:
        org_code = user.login.split('@')[1]
        code_row = (await db.execute(text("""
            SELECT company_id FROM public.company_login_code WHERE code = :code LIMIT 1
        """), {"code": org_code})).mappings().first()
        if code_row:
            company_id = code_row["company_id"]
    
    # Пытаемся получить ФИО сотрудника по табельному номеру
    fio = None
    if '@' in user.login:
        tab_num = user.login.split('@')[0]
        try:
            tab_int = int(tab_num)
            emp_row = (await db.execute(text("""
                SELECT TRIM(COALESCE("FamilyName",'') || ' ' || COALESCE("GivenName",'') || ' ' || COALESCE("PatronymicName",'')) AS fio
                FROM "Employee"
                WHERE "EmployeeNumber" = :idnum AND "Employer" = :cid
                LIMIT 1
            """), {"idnum": tab_int, "cid": company_id})).mappings().first()
            if emp_row and emp_row["fio"] and emp_row["fio"].strip():
                fio = emp_row["fio"].strip()
        except (ValueError, Exception):
            pass

    return MeOut(
        login=user.login,
        role=user.role,
        scope_company_id=company_id,
        scope_orgunit_id=orgunit_id,
        fio=fio
    )

@app.get("/api/dashboard/records", response_model=list[DashRecord])
async def dashboard_records(
    month: str | None = None,
    type: str | None = None,
    page: int = Query(1, ge=1, description="Страница (с 1)"),
    limit: int = Query(200, ge=1, le=1000, description="Записей на странице, макс. 1000"),
    user: UserModel = Depends(get_current_user),
    db=Depends(get_db),
):
    from .cache import cache_get, cache_set

    # Генерируем ключ кэша на основе параметров и роли пользователя
    cache_key = f"records:{user.role}:{user.scope_company_id}:{user.scope_orgunit_id}:{month}:{type}:{page}:{limit}"
    
    # Пытаемся получить из кэша
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached
    
    # Базовый запрос из представления (расширенные присутствия)
    sql = """
      SELECT
        id, session_id, idnum, fio, signed_at,
        type, month, file,
        company_id, company_name,
        orgunit_id, orgunit_name,
        worker_sig_path as "signatureLink",
        instr_sig_path as "instrSignatureLink",
        instructor_name as "instructorName",
        birth_year, birth_date::text as birth_date
      FROM instr.v_attendance_expanded
      WHERE 1=1
    """
    params: dict = {}

    # Фильтры от UI (по желанию)
    if month:
        sql += " AND month = :month"
        params["month"] = month
    if type:
        sql += " AND type = :type"
        params["type"] = type

    # <<< ВОТ ОНА, ТА САМАЯ ЛОГИКА ФИЛЬТРАЦИИ ПО РОЛЯМ >>>
    if user.role == "CHIEF":
        # Начальник видит ТОЛЬКО свой цех внутри своей компании
        sql += " AND company_id = :c AND orgunit_id = :o"
        params["c"] = user.scope_company_id
        params["o"] = user.scope_orgunit_id
    elif user.role == "GLOBAL" and user.scope_company_id is not None:
        sql += " AND company_id = :gc"
        params["gc"] = user.scope_company_id
    # GLOBAL и ADMIN видят всё — никаких доп. условий

    sql += " ORDER BY id DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = (page - 1) * limit

    res = await db.execute(text(sql), params)
    rows = [dict(r._mapping) for r in res]
    
    # Сохраняем в кэш на 1 минуту (60 секунд) - данные меняются чаще
    await cache_set(cache_key, rows, ttl=60)
    
    # pydantic сам приведёт к DashRecord
    return rows

@app.get("/api/dashboard/summary", response_model=list[SummaryItem])
async def dashboard_summary(
    month: str | None = None,
    type: str | None = None,
    user: UserModel = Depends(get_current_user),
    db=Depends(get_db),
):
    sql = """
      SELECT
        company_id, company_name,
        orgunit_id, orgunit_name,
        COUNT(*)::int AS cnt
      FROM instr.v_attendance_expanded
      WHERE 1=1
    """
    params: dict = {}
    if month:
        sql += " AND month = :month"
        params["month"] = month
    if type:
        sql += " AND type = :type"
        params["type"] = type

    if user.role == "CHIEF":
        sql += " AND company_id = :c AND orgunit_id = :o"
        params["c"] = user.scope_company_id
        params["o"] = user.scope_orgunit_id

    sql += """
      GROUP BY company_id, company_name, orgunit_id, orgunit_name
      ORDER BY company_name NULLS LAST, orgunit_name NULLS LAST
    """

    res = await db.execute(text(sql), params)
    rows = [dict(r._mapping) for r in res]
    return rows

@app.get("/api/dashboard/files")
@limiter.limit("100/minute")  # Максимум 100 запросов в минуту
async def dashboard_files_fs(
    request: Request,
    month: str = Query("", description="YYYY-MM; для vvodny/pervichny можно пусто"),
    type: str  = Query(..., description="vvodny|pervichny|povtorny|vneplanovy|celevoy"),
    db=Depends(get_db),
    user: UserModel = Depends(get_current_user),
):
    from app.models import InstructionalFile
    from sqlalchemy import select, text
    
    allowed = {"vvodny", "pervichny", "povtorny", "vneplanovy", "celevoy"}
    if type not in allowed:
        raise HTTPException(status_code=400, detail="unknown type")

    # Определяем company_id пользователя
    user_company_id = None
    
    if user.role in ["GLOBAL", "ADMIN", "CHIEF"]:
        # Для администраторов берём scope_company_id
        user_company_id = user.scope_company_id
    elif user.role.upper() in ("EMP", "EMPLOYEE"):
        # Для обычных сотрудников (EMP) извлекаем company_id из логина через company_login_code
        # Логин формата "7089@uat" или "27@uos2"
        if '@' in user.login:
            org_code = user.login.split('@')[1]
            code_query = text("SELECT company_id FROM public.company_login_code WHERE code = :code LIMIT 1")
            code_result = await db.execute(code_query, {"code": org_code})
            code_row = code_result.mappings().first()
            if code_row:
                user_company_id = code_row["company_id"]
    
    if not user_company_id:
        # Если не удалось определить company_id - возвращаем пустой список
        return []
    
    # Кэш с учётом company_id
    key = (type, month or "", user_company_id)
    now = time.time()
    cached = _files_cache.get(key)
    if cached and (now - cached[0] < FILES_TTL):
        return cached[1]

    # Получаем файлы из БД с фильтрацией по company_id
    query = select(InstructionalFile).where(
        InstructionalFile.file_type == type,
        InstructionalFile.company_id == user_company_id
    )
    
    if month:
        # Фильтрация по месяцу через LIKE на file_path
        # Путь выглядит как: instruktagi/vneplanovy/2025-11/filename.pdf
        query = query.where(InstructionalFile.file_path.like(f"%/{month}/%"))
    
    result = await db.execute(query)
    files = result.scalars().all()
    
    # Формируем ответ в том же формате
    items = []
    base_path = Path(__file__).parent.parent  # /home/instr/app
    for f in sorted(files, key=lambda x: x.file_name.lower()):
        # Проверяем существует ли файл физически
        # f.file_path имеет вид: storage/instruktagi/vneplanovy/2025-11/file.pdf
        full_path = base_path / f.file_path
        if full_path.exists():
            st = full_path.stat()
            items.append({"name": f.file_name, "mtime": int(st.st_mtime), "path": f.file_path})
    
    _files_cache[key] = (now, items)
    return items

@app.get("/api/dashboard/employees")
@limiter.limit("60/minute")  # Максимум 60 запросов в минуту
async def employees(
    request: Request,
    company_id: Optional[int] = None,
    orgunit_id: Optional[int] = None,
    q: Optional[str] = None,
    db=Depends(get_db),
    user: UserModel = Depends(get_current_user),
):
    from .cache import cache_get, cache_set
    
    # Генерируем ключ кэша на основе параметров и роли пользователя
    cache_key = f"employees:{user.role}:{user.scope_company_id}:{user.scope_orgunit_id}:{company_id}:{orgunit_id}:{q}"
    
    # Пытаемся получить из кэша
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached
    
    sql = """
      SELECT
        e."EmployeeNumber"::text AS idnum,
        TRIM(COALESCE(e."FamilyName",'') || ' ' || COALESCE(e."GivenName",'') || ' ' || COALESCE(e."PatronymicName",'')) AS fio,
        e."Employer"             AS company_id,
        c."CompanyName"          AS company_name,
        e."WorksIn"              AS orgunit_id,
        ou."OrgUnitName"         AS orgunit_name,
        p."PositionName"         AS profession,
        e.birth_year,
        e.birth_date::text       AS birth_date
      FROM "Employee" e
      LEFT JOIN "Company"  c  ON c."CompanyID"  = e."Employer"
      LEFT JOIN "OrgUnit"  ou ON ou."OrgUnitID" = e."WorksIn"
      LEFT JOIN "Position" p  ON p."PositionID" = e."Holds"
      WHERE 1=1
    """
    params = {}

    # ограничения ролями
    if user.role == "CHIEF":
        sql += " AND e.\"Employer\" = :c AND e.\"WorksIn\" = :o"
        params.update(c=user.scope_company_id, o=user.scope_orgunit_id)
    elif user.role == "GLOBAL" and user.scope_company_id is not None:
        # если глобалу задана компания — ограничиваем
        if user.scope_company_id is not None:
            sql += " AND e.\"Employer\" = :gc"
            params["gc"] = user.scope_company_id
        # иначе глобал без привязки видит всё
    elif user.role.upper() in ("EMP", "EMPLOYEE"):
        # EMP видит только сотрудников своей компании
        if '@' in user.login:
            org_code = user.login.split('@')[1]
            code_row = (await db.execute(text("SELECT company_id FROM public.company_login_code WHERE code = :code LIMIT 1"), {"code": org_code})).mappings().first()
            if code_row:
                sql += " AND e.\"Employer\" = :ec"
                params["ec"] = code_row["company_id"]

    # внешние фильтры
    if company_id is not None:
        sql += " AND e.\"Employer\" = :fc"
        params["fc"] = company_id
    if orgunit_id is not None:
        sql += " AND e.\"WorksIn\" = :fo"
        params["fo"] = orgunit_id
    if q:
        sql += " AND (e.\"FamilyName\" ILIKE :qq OR e.\"GivenName\" ILIKE :qq OR e.\"PatronymicName\" ILIKE :qq)"
        params["qq"] = f"%{q}%"

    sql += " ORDER BY fio"

    rows = (await db.execute(text(sql), params)).mappings().all()
    result = [dict(r) for r in rows]
    
    # Сохраняем в кэш на 5 минут (300 секунд)
    await cache_set(cache_key, result, ttl=300)
    
    return result

@app.get("/api/dashboard/events")
async def dashboard_events(token: str = Query(...), db=Depends(get_db)):
    """SSE endpoint для real-time обновлений дэшборда (авторизация через query параметр)"""
    # Проверяем токен вручную, т.к. EventSource не может передавать заголовки
    from jose import jwt, JWTError
    # Используем тот же ключ что и в auth.py
    SECRET_KEY = settings.secret_key if hasattr(settings, 'secret_key') else settings.sign_secret
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        login = payload.get("sub")
        if not login:
            raise HTTPException(401, "Invalid token")
    except JWTError:
        raise HTTPException(401, "Invalid token")
    
    async def event_generator():
        queue = asyncio.Queue()
        _sse_clients.append(queue)
        try:
            # Отправляем начальное сообщение о подключении
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            
            while True:
                # Ждём события из очереди
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            _sse_clients.remove(queue)
            raise
        except Exception as e:
            _sse_clients.remove(queue)
            raise
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

# Endpoint для загрузки подписи инструктора
class InstructorSignatureUpload(BaseModel):
    signature: str  # data:image/png;base64,...

@app.post("/api/upload-instructor-signature")
async def upload_instructor_signature(
    body: InstructorSignatureUpload,
    user: UserModel = Depends(get_current_user)
):
    """
    Загрузка подписи инструктора.
    Возвращает URL для использования в Excel.
    """
    # Проверяем роль: только CHIEF может загружать подпись инструктора
    if user.role != "CHIEF":
        raise HTTPException(status_code=403, detail="Only CHIEF can upload instructor signature")
    
    if not body.signature:
        raise HTTPException(400, "Signature is required")
    
    # Создаём директорию для подписей инструкторов
    instructor_sig_dir = os.path.join(DATA_ROOT, "instructor_signatures")
    os.makedirs(instructor_sig_dir, exist_ok=True)
    
    # Сохраняем подпись с уникальным именем: chief_{user_id}_{timestamp}.png
    prefix = f"chief_{user.id}_{int(time.time())}"
    sig_path = save_dataurl_png(body.signature, instructor_sig_dir, prefix)
    
    # Возвращаем относительный путь для веб-доступа
    filename = os.path.basename(sig_path)
    return {"url": f"instructor_signatures/{filename}"}