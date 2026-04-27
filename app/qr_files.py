import uuid
import io
import base64
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import text

from .auth import get_current_user
from .models_auth import User
from .db import SessionLocal
from .settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["qr"])

settings = get_settings()

# Папка для хранения QR-файлов
QR_STORAGE = Path(__file__).parent.parent / "storage" / "qr_uploads"
QR_STORAGE.mkdir(parents=True, exist_ok=True)

# Разрешённые расширения
ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx",
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".mp4", ".avi", ".mov", ".webm",
    ".xlsx", ".xls", ".pptx", ".ppt", ".txt",
}

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

# Роли, которым разрешён доступ
QR_ALLOWED_ROLES = {"QR_UPLOADER", "ADMIN", "GLOBAL"}


def get_base_url() -> str:
    """Базовый URL сайта для формирования ссылок на файлы."""
    return getattr(settings, "base_url", "https://hse.omg.kmg.kz")


async def _get_qr_file_row(file_id: str):
    async with SessionLocal() as db:
        result = await db.execute(
            text(
                "SELECT stored_path, original_name, mime_type, file_size "
                "FROM instr.qr_file WHERE id = :id"
            ),
            {"id": file_id},
        )
        return result.fetchone()


def _validate_qr_file_id(file_id: str) -> None:
    clean = file_id.replace("-", "").lower()
    if not clean.isalnum() or len(clean) != 32:
        raise HTTPException(400, "Неверный ID файла")


# ─── Страница загрузки ──────────────────────────────────────────────────────

@router.get("/qr-upload")
async def qr_upload_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/web/qr-upload.html")


# ─── Загрузка файла ─────────────────────────────────────────────────────────

@router.post("/api/qr/upload")
async def upload_file_for_qr(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    Загрузить файл и получить QR-код со ссылкой на него.
    Доступно для ролей: QR_UPLOADER, ADMIN, GLOBAL.
    """
    import qrcode  # late import — not always installed

    if current_user.role not in QR_ALLOWED_ROLES:
        raise HTTPException(403, "Нет доступа. Требуется роль QR_UPLOADER.")

    # Проверка расширения
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Неверный формат файла. Допустимые: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Читаем содержимое
    contents = await file.read()
    if len(contents) == 0:
        raise HTTPException(400, "Файл пустой")
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            400,
            f"Файл слишком большой ({len(contents) / 1024 / 1024:.1f} МБ). Максимум 100 МБ.",
        )

    # Сохраняем с UUID именем
    file_id = str(uuid.uuid4())
    stored_name = f"{file_id}{ext}"
    stored_path = QR_STORAGE / stored_name
    stored_path.write_bytes(contents)

    # Сохраняем метаданные в БД
    try:
        async with SessionLocal() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO instr.qr_file
                        (id, original_name, stored_path, uploaded_by, file_size, mime_type)
                    VALUES
                        (:id, :name, :path, :uploader, :size, :mime)
                    """
                ),
                {
                    "id": file_id,
                    "name": file.filename,
                    "path": stored_name,
                    "uploader": current_user.login,
                    "size": len(contents),
                    "mime": file.content_type or "application/octet-stream",
                },
            )
            await db.commit()
    except Exception as e:
        # Если БД не доступна — удаляем сохранённый файл
        stored_path.unlink(missing_ok=True)
        logger.error(f"QR upload: DB error: {e}", exc_info=True)
        raise HTTPException(500, "Ошибка сохранения в базе данных")

    # Генерируем QR-код
    base_url = get_base_url()
    file_url = f"{base_url}/qr/file/{file_id}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(file_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    logger.info(
        f"QR upload: user={current_user.login}, file={file.filename}, "
        f"id={file_id}, size={len(contents)} bytes"
    )

    return JSONResponse(
        {
            "success": True,
            "file_id": file_id,
            "original_name": file.filename,
            "file_url": file_url,
            "qr_base64": qr_b64,
        }
    )


# ─── Публичная отдача файла ──────────────────────────────────────────────────

@router.get("/qr/file/{file_id}")
async def get_qr_file(file_id: str, download: str = "0", raw: str = "0"):
    """
    Публичный доступ к файлу по UUID. Авторизация не нужна.
    download=1 — принудительное скачивание (Content-Disposition: attachment)
    raw=1 — отдать оригинальный файл без страницы предпросмотра
    """
    _validate_qr_file_id(file_id)

    row = await _get_qr_file_row(file_id)

    if not row:
        raise HTTPException(404, "Файл не найден")

    file_path = QR_STORAGE / row.stored_path
    if not file_path.exists():
        raise HTTPException(404, "Файл не найден на диске")

    if download != "1" and raw != "1":
        return RedirectResponse(url=f"/web/qr-file.html?id={file_id}")

    # Inline для просматриваемых форматов; attachment только при ?download=1
    force_download = download == "1"
    disposition = "attachment" if force_download else "inline"

    return FileResponse(
        path=str(file_path),
        filename=row.original_name,
        media_type=row.mime_type or "application/octet-stream",
        content_disposition_type=disposition,
    )


@router.get("/api/qr/public-file/{file_id}")
async def get_public_qr_file_info(file_id: str):
    """Публичная мета-информация по QR-файлу для страницы предпросмотра."""
    _validate_qr_file_id(file_id)

    row = await _get_qr_file_row(file_id)
    if not row:
        raise HTTPException(404, "Файл не найден")

    file_path = QR_STORAGE / row.stored_path
    if not file_path.exists():
        raise HTTPException(404, "Файл не найден на диске")

    base_url = get_base_url().rstrip("/")
    return {
        "file_id": file_id,
        "original_name": row.original_name,
        "mime_type": row.mime_type or "application/octet-stream",
        "file_size": row.file_size,
        "file_url": f"{base_url}/qr/file/{file_id}",
        "raw_url": f"{base_url}/qr/file/{file_id}?raw=1",
        "download_url": f"{base_url}/qr/file/{file_id}?download=1",
    }


# ─── Список файлов пользователя ─────────────────────────────────────────────

@router.get("/api/qr/files")
async def list_qr_files(current_user: User = Depends(get_current_user)):
    """Список файлов, загруженных текущим пользователем."""
    if current_user.role not in QR_ALLOWED_ROLES:
        raise HTTPException(403, "Нет доступа")

    async with SessionLocal() as db:
        result = await db.execute(
            text(
                """
                SELECT id, original_name, file_size, uploaded_at
                FROM instr.qr_file
                WHERE uploaded_by = :uploader
                  AND bundle_id IS NULL
                ORDER BY uploaded_at DESC
                LIMIT 100
                """
            ),
            {"uploader": current_user.login},
        )
        rows = result.fetchall()

    base_url = get_base_url()
    files = [
        {
            "file_id": str(r.id),
            "original_name": r.original_name,
            "file_size": r.file_size,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
            "file_url": f"{base_url}/qr/file/{r.id}",
        }
        for r in rows
    ]

    return {"files": files, "total": len(files)}


# ─── Удаление файла ──────────────────────────────────────────────────────────

@router.get("/api/qr/qr-image/{file_id}")
async def get_qr_image(file_id: str, current_user: User = Depends(get_current_user)):
    """Вернуть QR-изображение для существующего файла."""
    import qrcode  # late import

    if current_user.role not in QR_ALLOWED_ROLES:
        raise HTTPException(403, "Нет доступа")

    clean = file_id.replace("-", "").lower()
    if not clean.isalnum() or len(clean) != 32:
        raise HTTPException(400, "Неверный ID файла")

    async with SessionLocal() as db:
        result = await db.execute(
            text("SELECT id FROM instr.qr_file WHERE id = :id"),
            {"id": file_id},
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(404, "Файл не найден")

    base_url = get_base_url()
    file_url = f"{base_url}/qr/file/{file_id}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(file_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    return JSONResponse({"qr_base64": qr_b64, "file_url": file_url})


@router.delete("/api/qr/file/{file_id}")
async def delete_qr_file(
    file_id: str, current_user: User = Depends(get_current_user)
):
    """Удаление файла. Можно удалять только свои файлы (ADMIN/GLOBAL — любые)."""
    if current_user.role not in QR_ALLOWED_ROLES:
        raise HTTPException(403, "Нет доступа")

    async with SessionLocal() as db:
        result = await db.execute(
            text(
                "SELECT stored_path, uploaded_by FROM instr.qr_file WHERE id = :id"
            ),
            {"id": file_id},
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(404, "Файл не найден")

    if row.uploaded_by != current_user.login and current_user.role not in {
        "ADMIN",
        "GLOBAL",
    }:
        raise HTTPException(403, "Нельзя удалять чужие файлы")

    # Удаляем с диска
    file_path = QR_STORAGE / row.stored_path
    file_path.unlink(missing_ok=True)

    # Удаляем из БД
    async with SessionLocal() as db:
        await db.execute(
            text("DELETE FROM instr.qr_file WHERE id = :id"), {"id": file_id}
        )
        await db.commit()

    logger.info(f"QR delete: user={current_user.login}, file_id={file_id}")
    return {"success": True, "message": "Файл удалён"}


# ═══════════════════════════════════════════════════════════════════════════
# ПАКЕТЫ (BUNDLES) — несколько файлов на один QR
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/qr/bundle")
async def create_bundle(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Создать новый пакет документов. Возвращает bundle_id и QR-код."""
    import qrcode

    if current_user.role not in QR_ALLOWED_ROLES:
        raise HTTPException(403, "Нет доступа")

    body = await request.json()
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip()
    if not name:
        raise HTTPException(400, "Укажите название пакета")

    bundle_id = str(uuid.uuid4())
    async with SessionLocal() as db:
        await db.execute(
            text("""INSERT INTO instr.qr_bundle (id, name, description, uploaded_by)
                    VALUES (:id, :name, :desc, :uploader)"""),
            {"id": bundle_id, "name": name, "desc": description, "uploader": current_user.login},
        )
        await db.commit()

    base_url = get_base_url()
    bundle_url = f"{base_url}/qr/bundle/{bundle_id}"

    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(bundle_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    logger.info(f"QR bundle created: user={current_user.login}, bundle_id={bundle_id}, name={name}")
    return {"success": True, "bundle_id": bundle_id, "bundle_url": bundle_url, "qr_base64": qr_b64}


@router.post("/api/qr/bundle/{bundle_id}/upload")
async def upload_file_to_bundle(
    bundle_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """Загрузить файл в существующий пакет."""
    if current_user.role not in QR_ALLOWED_ROLES:
        raise HTTPException(403, "Нет доступа")

    # Проверяем пакет
    async with SessionLocal() as db:
        row = (await db.execute(
            text("SELECT id, uploaded_by FROM instr.qr_bundle WHERE id = :id"),
            {"id": bundle_id},
        )).fetchone()
    if not row:
        raise HTTPException(404, "Пакет не найден")
    if row.uploaded_by != current_user.login and current_user.role not in {"ADMIN", "GLOBAL"}:
        raise HTTPException(403, "Нельзя добавлять файлы в чужой пакет")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Неверный формат файла")

    contents = await file.read()
    if not contents:
        raise HTTPException(400, "Файл пустой")
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(400, f"Файл слишком большой (макс. 100 МБ)")

    file_id = str(uuid.uuid4())
    stored_name = f"{file_id}{ext}"
    stored_path = QR_STORAGE / stored_name
    stored_path.write_bytes(contents)

    try:
        async with SessionLocal() as db:
            await db.execute(
                text("""INSERT INTO instr.qr_file
                            (id, original_name, stored_path, uploaded_by, file_size, mime_type, bundle_id)
                        VALUES (:id, :name, :path, :uploader, :size, :mime, :bundle_id)"""),
                {
                    "id": file_id, "name": file.filename, "path": stored_name,
                    "uploader": current_user.login, "size": len(contents),
                    "mime": file.content_type or "application/octet-stream",
                    "bundle_id": bundle_id,
                },
            )
            await db.commit()
    except Exception as e:
        stored_path.unlink(missing_ok=True)
        logger.error(f"QR bundle upload: DB error: {e}", exc_info=True)
        raise HTTPException(500, "Ошибка сохранения в базе данных")

    logger.info(f"QR bundle upload: user={current_user.login}, bundle={bundle_id}, file={file.filename}")
    base_url = get_base_url()
    return {
        "success": True,
        "file_id": file_id,
        "original_name": file.filename,
        "file_url": f"{base_url}/qr/file/{file_id}",
        "file_size": len(contents),
    }


@router.get("/api/qr/bundle/{bundle_id}")
async def get_bundle(bundle_id: str):
    """Публичный эндпоинт — данные пакета и список файлов. Авторизация не нужна."""
    async with SessionLocal() as db:
        bundle_row = (await db.execute(
            text("SELECT id, name, description, created_at FROM instr.qr_bundle WHERE id = :id"),
            {"id": bundle_id},
        )).fetchone()
        if not bundle_row:
            raise HTTPException(404, "Пакет не найден")

        files_rows = (await db.execute(
            text("""SELECT id, original_name, file_size, mime_type, uploaded_at
                    FROM instr.qr_file WHERE bundle_id = :bid ORDER BY uploaded_at ASC"""),
            {"bid": bundle_id},
        )).fetchall()

    base_url = get_base_url()
    return {
        "bundle_id": bundle_id,
        "name": bundle_row.name,
        "description": bundle_row.description or "",
        "created_at": bundle_row.created_at.isoformat() if bundle_row.created_at else None,
        "files": [
            {
                "file_id": str(r.id),
                "original_name": r.original_name,
                "file_size": r.file_size,
                "mime_type": r.mime_type,
                "file_url": f"{base_url}/qr/file/{r.id}",
            }
            for r in files_rows
        ],
    }


@router.get("/api/qr/bundles")
async def list_bundles(current_user: User = Depends(get_current_user)):
    """Список пакетов текущего пользователя."""
    if current_user.role not in QR_ALLOWED_ROLES:
        raise HTTPException(403, "Нет доступа")

    async with SessionLocal() as db:
        rows = (await db.execute(
            text("""SELECT b.id, b.name, b.description, b.created_at,
                           COUNT(f.id)::int AS file_count
                    FROM instr.qr_bundle b
                    LEFT JOIN instr.qr_file f ON f.bundle_id = b.id
                    WHERE b.uploaded_by = :uploader
                    GROUP BY b.id, b.name, b.description, b.created_at
                    ORDER BY b.created_at DESC LIMIT 100"""),
            {"uploader": current_user.login},
        )).fetchall()

    base_url = get_base_url()
    return {
        "bundles": [
            {
                "bundle_id": str(r.id),
                "name": r.name,
                "description": r.description or "",
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "file_count": r.file_count,
                "bundle_url": f"{base_url}/qr/bundle/{r.id}",
            }
            for r in rows
        ]
    }


@router.get("/api/qr/bundle/{bundle_id}/qr-image")
async def get_bundle_qr(bundle_id: str, current_user: User = Depends(get_current_user)):
    """QR-изображение для пакета."""
    import qrcode

    if current_user.role not in QR_ALLOWED_ROLES:
        raise HTTPException(403, "Нет доступа")

    async with SessionLocal() as db:
        row = (await db.execute(
            text("SELECT id FROM instr.qr_bundle WHERE id = :id"), {"id": bundle_id}
        )).fetchone()
    if not row:
        raise HTTPException(404, "Пакет не найден")

    base_url = get_base_url()
    bundle_url = f"{base_url}/qr/bundle/{bundle_id}"

    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(bundle_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    return JSONResponse({"qr_base64": qr_b64, "bundle_url": bundle_url})


@router.delete("/api/qr/bundle/{bundle_id}")
async def delete_bundle(bundle_id: str, current_user: User = Depends(get_current_user)):
    """Удалить пакет вместе со всеми файлами."""
    if current_user.role not in QR_ALLOWED_ROLES:
        raise HTTPException(403, "Нет доступа")

    async with SessionLocal() as db:
        bundle_row = (await db.execute(
            text("SELECT id, uploaded_by FROM instr.qr_bundle WHERE id = :id"), {"id": bundle_id}
        )).fetchone()
    if not bundle_row:
        raise HTTPException(404, "Пакет не найден")
    if bundle_row.uploaded_by != current_user.login and current_user.role not in {"ADMIN", "GLOBAL"}:
        raise HTTPException(403, "Нельзя удалять чужой пакет")

    # Удаляем файлы пакета с диска
    async with SessionLocal() as db:
        files = (await db.execute(
            text("SELECT stored_path FROM instr.qr_file WHERE bundle_id = :bid"), {"bid": bundle_id}
        )).fetchall()
        for f in files:
            (QR_STORAGE / f.stored_path).unlink(missing_ok=True)
        await db.execute(text("DELETE FROM instr.qr_bundle WHERE id = :id"), {"id": bundle_id})
        await db.commit()

    logger.info(f"QR bundle delete: user={current_user.login}, bundle_id={bundle_id}")
    return {"success": True, "message": "Пакет удалён"}


@router.delete("/api/qr/bundle/{bundle_id}/file/{file_id}")
async def remove_file_from_bundle(
    bundle_id: str, file_id: str,
    current_user: User = Depends(get_current_user),
):
    """Удалить один файл из пакета."""
    if current_user.role not in QR_ALLOWED_ROLES:
        raise HTTPException(403, "Нет доступа")

    async with SessionLocal() as db:
        row = (await db.execute(
            text("SELECT stored_path, uploaded_by FROM instr.qr_file WHERE id = :id AND bundle_id = :bid"),
            {"id": file_id, "bid": bundle_id},
        )).fetchone()
    if not row:
        raise HTTPException(404, "Файл не найден в этом пакете")
    if row.uploaded_by != current_user.login and current_user.role not in {"ADMIN", "GLOBAL"}:
        raise HTTPException(403, "Нельзя удалять чужие файлы")

    (QR_STORAGE / row.stored_path).unlink(missing_ok=True)
    async with SessionLocal() as db:
        await db.execute(text("DELETE FROM instr.qr_file WHERE id = :id"), {"id": file_id})
        await db.commit()

    return {"success": True}


@router.get("/qr/bundle/{bundle_id}")
async def view_bundle_redirect(bundle_id: str):
    """Публичный QR-переход — редирект на страницу просмотра пакета."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/web/qr-bundle.html?id={bundle_id}")


@router.put("/api/qr/bundle/{bundle_id}")
async def update_bundle(
    bundle_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Изменить название и/или описание пакета."""
    if current_user.role not in QR_ALLOWED_ROLES:
        raise HTTPException(403, "Нет доступа")

    body = await request.json()
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip()
    if not name:
        raise HTTPException(400, "Укажите название пакета")

    async with SessionLocal() as db:
        row = (await db.execute(
            text("SELECT uploaded_by FROM instr.qr_bundle WHERE id = :id"),
            {"id": bundle_id},
        )).fetchone()
        if not row:
            raise HTTPException(404, "Пакет не найден")
        if row.uploaded_by != current_user.login and current_user.role not in {"ADMIN", "GLOBAL"}:
            raise HTTPException(403, "Нельзя редактировать чужой пакет")

        await db.execute(
            text("UPDATE instr.qr_bundle SET name = :name, description = :desc WHERE id = :id"),
            {"name": name, "desc": description, "id": bundle_id},
        )
        await db.commit()

    logger.info(f"QR bundle updated: user={current_user.login}, bundle_id={bundle_id}, name={name}")
    return {"success": True, "name": name, "description": description}
