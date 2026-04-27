# HSE Instr — Система электронных инструктажей

Веб-приложение для проведения и учёта инструктажей по охране труда и промышленной безопасности. Включает AI-генерацию проверочных вопросов, QR-коды для документов, push-уведомления и подпись сотрудников.

---

## Возможности

- **Инструктажи** — просмотр материалов, прохождение проверки знаний
- **AI-вопросы** — автоматическая генерация тестов по тексту инструктажа (OpenAI)
- **Электронная подпись** — сотрудник подписывает прохождение инструктажа на экране
- **QR-генератор** — загрузка файлов (PDF, Excel, Word и др.) с автоматической генерацией QR-кода и страницей предпросмотра в браузере
- **Push-уведомления** — напоминания сотрудникам через Firebase Cloud Messaging
- **Дашборд** — статистика прохождений, экспорт отчётов
- **Аудит** — журнал всех действий пользователей

---

## Стек технологий

| Компонент | Технология |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy (async) |
| База данных | PostgreSQL 15 |
| Frontend | Vanilla HTML/CSS/JS (без фреймворков) |
| Веб-сервер | Nginx (SSL termination, static files) |
| Контейнеры | Docker + Docker Compose |
| Push-уведомления | Firebase Cloud Messaging (FCM) |
| AI | OpenAI API (GPT) |

---

## Структура проекта

```
instr-backend/
├── app/                      # FastAPI приложение
│   ├── app.py                #   Точка входа, роутеры
│   ├── auth.py               #   Аутентификация (JWT)
│   ├── models.py             #   SQLAlchemy модели
│   ├── schemas.py            #   Pydantic схемы
│   ├── db.py                 #   Подключение к БД
│   ├── settings.py           #   Конфигурация (через .env)
│   ├── quiz.py               #   API тестов/вопросов
│   ├── ai_questions.py       #   Генерация AI-вопросов
│   ├── qr_files.py           #   QR-генератор файлов
│   ├── upload.py             #   Загрузка файлов инструктажей
│   ├── fcm.py                #   Push-уведомления
│   ├── audit.py              #   Журнал аудита
│   └── utils.py              #   Вспомогательные функции
│
├── web/                      # Frontend (статические файлы)
│   ├── инструктаж.html       #   Главная страница инструктажей
│   ├── login.html            #   Страница входа
│   ├── dashboard.html        #   Дашборд администратора
│   ├── qr-upload.html        #   QR-генератор (загрузка файлов)
│   ├── qr-bundle.html        #   Просмотр пакета QR-документов
│   └── qr-file.html          #   Предпросмотр файла по QR-ссылке
│
├── nginx/
│   └── nginx.conf            #   Конфигурация Nginx (в .gitignore)
│
├── storage/                  #   Загружаемые файлы (в .gitignore)
│
├── _sql_migrations/          # SQL-миграции базы данных
├── _docs/                    # Дополнительная документация
├── _deploy_scripts/          # Скрипты деплоя и бэкапа
│
├── docker-compose.yml        # Docker Compose конфигурация
├── requirements.txt          # Python зависимости
├── .env.example              # Пример переменных окружения
├── nginx.conf.example        # Пример конфигурации Nginx
├── firebase-service-account.example.json  # Пример Firebase credentials
└── instr.service.example     # Пример systemd-сервиса
```

---

## Быстрый старт (Docker)

### 1. Клонируйте репозиторий

```bash
git clone https://github.com/YOUR_USERNAME/instr-backend.git
cd instr-backend
```

### 2. Настройте окружение

```bash
cp .env.example .env
# Отредактируйте .env — заполните DATABASE_URL, SECRET_KEY, BASE_URL и т.д.
```

### 3. Настройте Firebase (для push-уведомлений)

```bash
cp firebase-service-account.example.json firebase-service-account.json
# Замените значения на реальные из Firebase Console → Project Settings → Service Accounts
```

### 4. Настройте Nginx

```bash
cp nginx.conf.example nginx/nginx.conf
# Укажите ваш домен и пути к SSL-сертификатам
```

### 5. Запустите

```bash
docker compose up -d
```

Приложение будет доступно на `https://your-domain.com`.

---

## Локальная разработка (без Docker)

```bash
# Создайте виртуальное окружение
python -m venv venv
source venv/bin/activate      # Linux/Mac
venv\Scripts\activate         # Windows

# Установите зависимости
pip install -r requirements.txt

# Настройте .env (нужна запущенная PostgreSQL)
cp .env.example .env

# Запустите приложение
uvicorn app.app:app --reload --port 8000
```

---

## Переменные окружения

Все переменные описаны в [.env.example](.env.example). Обязательные:

| Переменная | Описание |
|---|---|
| `DATABASE_URL` | Строка подключения к PostgreSQL |
| `SECRET_KEY` | Секрет для подписи JWT токенов |
| `SIGN_SECRET` | Секрет для подписи документов |
| `BASE_URL` | Публичный URL сайта (для QR-ссылок) |
| `FIREBASE_SERVICE_ACCOUNT_PATH` | Путь к JSON-файлу Firebase credentials |

---

## База данных

Схема `instr` в PostgreSQL. Миграции находятся в папке `_sql_migrations/` — применяются по порядку в ручном режиме.

---

## Безопасность

- JWT аутентификация (Bearer token)
- Блокировка после N неудачных попыток входа
- Swagger/OpenAPI закрыты от публичного доступа через Nginx
- HTTPS с редиректом с HTTP, HSTS заголовки
- Переменные окружения — только через `.env`, никогда не коммитить в git

---

## Лицензия

Проект разработан для внутреннего использования. Все права защищены.
