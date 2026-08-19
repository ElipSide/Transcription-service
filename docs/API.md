# API транскрибации и диаризации

Документация по программному обращению к сервису. Есть два уровня:

| Способ | Когда использовать | Авторизация |
|---|---|---|
| **Веб-API** (рекомендуется) | загрузка файлов, история, отчёты/выжимки, PDF | JWT-токен |
| **DiarService напрямую** | только распознавание + диаризация, без истории и AI-отчётов | нет |

- **База веб-API:** `https://csort-news.ru/transcribe/api`
- **База DiarService:** `https://109.202.9.107:8443/diaraudio` (самоподписанный TLS — в `curl` нужен `-k`)

---

## Часть 1. Веб-API

Полный путь: загрузка файла → задача → опрос статуса → результат (текст/сегменты) → PDF.

### 1.1. Аутентификация

Все запросы к `/api/jobs/*` требуют заголовок `Authorization: Bearer <access_token>`.

Способы получить токены (все возвращают `{access_token, refresh_token, token_type}`):

```bash
# Гость — без регистрации. Данные живут ~24 часа, потом удаляются.
curl -s -X POST https://csort-news.ru/transcribe/api/auth/guest

# Регистрация постоянного аккаунта
curl -s -X POST https://csort-news.ru/transcribe/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"secret123","full_name":"Имя"}'

# Вход
curl -s -X POST https://csort-news.ru/transcribe/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"secret123"}'
```

- `access_token` живёт 30 минут. Когда истёк — обновить:
  ```bash
  curl -s -X POST https://csort-news.ru/transcribe/api/auth/refresh \
    -H "Content-Type: application/json" \
    -d '{"refresh_token":"<refresh_token>"}'
  ```
- Проверить, кто вы: `GET /api/auth/me`.

### 1.2. Загрузка файла (chunked)

Файл заливается частями по 8 МБ. Лимит — 2 ГБ. Три шага:

```bash
TOKEN="<access_token>"
BASE="https://csort-news.ru/transcribe/api"
FILE="meeting.ogg"

# 1) init — регистрируем загрузку, получаем upload_id (= job_id) и размер части
INIT=$(curl -s -X POST "$BASE/jobs/upload/init" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"filename\":\"$FILE\",\"file_size\":$(stat -c%s "$FILE"),\"mode\":\"transcription\"}")
UID=$(echo "$INIT" | python3 -c "import sys,json;print(json.load(sys.stdin)['upload_id'])")

# 2) chunk — шлём части (здесь одним куском для примера; index с нуля)
curl -s -X POST "$BASE/jobs/upload/$UID/chunk?index=0" \
  -H "Authorization: Bearer $TOKEN" -F "chunk=@$FILE"

# 3) complete — собираем файл, задача уходит в очередь
curl -s -X POST "$BASE/jobs/upload/$UID/complete" -H "Authorization: Bearer $TOKEN"
# -> {"job_id":"..."}
```

Параметры `init`:

| Поле | Значение |
|---|---|
| `filename` | имя файла (для отображения и имён PDF) |
| `file_size` | размер в байтах |
| `mode` | `transcription` \| `report` \| `ai_extract` |
| `batch_id` | *(опц.)* один UUID на группу файлов — тогда их можно скачать архивом одной пачкой |

Реальные части файла — по `chunk_size` из ответа `init` (8 МБ), `index` последовательно с `0`.

### 1.3. Ожидание результата

**Вариант А — опрос** `GET /api/jobs/{job_id}` раз в 2–3 c, пока `status` не станет `completed` или `failed`.

**Вариант Б — WebSocket** (живой прогресс):
```
wss://csort-news.ru/transcribe/api/jobs/{job_id}/ws?token=<access_token>
```
Приходят кадры `{status, progress, stage_message, error}`; по `completed`/`failed` соединение закрывается.

Статусы: `uploading` → `queued` → (`waiting`) → `processing` → `completed` / `failed`.
`waiting` — сервис распознавания временно недоступен, задача сама уйдёт в работу позже.

### 1.4. Получение результата

`GET /api/jobs/{job_id}` возвращает объект задачи:

```jsonc
{
  "id": "…", "batch_id": null,
  "original_filename": "meeting.ogg", "file_size": 12345678,
  "mode": "transcription", "status": "completed",
  "progress": 100, "stage_message": "Готово", "error": null,
  "created_at": "…", "updated_at": "…",

  "result_text": "Спикер 1: …\nСпикер 2: …",   // сплошной текст
  "result_segments": [                            // по репликам
    { "id": 1, "speaker": "SPEAKER_00", "start_ms": 0, "end_ms": 8000, "text": "…" }
  ],
  "report_html": "…",            // HTML отчёта / markdown выжимки (для report/ai_extract)
  "diarization_note": null,      // заметка, если диаризация деградировала
  "has_transcript_pdf": true,
  "has_report_pdf": false
}
```

### 1.5. Скачивание PDF

```bash
# kind = transcript | report
curl -s -X GET "$BASE/jobs/$JOB/download/transcript" \
  -H "Authorization: Bearer $TOKEN" -o "result.pdf"
```
Имя файла в заголовке ответа — уже человекочитаемое (`Транскрибация - meeting.pdf`).

### 1.6. Прочее

```bash
# Список задач
curl -s "$BASE/jobs" -H "Authorization: Bearer $TOKEN"

# Обработать готовую транскрипцию в другом режиме (без повторного распознавания)
curl -s -X POST "$BASE/jobs/$JOB/rerun" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"mode":"ai_extract"}'          # -> {"job_id":"…"}

# Скачать результаты нескольких задач архивом (ZIP)
curl -s -X POST "$BASE/jobs/archive" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"job_ids":["id1","id2"],"kind":"all"}' -o results.zip   # kind: all|transcript|report

# Удалить задачу и её файлы
curl -s -X DELETE "$BASE/jobs/$JOB" -H "Authorization: Bearer $TOKEN"

# Доступность распознавания / здоровье
curl -s "$BASE/diar/status"     # {"available":true}
curl -s "$BASE/health"          # {"status":"ok"}
```

### 1.7. Полный пример на Python

```python
import time, requests

BASE = "https://csort-news.ru/transcribe/api"

# 1) токен (гость)
tok = requests.post(f"{BASE}/auth/guest").json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

# 2) загрузка
path, name = "meeting.ogg", "meeting.ogg"
data = open(path, "rb").read()
init = requests.post(f"{BASE}/jobs/upload/init", headers=H,
                     json={"filename": name, "file_size": len(data),
                           "mode": "transcription"}).json()
uid, chunk = init["upload_id"], init["chunk_size"]
for i in range(0, len(data), chunk):
    requests.post(f"{BASE}/jobs/upload/{uid}/chunk", headers=H,
                  params={"index": i // chunk},
                  files={"chunk": data[i:i + chunk]})
job = requests.post(f"{BASE}/jobs/upload/{uid}/complete", headers=H).json()["job_id"]

# 3) ожидание
while True:
    j = requests.get(f"{BASE}/jobs/{job}", headers=H).json()
    print(j["status"], j["progress"], j.get("stage_message"))
    if j["status"] in ("completed", "failed"):
        break
    time.sleep(3)

# 4) результат
if j["status"] == "completed":
    print(j["result_text"])
    pdf = requests.get(f"{BASE}/jobs/{job}/download/transcript", headers=H).content
    open("result.pdf", "wb").write(pdf)
else:
    print("Ошибка:", j["error"])
```

---

## Часть 2. DiarService напрямую

Только распознавание речи (whisper) + диаризация (NeMo MSDD). Без аккаунтов, истории и AI-отчётов. **Открыт без авторизации** — использовать только из доверенной сети.

> Отправляйте **WAV 16 кГц, моно** — так надёжнее всего. Другие форматы могут работать, если whisper.cpp собран с ffmpeg, но лучше конвертировать заранее:
> ```bash
> ffmpeg -i input.ogg -ac 1 -ar 16000 -c:a pcm_s16le out_16k.wav
> ```

### 2.1. Загрузка

```bash
curl -k -s -X POST "https://109.202.9.107:8443/diaraudio/upload" \
  -F "file=@out_16k.wav"
# -> {"task_id":"<uuid>","ws":"/ws/status/<uuid>"}
```

### 2.2. Статус и результат (WebSocket)

```
wss://109.202.9.107:8443/diaraudio/ws/status/<task_id>
```
Кадры:
```jsonc
{ "status": "processing", "progress": 42, "message": "whisper.cpp: …" }
// по завершении:
{ "status": "completed", "progress": 100,
  "result_text": "Speaker 0: …",
  "result_segments": [ {"id":1,"speaker":"Speaker 0","start_ms":0,"end_ms":8000,"text":"…"} ],
  "diarization_error": null }   // присутствует, только если диаризация упала
// при ошибке:
{ "status": "failed", "message": "Ошибка обработки", "error": "<причина>" }
```

### 2.3. Пример на Python

```python
import json, requests, websocket   # pip install websocket-client

BASE = "https://109.202.9.107:8443/diaraudio"
up = requests.post(f"{BASE}/upload", files={"file": open("out_16k.wav", "rb")},
                   verify=False).json()
ws = websocket.create_connection(
    f"wss://109.202.9.107:8443/diaraudio/ws/status/{up['task_id']}",
    sslopt={"cert_reqs": 0})       # самоподписанный cert
while True:
    msg = json.loads(ws.recv())
    print(msg["status"], msg.get("progress"), msg.get("message", ""))
    if msg["status"] == "completed":
        print(msg["result_text"])
        break
    if msg["status"] == "failed":
        print("Ошибка:", msg.get("error"))
        break
ws.close()
```

---

## Справочник

**Режимы (`mode`)**
| Значение | Что делает |
|---|---|
| `transcription` | текст со спикерами и таймкодами |
| `report` | структурированный деловой отчёт (AI) |
| `ai_extract` | короткая выжимка ключевых пунктов (AI) |

**Статусы задачи (`status`)**
`uploading` · `queued` · `waiting` · `processing` · `completed` · `failed`

**Сегмент (`result_segments[]`)**
| Поле | Тип | Описание |
|---|---|---|
| `id` | int | порядковый номер |
| `speaker` | str | метка спикера (`SPEAKER_00` / `Speaker 0`) |
| `start_ms`, `end_ms` | int | границы реплики, мс |
| `text` | str | текст реплики |

**Ограничения**
- Максимальный размер файла: **2 ГБ**.
- Размер части при загрузке: **8 МБ** (значение из ответа `init`).
- `access_token`: 30 минут; `refresh_token`: 14 дней.
- Гостевые данные удаляются через ~24 часа.

**Типичные коды ответов**
| Код | Значение |
|---|---|
| `401` | нет/просрочен токен → обновить через `refresh` |
| `404` | задача не найдена / файл недоступен |
| `409` | загрузка уже завершена / rerun недоступен для незавершённой задачи |
| `413` | файл больше лимита |
