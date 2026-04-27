"""
API эндпоинты для работы с AI-вопросами по инструктажам
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from typing import Dict, List, Optional
from pathlib import Path
import json
import logging

from .auth import get_current_user, get_db
from .models_auth import User as UserModel
from .models import InstructionalQuestion, QuizAttempt
from .settings import get_settings
from .ai_questions import (
    extract_text_from_file,
    generate_questions_openai,
    validate_answers
)

router = APIRouter(prefix="/api/quiz", tags=["quiz"])
logger = logging.getLogger(__name__)

class QuizRequest(BaseModel):
    file_path: str  # путь к файлу (instruktagi/vneplanovy/2025-11/file.pdf)
    language: str = "ru"  # 'ru' или 'kk'

class QuizSubmitRequest(BaseModel):
    file_path: str
    language: str
    answers: Dict[str, str]  # {"0": "B", "1": "A", ...}

class QuizResponse(BaseModel):
    questions: List[Dict]
    language: str
    cached: bool = False

class QuizResultResponse(BaseModel):
    passed: bool
    correct_count: int
    total_count: int
    score_percentage: float
    details: List[Dict]


@router.post("/get-questions", response_model=QuizResponse)
async def get_questions(
    request: QuizRequest,
    user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Получить вопросы для файла инструктажа
    - Сначала проверяет кеш в БД
    - Если нет - генерирует через OpenAI и сохраняет
    """
    settings = get_settings()
    
    if not settings.ai_questions_enabled:
        raise HTTPException(400, "AI вопросы отключены")
    
    if not settings.openai_api_key:
        raise HTTPException(500, "OpenAI API ключ не настроен")
    
    # Проверяем кеш
    cached_q = (await db.execute(
        select(InstructionalQuestion).where(
            InstructionalQuestion.file_path == request.file_path,
            InstructionalQuestion.language == request.language
        )
    )).scalar_one_or_none()
    
    if cached_q:
        return QuizResponse(
            questions=cached_q.questions["questions"],
            language=cached_q.language,
            cached=True
        )
    
    # Генерируем новые вопросы
    storage_path = Path("storage") / request.file_path
    
    if not storage_path.exists():
        raise HTTPException(404, f"Файл не найден: {request.file_path}")
    
    # Извлекаем текст
    try:
        file_text = extract_text_from_file(str(storage_path))
    except Exception as e:
        raise HTTPException(400, f"Ошибка чтения файла: {str(e)}")
    
    if not file_text or len(file_text) < 100:
        raise HTTPException(400, f"Файл пустой или слишком короткий (извлечено {len(file_text) if file_text else 0} символов). Возможно, PDF содержит только изображения (сканы).")
    
    # Генерируем вопросы через OpenAI
    try:
        result = generate_questions_openai(
            text=file_text,
            api_key=settings.openai_api_key,
            language=request.language,
            num_questions=settings.ai_questions_count
        )
    except Exception as e:
        logger.error(
            f"OpenAI question generation failed: file={request.file_path}, "
            f"language={request.language}, text_length={len(file_text)}, error={str(e)}",
            exc_info=True
        )
        raise HTTPException(500, f"Ошибка генерации вопросов: {str(e)}")
    
    # Проверяем результат с подробным логированием
    if not result.get("questions"):
        logger.error(
            f"OpenAI returned empty questions: file={request.file_path}, "
            f"language={request.language}, result={result}"
        )
        raise HTTPException(
            500, 
            "Не удалось сгенерировать вопросы. OpenAI вернул пустой ответ."
        )
    
    # Сохраняем в кеш с обработкой race condition
    try:
        new_q = InstructionalQuestion(
            file_path=request.file_path,
            language=request.language,
            questions=result
        )
        db.add(new_q)
        await db.commit()
    except IntegrityError:
        # Другой запрос успел создать кеш раньше - откатываем и используем его
        await db.rollback()
        stmt = select(InstructionalQuestion).where(
            InstructionalQuestion.file_path == request.file_path,
            InstructionalQuestion.language == request.language
        ).order_by(InstructionalQuestion.generated_at.desc())
        result_cached = await db.execute(stmt)
        cached_q = result_cached.scalar_one_or_none()
        if cached_q:
            return QuizResponse(
                questions=cached_q.questions["questions"],
                language=cached_q.language,
                cached=True
            )
    
    return QuizResponse(
        questions=result["questions"],
        language=request.language,
        cached=False
    )


@router.post("/submit", response_model=QuizResultResponse)
async def submit_quiz(
    request: QuizSubmitRequest,
    user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Проверить ответы пользователя на вопросы
    - Сохраняет попытку в БД
    - Возвращает результат (прошел/не прошел)
    """
    settings = get_settings()
    
    # Получаем вопросы из кеша
    cached_q = (await db.execute(
        select(InstructionalQuestion).where(
            InstructionalQuestion.file_path == request.file_path,
            InstructionalQuestion.language == request.language
        )
    )).scalar_one_or_none()
    
    if not cached_q:
        raise HTTPException(404, "Вопросы не найдены. Сначала вызовите get-questions")
    
    questions = cached_q.questions["questions"]
    
    # Валидируем ответы
    validation_result = validate_answers(questions, request.answers)
    
    # Извлекаем табельный номер и company_id из токена
    idnum = user.login.split('@')[0] if '@' in user.login else user.login
    
    # Для EMP - извлекаем company_id из логина
    if user.role.upper() in ["EMP", "EMPLOYEE"]:
        org_code = user.login.split('@')[1] if '@' in user.login else None
        if not org_code:
            raise HTTPException(400, "Не удалось определить компанию")
        
        # Получаем company_id
        code_row = (await db.execute(
            text("SELECT company_id FROM public.company_login_code WHERE code = :code LIMIT 1"),
            {"code": org_code}
        )).mappings().first()
        
        if not code_row:
            raise HTTPException(400, f"Компания не найдена: {org_code}")
        
        company_id = code_row["company_id"]
    else:
        company_id = user.scope_company_id or 0
    
    # Сохраняем попытку
    attempt = QuizAttempt(
        file_path=request.file_path,
        idnum=idnum,
        company_id=company_id,
        language=request.language,
        questions_shown={"questions": questions},
        answers_given=request.answers,
        correct_count=validation_result["correct_count"],
        total_count=validation_result["total_count"],
        passed=validation_result["passed"]
    )
    db.add(attempt)
    await db.commit()
    
    return QuizResultResponse(
        passed=validation_result["passed"],
        correct_count=validation_result["correct_count"],
        total_count=validation_result["total_count"],
        score_percentage=validation_result["score_percentage"],
        details=validation_result["details"]
    )


@router.get("/check-passed/{file_path:path}")
async def check_quiz_passed(
    file_path: str,
    language: str = "ru",
    user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Проверить, проходил ли пользователь тест по этому файлу
    """
    idnum = user.login.split('@')[0] if '@' in user.login else user.login
    
    # Для EMP - извлекаем company_id
    if user.role.upper() in ["EMP", "EMPLOYEE"]:
        org_code = user.login.split('@')[1] if '@' in user.login else None
        if not org_code:
            return {"passed": False, "attempted": False}
        
        code_row = (await db.execute(
            text("SELECT company_id FROM public.company_login_code WHERE code = :code LIMIT 1"),
            {"code": org_code}
        )).mappings().first()
        
        if not code_row:
            return {"passed": False, "attempted": False}
        
        company_id = code_row["company_id"]
    else:
        company_id = user.scope_company_id or 0
    
    # Ищем успешную попытку
    attempt = (await db.execute(
        select(QuizAttempt).where(
            QuizAttempt.file_path == file_path,
            QuizAttempt.idnum == idnum,
            QuizAttempt.company_id == company_id,
            QuizAttempt.language == language,
            QuizAttempt.passed == True
        ).order_by(QuizAttempt.attempted_at.desc())
    )).scalar_one_or_none()
    
    if attempt:
        return {
            "passed": True,
            "attempted": True,
            "score_percentage": float(attempt.score_percentage) if attempt.score_percentage else 0,
            "attempted_at": attempt.attempted_at.isoformat()
        }
    
    # Проверяем любые попытки
    any_attempt = (await db.execute(
        select(QuizAttempt).where(
            QuizAttempt.file_path == file_path,
            QuizAttempt.idnum == idnum,
            QuizAttempt.company_id == company_id,
            QuizAttempt.language == language
        ).order_by(QuizAttempt.attempted_at.desc())
    )).scalar_one_or_none()
    
    if any_attempt:
        return {
            "passed": False,
            "attempted": True,
            "score_percentage": float(any_attempt.score_percentage) if any_attempt.score_percentage else 0,
            "attempts_count": 1  # можно расширить для подсчета всех попыток
        }
    
    return {"passed": False, "attempted": False}
