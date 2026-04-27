from pathlib import Path
from datetime import datetime
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession
from .auth import get_current_user
from .models_auth import User
from .db import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/upload", tags=["upload"])
limiter = Limiter(key_func=get_remote_address)

# Базовая папка для хранения инструктажей
STORAGE_BASE = Path(__file__).parent.parent / "storage" / "instruktagi"

# Разрешенные типы инструктажей
ALLOWED_TYPES = ["povtorny", "pervichny", "vvodny", "celevoy", "vneplanovy"]

# Разрешенные расширения файлов
ALLOWED_EXTENSIONS = {
    # Документы
    ".pdf", ".doc", ".docx",
    # Видео
    ".mp4", ".avi", ".mov", ".webm", ".mkv", ".wmv", ".flv", ".m4v",
    # Изображения
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"
}

# Максимальный размер файла: 100 МБ (для видео инструктажей)
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB в байтах


@router.post("/instruction")
@limiter.limit("10/minute")  # Максимум 10 загрузок в минуту (защита от спама)
async def upload_instruction(
    request: Request,
    file: UploadFile = File(...),
    instruction_type: str = Form(...),
    current_user: User = Depends(get_current_user)
):
    """
    Загрузка файла инструктажа.
    Доступно только для ADMIN и GLOBAL.
    """
    
    # Проверка прав доступа (только для руководителей и администраторов)
    if current_user.role not in ["ADMIN", "GLOBAL"]:
        raise HTTPException(403, "Недостаточно прав для загрузки файлов. Доступно только для администраторов.")
    
    # Проверка типа инструктажа
    if instruction_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"Неверный тип инструктажа. Допустимые: {', '.join(ALLOWED_TYPES)}")
    
    # Проверка расширения файла
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Неверный формат файла. Допустимые: {', '.join(ALLOWED_EXTENSIONS)}")
    
    # Читаем файл и проверяем размер
    contents = await file.read()
    file_size = len(contents)
    
    # Проверка размера файла
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            400, 
            f"Файл слишком большой ({file_size / (1024*1024):.1f} МБ). Максимальный размер: {MAX_FILE_SIZE / (1024*1024):.0f} МБ"
        )
    
    if file_size == 0:
        raise HTTPException(400, "Файл пустой")
    
    # Создаем путь: storage/instruktagi/{тип}/{год-месяц}/
    current_date = datetime.now()
    year_month = current_date.strftime("%Y-%m")
    target_dir = STORAGE_BASE / instruction_type / year_month
    
    # Создаем директорию если не существует
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Ensured directory exists: {target_dir}")
    except PermissionError as e:
        logger.error(
            f"Permission denied creating directory: {target_dir}, "
            f"user={current_user.login}, error={str(e)}",
            exc_info=True
        )
        raise HTTPException(
            500, 
            f"Ошибка доступа к директории хранения. Обратитесь к администратору."
        )
    except Exception as e:
        logger.error(
            f"Failed to create directory: {target_dir}, error={str(e)}",
            exc_info=True
        )
        raise HTTPException(500, f"Ошибка создания директории: {str(e)}")
    
    # Безопасное имя файла (убираем опасные символы)
    safe_filename = "".join(c for c in file.filename if c.isalnum() or c in "._- ")
    target_path = target_dir / safe_filename
    
    # Если файл существует - добавляем timestamp
    if target_path.exists():
        timestamp = current_date.strftime("%H%M%S")
        name_without_ext = target_path.stem
        safe_filename = f"{name_without_ext}_{timestamp}{file_ext}"
        target_path = target_dir / safe_filename
    
    # Сохраняем файл (contents уже прочитан выше для проверки размера)
    try:
        with open(target_path, "wb") as f:
            f.write(contents)
        
        logger.info(
            f"File uploaded: user={current_user.login}, "
            f"filename={safe_filename}, type={instruction_type}, "
            f"size={file_size/1024:.1f}KB, company_id={current_user.scope_company_id}, "
            f"path={target_path}"
        )
        
        # Сохраняем запись в БД (в фоновом режиме)
        import asyncio
        from .db import SessionLocal
        from sqlalchemy import text
        
        async def save_to_db():
            try:
                async with SessionLocal() as db:
                    # STORAGE_BASE.parent.parent = /home/instr/app
                    app_base = STORAGE_BASE.parent.parent
                    relative_path = str(target_path.relative_to(app_base))  # storage/instruktagi/...
                    
                    # Проверяем существует ли уже запись
                    result = await db.execute(
                        text("SELECT id FROM instructional_file WHERE file_path = :path"),
                        {"path": relative_path}
                    )
                    existing = result.fetchone()
                    
                    if not existing:
                        await db.execute(
                            text("""
                                INSERT INTO instructional_file (file_path, file_name, file_type, company_id, uploaded_by, uploaded_at)
                                VALUES (:path, :name, :type, :company, :uploader, NOW())
                            """),
                            {
                                "path": relative_path,
                                "name": safe_filename,
                                "type": instruction_type,
                                "company": current_user.scope_company_id,
                                "uploader": current_user.login
                            }
                        )
                        await db.commit()
                        logger.info(f"File metadata saved to DB: path={relative_path}")
                    else:
                        logger.info(f"File already exists in DB: path={relative_path}")
            except Exception as e:
                logger.error(f"Failed to save file metadata to DB: {e}", exc_info=True)
        
        # Запускаем сохранение в БД
        try:
            asyncio.create_task(save_to_db())
        except Exception as e:
            logger.error(f"Failed to create save_to_db task: {e}", exc_info=True)
        
        # Отправить push-уведомление всем сотрудникам компании (в фоне)
        if current_user.scope_company_id:
            try:
                from .fcm import send_notification_to_company
                from .db import SessionLocal
                
                async def send_notification_background():
                    try:
                        async with SessionLocal() as db_session:
                            result = await send_notification_to_company(
                                company_id=current_user.scope_company_id,
                                title="Новый инструктаж доступен",
                                body=f"Загружен новый файл: {safe_filename}",
                                db=db_session,
                                data={
                                    "type": "new_instruction",
                                    "instruction_type": instruction_type,
                                    "filename": safe_filename
                                }
                            )
                            logger.info(f"Push notification sent: {result}")
                    except Exception as e:
                        logger.warning(f"Failed to send push notification: {e}", exc_info=True)
                
                # Запустить в фоновом режиме
                asyncio.create_task(send_notification_background())
            except Exception as e:
                logger.warning(f"Failed to schedule push notification: {e}", exc_info=True)
        
        return JSONResponse({
            "success": True,
            "message": "Файл успешно загружен",
            "filename": safe_filename,
            "path": str(target_path.relative_to(STORAGE_BASE)),
            "size": len(contents)
        })
    
    except PermissionError as e:
        logger.error(
            f"Permission denied saving file: user={current_user.login}, "
            f"filename={file.filename}, type={instruction_type}, "
            f"target_path={target_path}, error={str(e)}",
            exc_info=True
        )
        raise HTTPException(
            500, 
            f"Ошибка доступа к файлу. Проверьте права на директорию {target_dir}"
        )
    except Exception as e:
        logger.error(
            f"File upload failed: user={current_user.login}, "
            f"filename={file.filename}, type={instruction_type}, "
            f"error={str(e)}",
            exc_info=True
        )
        raise HTTPException(500, f"Ошибка при сохранении файла: {str(e)}")


@router.get("/list")
async def list_instructions(
    instruction_type: str = "povtorny",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Получить список загруженных инструктажей.
    Доступно для всех авторизованных пользователей.
    Фильтруется по company_id пользователя.
    """
    from app.models import InstructionalFile
    from sqlalchemy import select
    
    if instruction_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"Неверный тип инструктажа. Допустимые: {', '.join(ALLOWED_TYPES)}")
    
    # Определяем company_id пользователя
    user_company_id = current_user.scope_company_id
    
    if not user_company_id:
        return {"files": [], "type": instruction_type, "total": 0}
    
    # Получаем файлы из БД с фильтрацией по company_id
    query = select(InstructionalFile).where(
        InstructionalFile.file_type == instruction_type,
        InstructionalFile.company_id == user_company_id
    ).order_by(InstructionalFile.uploaded_at.desc())
    
    result = await db.execute(query)
    db_files = result.scalars().all()
    
    files = []
    for f in db_files:
        # Извлекаем месяц из file_path (формат: storage/instruktagi/type/YYYY-MM/file.pdf)
        parts = Path(f.file_path).parts
        month = parts[3] if len(parts) > 3 else "unknown"
        
        # Проверяем существование файла
        full_path = Path(__file__).parent.parent / f.file_path
        if full_path.exists():
            files.append({
                "filename": f.file_name,
                "month": month,
                "size": full_path.stat().st_size,
                "modified": f.uploaded_at.isoformat() if f.uploaded_at else datetime.now().isoformat()
            })
    
    return {"files": files, "type": instruction_type, "total": len(files)}


@router.delete("/instruction")
async def delete_instruction(
    instruction_type: str = Form(...),
    month: str = Form(...),
    filename: str = Form(...),
    current_user: User = Depends(get_current_user)
):
    """
    Удаление файла инструктажа.
    Доступно только для ADMIN и GLOBAL.
    """
    
    # Проверка прав доступа (только администраторы)
    if current_user.role not in ["ADMIN", "GLOBAL"]:
        raise HTTPException(403, "Недостаточно прав для удаления файлов. Доступно только для администраторов.")
    
    # Проверка типа инструктажа
    if instruction_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"Неверный тип инструктажа. Допустимые: {', '.join(ALLOWED_TYPES)}")
    
    # Проверка формата месяца (YYYY-MM)
    if not month or len(month.split("-")) != 2:
        raise HTTPException(400, "Неверный формат месяца. Ожидается YYYY-MM")
    
    # Безопасная проверка имени файла (без опасных символов)
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(400, "Недопустимое имя файла")
    
    # Формируем путь к файлу
    file_path = STORAGE_BASE / instruction_type / month / filename
    
    # Проверяем что файл существует
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "Файл не найден")
    
    # Удаляем файл
    try:
        file_path.unlink()
        return JSONResponse({
            "success": True,
            "message": f"Файл {filename} успешно удален"
        })
    except Exception as e:
        raise HTTPException(500, f"Ошибка при удалении файла: {str(e)}")
