"""
Firebase Cloud Messaging (FCM) Integration
API endpoints для регистрации устройств и отправки push-уведомлений
"""

import os
import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import httpx  # Для прямых HTTP запросов к Legacy FCM API

from .auth import get_current_user, get_db
from .models_auth import User as UserModel

logger = logging.getLogger(__name__)

# ===== Firebase Initialization =====

def init_firebase():
    """Инициализация Firebase Admin SDK"""
    try:
        possible_paths = [
            "firebase-service-account.json",
            "../firebase-service-account.json",
            "/home/instr/app/firebase-service-account.json",
            os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "firebase-service-account.json")
        ]
        
        service_account_path = None
        for path in possible_paths:
            if os.path.exists(path):
                service_account_path = path
                break
        
        if not service_account_path:
            logger.error("firebase-service-account.json not found")
            return False
        
        if not firebase_admin._apps:
            cred = credentials.Certificate(service_account_path)
            
            # Логируем project_id из service account
            import json
            with open(service_account_path, 'r') as f:
                sa_data = json.load(f)
                project_id = sa_data.get('project_id', 'UNKNOWN')
                logger.info(f"🔑 Service Account project_id: {project_id}")
            
            firebase_admin.initialize_app(cred)
            logger.info(f"✅ Firebase Admin SDK initialized: {service_account_path}")
            
            # Проверяем что приложение действительно инициализировано
            app = firebase_admin.get_app()
            logger.info(f"📱 Firebase app name: {app.name}, project_id: {app.project_id}")
        
        return True
    
    except Exception as e:
        logger.error(f"❌ Failed to initialize Firebase: {e}")
        return False

# Инициализация при импорте
firebase_initialized = init_firebase()

# ===== Router =====

router = APIRouter(prefix="/api/fcm", tags=["push-notifications"])

# ===== Pydantic Models =====

class DeviceTokenRequest(BaseModel):
    token: str
    device_info: Optional[dict] = None

class NotificationRequest(BaseModel):
    title: str
    body: str
    company_id: Optional[int] = None
    data: Optional[dict] = None

# ===== API Endpoints =====

@router.post("/register-device")
async def register_device(
    request: DeviceTokenRequest,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Регистрация FCM токена устройства
    Вызывается из Android app при получении токена
    """
    logger.info(f"🔔 /api/fcm/register-device called by user {current_user.login} (ID={current_user.id})")
    logger.info(f"   Role: {current_user.role}, scope_company_id={current_user.scope_company_id}, company_id={getattr(current_user, 'company_id', None)}")
    logger.info(f"   Token preview: {request.token[:50]}...")
    logger.info(f"   Device info: {request.device_info}")
    
    if not firebase_initialized:
        logger.error("❌ Firebase not initialized!")
        raise HTTPException(
            status_code=503,
            detail="Firebase not configured"
        )
    
    try:
        user_id = current_user.id
        
        # Проверить существование токена
        result = await db.execute(
            text("SELECT id FROM device_tokens WHERE token = :token"),
            {"token": request.token}
        )
        existing = result.first()
        
        if existing:
            # Обновить user_id и device_info
            await db.execute(
                text("""
                UPDATE device_tokens 
                SET device_info = :device_info, updated_at = NOW(), user_id = :user_id
                WHERE token = :token
                """),
                {"device_info": request.device_info, "user_id": user_id, "token": request.token}
            )
            logger.info(f"✅ Updated device token for user {user_id} ({current_user.login})")
        else:
            # Вставить новый токен
            await db.execute(
                text("""
                INSERT INTO device_tokens (user_id, token, device_info)
                VALUES (:user_id, :token, :device_info)
                """),
                {"user_id": user_id, "token": request.token, "device_info": request.device_info}
            )
            logger.info(f"✅ Registered NEW device token for user {user_id} ({current_user.login})")
        
        await db.commit()
        logger.info(f"✅ Transaction committed successfully")
        return {"status": "success", "message": "Device registered"}
    
    except Exception as e:
        await db.rollback()
        logger.error(f"Error registering device: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/unregister-device")
async def unregister_device(
    token: str,
    current_user: UserModel = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Удаление FCM токена (при logout)"""
    try:
        await db.execute(
            text("DELETE FROM device_tokens WHERE token = :token AND user_id = :user_id"),
            {"token": token, "user_id": current_user.id}
        )
        await db.commit()
        return {"status": "success"}
    
    except Exception as e:
        await db.rollback()
        logger.error(f"Error unregistering device: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===== Helper Functions =====

async def get_company_tokens(company_id: int, db: AsyncSession) -> List[str]:
    """Получить все FCM токены пользователей компании"""
    query = text("""
        SELECT DISTINCT dt.token
        FROM device_tokens dt
        JOIN instr.user u ON dt.user_id = u.id
        WHERE u.scope_company_id = :company_id
    """)
    result = await db.execute(query, {"company_id": company_id})
    rows = result.fetchall()
    return [row[0] for row in rows]


async def send_notification_to_company(
    company_id: int,
    title: str,
    body: str,
    db: AsyncSession,
    data: Optional[dict] = None
):
    """
    Отправить push-уведомление всем пользователям компании через FCM HTTP v1 API
    """
    if not firebase_initialized:
        logger.warning("Firebase not initialized, skipping notification")
        return {"success_count": 0, "failure_count": 0}
    
    try:
        tokens = await get_company_tokens(company_id, db)
        
        logger.info(f"📤 [FCM v1] Sending notification to company {company_id}: '{title}'")
        logger.info(f"   Found {len(tokens)} device tokens")
        logger.info(f"   Tokens: {[t[:50] + '...' for t in tokens]}")
        
        if not tokens:
            logger.warning(f"❌ No device tokens for company {company_id}")
            return {"success_count": 0, "failure_count": 0}
        
        success_count = 0
        failure_count = 0
        failed_tokens = []
        
        # Отправляем по одному через messaging.send() вместо send_each()
        for idx, token in enumerate(tokens):
            try:
                message = messaging.Message(
                    notification=messaging.Notification(
                        title=title,
                        body=body
                    ),
                    data=data or {},
                    token=token
                )
                
                # Используем send() вместо send_each() - он работает надежнее
                message_id = messaging.send(message, app=firebase_admin.get_app())
                success_count += 1
                logger.info(f"✅ Token {idx} sent successfully, message_id={message_id}")
                
            except messaging.UnregisteredError:
                failure_count += 1
                failed_tokens.append(token)
                logger.warning(f"⚠️ Token {idx} - UnregisteredError (token invalid, will be cleaned up)")
            except messaging.SenderIdMismatchError:
                failure_count += 1
                failed_tokens.append(token)
                logger.warning(f"⚠️ Token {idx} - SenderIdMismatchError (wrong project, will be cleaned up)")
            except Exception as e:
                failure_count += 1
                failed_tokens.append(token)
                logger.error(f"❌ Token {idx} exception: {type(e).__name__}: {e}")
        
        logger.info(f"✅ Sent {success_count} notifications to company {company_id}, ❌ {failure_count} failed")
        
        if failed_tokens:
            logger.info(f"🧹 Cleaning up {len(failed_tokens)} invalid tokens from database")
            await cleanup_invalid_tokens(failed_tokens, db)
        
        return {
            "success_count": success_count,
            "failure_count": failure_count
        }
    
    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        return {"error": str(e)}


async def cleanup_invalid_tokens(tokens: List[str], db: AsyncSession):
    """Удалить недействительные токены из БД"""
    if not tokens:
        return
    
    try:
        # SQLAlchemy не поддерживает ANY напрямую, используем IN
        placeholders = ", ".join([f":token_{i}" for i in range(len(tokens))])
        params = {f"token_{i}": token for i, token in enumerate(tokens)}
        
        await db.execute(
            text(f"DELETE FROM device_tokens WHERE token IN ({placeholders})"),
            params
        )
        await db.commit()
        logger.info(f"Cleaned up {len(tokens)} invalid tokens")
    except Exception as e:
        await db.rollback()
        logger.error(f"Error cleaning up tokens: {e}")
