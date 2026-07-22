import { useCallback, useRef, useState } from "react";
import { uploadFile, uuid } from "../api";
import type { JobMode } from "../types";
import { MODE_LABELS } from "../types";

const MODES: JobMode[] = ["transcription", "report", "ai_extract"];
const MODE_HINTS: Record<JobMode, string> = {
  transcription: "Текст со спикерами и таймкодами",
  report: "Структурированный деловой отчёт (AI)",
  ai_extract: "Короткая выжимка ключевых пунктов (AI)",
};
const MODE_ICONS: Record<JobMode, string> = {
  transcription: "📝",
  report: "📊",
  ai_extract: "💡",
};

type ItemStatus = "pending" | "uploading" | "done" | "error";

interface QueueItem {
  id: number;
  file: File;
  status: ItemStatus;
  pct: number;
  error?: string;
}

const STATUS_TEXT: Record<ItemStatus, string> = {
  pending: "В очереди",
  uploading: "Загрузка…",
  done: "✓ Загружено",
  error: "Ошибка",
};

function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 ** 3).toFixed(2)} ГБ`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 ** 2).toFixed(1)} МБ`;
  return `${(bytes / 1024).toFixed(0)} КБ`;
}

export default function Upload({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<JobMode>("transcription");
  const [items, setItems] = useState<QueueItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const nextId = useRef(0);

  const add = useCallback((list: FileList | null) => {
    if (!list || list.length === 0) return;
    const added: QueueItem[] = Array.from(list).map((file) => ({
      id: nextId.current++,
      file,
      status: "pending",
      pct: 0,
    }));
    setItems((prev) => [...prev, ...added]);
  }, []);

  const patch = useCallback((id: number, p: Partial<QueueItem>) => {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...p } : it)));
  }, []);

  const remove = useCallback((id: number) => {
    setItems((prev) => prev.filter((it) => it.id !== id));
  }, []);

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (busy) return;
    add(e.dataTransfer.files);
  }

  /** Upload one file at a time so the queue order is predictable and we don't
   *  saturate the connection; each finished file becomes a job immediately. */
  async function start() {
    const queue = items.filter((it) => it.status === "pending" || it.status === "error");
    if (queue.length === 0) return;
    setBusy(true);
    // One id for the whole run: these files stay a group you can download at once.
    const batchId = uuid();
    for (const it of queue) {
      patch(it.id, { status: "uploading", pct: 0, error: undefined });
      try {
        await uploadFile(it.file, mode, (p) => patch(it.id, { pct: p }), batchId);
        patch(it.id, { status: "done", pct: 100 });
        onDone(); // job created — refresh the list below right away
      } catch (err: any) {
        patch(it.id, { status: "error", error: err.message ?? "Ошибка загрузки" });
      }
    }
    setBusy(false);
    if (inputRef.current) inputRef.current.value = "";
    // Successful ones now live in the job list; keep only failures for a retry.
    setItems((prev) => prev.filter((it) => it.status === "error"));
  }

  const queued = items.filter((it) => it.status === "pending" || it.status === "error").length;

  return (
    <div className="card upload">
      <h2>Новая загрузка</h2>

      <div className="field-label">Режим обработки</div>
      <div className="modes">
        {MODES.map((m) => (
          <button
            key={m}
            type="button"
            className={`mode-chip ${mode === m ? "active" : ""}`}
            onClick={() => setMode(m)}
            disabled={busy}
          >
            <span className="mode-icon">{MODE_ICONS[m]}</span>
            <span className="mode-text">
              <strong>{MODE_LABELS[m]}</strong>
              <span className="muted small">{MODE_HINTS[m]}</span>
            </span>
          </button>
        ))}
      </div>

      <div className="field-label">Файлы</div>
      <div
        className={`dropzone ${dragging ? "dragging" : ""} ${busy ? "disabled" : ""}`}
        onClick={() => !busy && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !busy) inputRef.current?.click();
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept="audio/*,video/*"
          multiple
          hidden
          disabled={busy}
          onChange={(e) => add(e.target.files)}
        />
        <div className="dropzone-empty">
          <div className="dropzone-icon">⬆️</div>
          <div className="dropzone-title">
            Перетащите файлы сюда или <span className="link-like">выберите</span>
          </div>
          <div className="muted small">
            Аудио или видео · до 2 ГБ каждый · можно несколько сразу
          </div>
        </div>
      </div>

      {items.length > 0 && (
        <div className="upload-list">
          {items.map((it) => (
            <div className={`upload-item ${it.status}`} key={it.id}>
              <div className="file-icon">🎧</div>
              <div className="file-meta">
                <div className="file-name">{it.file.name}</div>
                <div className="muted small">
                  {fmtSize(it.file.size)} · {STATUS_TEXT[it.status]}
                </div>
                {it.status === "uploading" && (
                  <div className="progress mini">
                    <div className="progress-bar" style={{ width: `${it.pct}%` }} />
                    <span className="progress-label">{it.pct}%</span>
                  </div>
                )}
                {it.status === "error" && it.error && (
                  <div className="small item-error">{it.error}</div>
                )}
              </div>
              {!busy && (
                <button
                  type="button"
                  className="file-remove"
                  onClick={() => remove(it.id)}
                  aria-label="Убрать файл"
                >
                  ✕
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <button className="btn primary btn-block" onClick={start} disabled={queued === 0 || busy}>
        {busy
          ? "Загрузка…"
          : queued > 1
            ? `Загрузить и обработать (${queued})`
            : "Загрузить и обработать"}
      </button>
    </div>
  );
}
