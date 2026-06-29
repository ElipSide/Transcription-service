import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, downloadPdf, openJobSocket } from "../api";
import type { JobDetail } from "../types";
import { MODE_LABELS, STATUS_LABELS } from "../types";

function fmtTime(ms: number | null): string {
  if (ms === null || ms === undefined) return "";
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

const ACTIVE = new Set(["queued", "processing"]);

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      setJob(await api.getJob(id));
    } catch (err: any) {
      setError(err.message ?? "Не удалось загрузить задачу");
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  // Live progress via WebSocket while the job is active
  useEffect(() => {
    if (!id || !job) return;
    if (!ACTIVE.has(job.status)) return;
    if (wsRef.current) return;

    const ws = openJobSocket(id, (data) => {
      setJob((prev) =>
        prev ? { ...prev, status: data.status, progress: data.progress, stage_message: data.stage_message, error: data.error } : prev
      );
      if (data.status === "completed" || data.status === "failed") {
        ws.close();
        wsRef.current = null;
        load(); // refetch full result
      }
    });
    wsRef.current = ws;
    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [id, job?.status, load]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <div className="card error">{error}</div>;
  if (!job) return <div className="muted">Загрузка…</div>;

  const active = ACTIVE.has(job.status);

  return (
    <div className="job-detail">
      <Link to="/" className="link">
        ← К списку
      </Link>

      <div className="card">
        <h1 className="filename">{job.original_filename}</h1>
        <div className="meta">
          <span className="badge">{MODE_LABELS[job.mode]}</span>
          <span className={`badge ${job.status}`}>{STATUS_LABELS[job.status]}</span>
        </div>

        {active && (
          <div className="progress">
            <div className="progress-bar" style={{ width: `${job.progress}%` }} />
            <span className="progress-label">
              {job.stage_message ?? "Обработка…"} · {job.progress}%
            </span>
          </div>
        )}

        {job.status === "failed" && <div className="error">{job.error}</div>}
      </div>

      {job.status === "completed" && (job.has_transcript_pdf || job.has_report_pdf) && (
        <div className="card downloads-card">
          <div className="downloads-head">
            <span className="dl-icon">⬇️</span>
            <div>
              <div className="downloads-title">Скачать результат</div>
              <div className="muted small">Готовые PDF-файлы</div>
            </div>
          </div>
          <div className="downloads">
            {job.has_report_pdf && (
              <button
                className="btn primary btn-lg"
                onClick={() => downloadPdf(job.id, "report")}
              >
                📄 {job.mode === "ai_extract" ? "AI-выжимка" : "Отчёт"} (PDF)
              </button>
            )}
            {job.has_transcript_pdf && (
              <button
                className={`btn btn-lg ${job.has_report_pdf ? "" : "primary"}`}
                onClick={() => downloadPdf(job.id, "transcript")}
              >
                📝 Транскрипция (PDF)
              </button>
            )}
          </div>
        </div>
      )}

      {job.status === "completed" && job.mode !== "transcription" && job.report_html && (
        <div className="card">
          <h2>{job.mode === "ai_extract" ? "AI-выжимка" : "Деловой отчёт"}</h2>
          {job.mode === "report" ? (
            <div
              className="report-html"
              dangerouslySetInnerHTML={{ __html: job.report_html }}
            />
          ) : (
            <pre className="extract">{job.report_html}</pre>
          )}
        </div>
      )}

      {job.status === "completed" && (
        <div className="card">
          <h2>Транскрипция</h2>
          {job.result_segments && job.result_segments.length > 0 ? (
            <div className="segments">
              {job.result_segments.map((s, i) => (
                <div className="segment" key={s.id ?? i}>
                  <div className="seg-head">
                    <span className="speaker">{s.speaker ?? "Speaker"}</span>
                    <span className="muted small ts">
                      {fmtTime(s.start_ms)}–{fmtTime(s.end_ms)}
                    </span>
                  </div>
                  <div className="seg-text">{s.text}</div>
                </div>
              ))}
            </div>
          ) : (
            <pre className="transcript">{job.result_text}</pre>
          )}
        </div>
      )}
    </div>
  );
}
