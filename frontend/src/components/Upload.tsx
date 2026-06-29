import { useRef, useState } from "react";
import { uploadFile } from "../api";
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

function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 ** 3).toFixed(2)} ГБ`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 ** 2).toFixed(1)} МБ`;
  return `${(bytes / 1024).toFixed(0)} КБ`;
}

export default function Upload({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<JobMode>("transcription");
  const [file, setFile] = useState<File | null>(null);
  const [pct, setPct] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function pickFile(f: File | null) {
    setError(null);
    setFile(f);
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (busy) return;
    const f = e.dataTransfer.files?.[0];
    if (f) pickFile(f);
  }

  async function start() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setPct(0);
    try {
      await uploadFile(file, mode, setPct);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      onDone();
    } catch (err: any) {
      setError(err.message ?? "Ошибка загрузки");
    } finally {
      setBusy(false);
    }
  }

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

      <div className="field-label">Файл</div>
      <div
        className={`dropzone ${dragging ? "dragging" : ""} ${file ? "has-file" : ""} ${
          busy ? "disabled" : ""
        }`}
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
          hidden
          disabled={busy}
          onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
        />

        {!file ? (
          <div className="dropzone-empty">
            <div className="dropzone-icon">⬆️</div>
            <div className="dropzone-title">
              Перетащите файл сюда или <span className="link-like">выберите</span>
            </div>
            <div className="muted small">Аудио или видео · до 2 ГБ</div>
          </div>
        ) : (
          <div className="dropzone-file">
            <div className="file-icon">🎧</div>
            <div className="file-meta">
              <div className="file-name">{file.name}</div>
              <div className="muted small">{fmtSize(file.size)}</div>
            </div>
            {!busy && (
              <button
                type="button"
                className="file-remove"
                onClick={(e) => {
                  e.stopPropagation();
                  pickFile(null);
                  if (inputRef.current) inputRef.current.value = "";
                }}
                aria-label="Убрать файл"
              >
                ✕
              </button>
            )}
          </div>
        )}
      </div>

      {busy && (
        <div className="progress">
          <div className="progress-bar" style={{ width: `${pct}%` }} />
          <span className="progress-label">Загрузка {pct}%</span>
        </div>
      )}

      {error && <div className="error">{error}</div>}

      <button className="btn primary btn-block" onClick={start} disabled={!file || busy}>
        {busy ? "Загрузка…" : "Загрузить и обработать"}
      </button>
    </div>
  );
}
