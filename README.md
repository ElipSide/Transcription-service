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
- Загрузка файлов: **одним запросом** (до 512 МБ, `POST /api/jobs/upload`) или
  **частями по 8 МБ** (до 2 ГБ, chunked) — без Telegram и tdlib.
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

---

## Обновление прода (деплой)

Прод: **https://csort-news.ru/transcribe/** — сервер `sammy` (185.209.31.149),
каталог `/home/sammy/Transcription-service`, поднят docker compose
(внешний nginx хоста проксирует `/transcribe/` → `127.0.0.1:8095`).

```bash
# 1) локально: закоммитить и отправить (прод тянет с форка ElipSide)
git push origin master
git push elipside master

# 2) на сервере
ssh sammy
cd ~/Transcription-service
git pull --ff-only
# (если рабочая копия «грязная» после старых деплоев синком файлов:
#  git fetch && git reset --hard origin/master)

docker compose up -d --build      # пересобрать изменившиеся образы и перезапустить
docker compose logs -f backend    # убедиться, что поднялся без ошибок

# 3) быстрая проверка после деплоя
curl -s https://csort-news.ru/transcribe/api/health                 # {"status":"ok"}
curl -s https://csort-news.ru/transcribe/api/openapi.json | grep -c '/api/jobs/upload'
curl -s -X POST https://csort-news.ru/transcribe/api/jobs/upload \
  -F 'file=@/dev/null'                                             # 401 = эндпоинт жив
```

- Данные (БД — том `db-data`, файлы — том `storage`) при пересборке не трогаются.
- Новые таблицы БД создаются автоматически при старте (`init_db`); **изменение
  колонок существующих таблиц** миграциями не накатывается — при таких правках
  ALTER вручную.
- Изменился `frontend/nginx.conf` или код фронтенда — пересобирается образ
  `frontend` (лимит тела и проксирование вшиты в образ), бэкенда — `backend`.
  `docker compose up -d --build` пересоберёт только нужный.

### Внешний прокси (nginx хоста)

`/etc/nginx/sites-available/dom.conf`, блок `location /transcribe/`, должен
содержать (актуально для однозапросной загрузки и WebSocket-прогресса):

```nginx
location /transcribe/ {
    proxy_pass http://127.0.0.1:8095;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;         # WebSocket (live-прогресс)
    proxy_set_header Connection $connection_upgrade;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;                       # длинные загрузки и распознавание
    proxy_send_timeout 3600s;
    proxy_request_buffering off;                    # тело сразу в контейнер, без буфера на диск
    client_max_body_size 512m;                      # ≥ лимита однозапросной загрузки (см. docs/API.md)
}
```

Правки прокси: `sudo nginx -t && sudo systemctl reload nginx`.
Внутренний nginx фронтенд-контейнера (`frontend/nginx.conf`) уже настроен так же
(512 МБ для `/transcribe/api/`); правится в репозитории и приезжает с деплоем.

## Структура
```
backend/app/
  main.py              точка входа FastAPI
  config.py            настройки (.env)
  database.py models.py schemas.py security.py deps.py
  routers/auth.py      регистрация / вход / refresh / me
  routers/jobs.py      загрузка (одним запросом + chunked), список, деталь, скачивание, WS-прогресс
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
