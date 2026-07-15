# Transcription Service (веб-версия)

Веб-приложение для загрузки аудио/видео, распознавания речи с диаризацией и просмотра
истории. Заменяет Telegram-бота (`csort-report-bot`) и локальный `telegram-bot-api`.
Бэкенд по-прежнему использует существующий **DiarService** для распознавания/диаризации
и **DeepSeek** для отчётов.

```
Браузер (React SPA)
        │  REST + WebSocket
        ▼
Backend (FastAPI)  ──upload/WS──▶  DiarService (109.202.9.107:8443)
        │                                 (whisper.cpp + NeMo MSDD)
        ├── PostgreSQL  (пользователи, задачи, история)
        └── DeepSeek API (деловой отчёт / AI-выжимка)
```

## Возможности
- Регистрация и вход (email + пароль, JWT). Каждый пользователь видит **только свои** файлы.
- Загрузка больших файлов (до 2 ГБ) частями (chunked upload) — без Telegram и tdlib.
- Три режима: **Транскрипция**, **Деловой отчёт** (AI), **AI-выжимка** (AI).
- Прогресс обработки в реальном времени (WebSocket).
- Просмотр транскрипции по сегментам (спикер + таймкоды), скачивание PDF.
- История всех распознаваний.

---

## Запуск через Docker (рекомендуется)

Всё (PostgreSQL + бэкенд + фронтенд за nginx) поднимается одной командой.
Нужен только установленный Docker + Docker Compose.

```bash
cp .env.example .env
# сгенерировать секрет и вписать в .env -> SECRET_KEY:
python -c "import secrets; print(secrets.token_urlsafe(48))"
# (опционально) вписать DEEPSEEK_API_KEY для режимов отчётов

docker compose up -d --build
```
Приложение: **http://localhost:8095/transcribe/**
(порт меняется через `WEB_PORT`, а публичный под-путь — через `WEB_BASE` в `.env`;
для отдачи с корня укажите `WEB_BASE=/`).

Контейнеры:
| Сервис | Образ / сборка | Назначение |
|---|---|---|
| `db` | postgres:16-alpine | БД (том `db-data`) |
| `backend` | `./backend` (python:3.12-slim + ffmpeg + WeasyPrint) | FastAPI на :8000 (внутри сети) |
| `frontend` | `./frontend` (сборка React → nginx) | раздаёт SPA под `/transcribe/` и проксирует `/transcribe/api/` (+WebSocket) на backend :8000 |

Загруженные файлы и результаты хранятся в томе `storage`. Логи: `docker compose logs -f backend`.
Остановить: `docker compose down` (данные сохраняются; `down -v` — удалить и тома).

> ⚠️ `DiarService` (распознавание) — **внешний** сервис и в compose не входит: бэкенд
> ходит на адрес из `DIAR_BASE_URL`/`DIAR_WS_URL`. Убедитесь, что он доступен и поднят.

---

## Ручной запуск (без Docker)

### Требования
- Python 3.12+
- Node.js 18+
- PostgreSQL 13+
- `ffmpeg` в `PATH`
- Для генерации PDF на сервере: WeasyPrint (Linux — ставится из `requirements.txt`).
  Генерация PDF — best-effort: если не установлен, текст/HTML результат всё равно доступен в UI.

---

## Настройка БД (PostgreSQL)
```sql
CREATE USER transcribe WITH PASSWORD 'transcribe';
CREATE DATABASE transcribe OWNER transcribe;
```
Таблицы создаются автоматически при первом старте бэкенда (`init_db`).

## Бэкенд
```bash
cd backend
python -m venv venv
# Linux/macOS:  source venv/bin/activate
# Windows:      venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env          # затем отредактировать .env
# обязательно задать: DATABASE_URL, SECRET_KEY, DEEPSEEK_API_KEY, DIAR_BASE_URL/DIAR_WS_URL

uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Swagger доступен на `http://localhost:8000/docs`.

### Ключевые переменные `.env`
| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | строка подключения к PostgreSQL (asyncpg) |
| `SECRET_KEY` | секрет для подписи JWT (сгенерировать!) |
| `DIAR_BASE_URL` / `DIAR_WS_URL` | адрес DiarService (HTTP и WS) |
| `DEEPSEEK_API_KEY` | ключ DeepSeek для отчётов |
| `MAX_FILE_SIZE` | лимит размера файла (байт), по умолчанию 2 ГБ |
| `CORS_ORIGINS` | разрешённые origin фронтенда |

## Фронтенд
```bash
cd frontend
npm install
npm run dev          # http://localhost:5173 (проксирует /api на :8000)
```
Сборка для прода:
```bash
npm run build        # результат в frontend/dist — раздать любым статик-сервером / nginx
```

---

## Прод-развёртывание (кратко)
1. PostgreSQL + `ffmpeg` на сервере.
2. Бэкенд под systemd: `uvicorn app.main:app --host 127.0.0.1 --port 8000`
   (несколько воркеров требуют замены in-process pub/sub на Redis — см. `services/progress_hub.py`).
3. `npm run build` (при отдаче с под-пути укажите `VITE_BASE`, напр. `/transcribe/`),
   раздать `frontend/dist` через nginx; проксировать `/transcribe/api/` (вкл. WebSocket) на бэкенд.
4. DiarService должен быть доступен по адресам из `.env`.

## Структура
```
backend/app/
  main.py              точка входа FastAPI
  config.py            настройки (.env)
  database.py models.py schemas.py security.py deps.py
  routers/auth.py      регистрация / вход / refresh / me
  routers/jobs.py      chunked upload, список, деталь, скачивание, WS-прогресс
  services/
    diar_client.py     интеграция с DiarService
    processor.py       пайплайн: ffmpeg → DiarService → DeepSeek → PDF
    deepseek.py prompts.py pdf.py
    progress_hub.py    in-process pub/sub для live-прогресса
frontend/src/
  api.ts auth.tsx App.tsx
  pages/  Login Register Dashboard JobDetail
  components/ Layout Upload
```
