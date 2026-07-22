import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, downloadPdf, openJobSocket, resultFileName } from "../api";
import type { JobDetail, JobMode, Segment } from "../types";
import { MODE_LABELS, STATUS_LABELS } from "../types";
import { Markdown } from "../markdown";
import { groupSegments, initials, type SpeakerColor } from "../speakers";

function fmtTime(ms: number | null): string {
  if (ms === null || ms === undefined) return "";
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

/** Strip ```html … ``` fences the model sometimes wraps its HTML/output in. */
function stripCodeFences(s: string): string {
  const t = s.trim();
  const m = /^```[a-zA-Z]*\s*\n([\s\S]*?)\n?```$/.exec(t);
  return m ? m[1].trim() : t;
}

const ACTIVE = new Set(["queued", "waiting", "processing"]);

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

        {job.status === "waiting" ? (
          <div className="notice info">
            ⏸️ {job.stage_message ??
              "Сервис распознавания недоступен — задача ждёт в очереди и начнётся автоматически."}
          </div>
        ) : (
          active && (
            <div className="progress">
              <div className="progress-bar" style={{ width: `${job.progress}%` }} />
              <span className="progress-label">
                {job.stage_message ?? "Обработка…"} · {job.progress}%
              </span>
            </div>
          )
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
                onClick={() =>
                  downloadPdf(job.id, "report", resultFileName(job, "report"))
                }
              >
                📄 {job.mode === "ai_extract" ? "AI-выжимка" : "Отчёт"} (PDF)
              </button>
            )}
            {job.has_transcript_pdf && (
              <button
                className={`btn btn-lg ${job.has_report_pdf ? "" : "primary"}`}
                onClick={() =>
                  downloadPdf(job.id, "transcript", resultFileName(job, "transcript"))
                }
              >
                📝 Транскрипция (PDF)
              </button>
            )}
          </div>
        </div>
      )}

      {job.status === "completed" && job.result_text && <RerunCard job={job} />}

      {job.status === "completed" && job.mode !== "transcription" && job.report_html && (
        <ReportCard mode={job.mode} html={job.report_html} />
      )}

      {job.status === "completed" && (
        <TranscriptCard
          segments={job.result_segments}
          text={job.result_text}
          note={job.diarization_note}
        />
      )}
    </div>
  );
}

/* ---------- Re-run in another mode ---------- */

const MODE_ICON: Record<JobMode, string> = {
  transcription: "📝",
  report: "📄",
  ai_extract: "✨",
};

function RerunCard({ job }: { job: JobDetail }) {
  const [busy, setBusy] = useState<JobMode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const nav = useNavigate();

  const others = (Object.keys(MODE_LABELS) as JobMode[]).filter(
    (m) => m !== job.mode
  );

  const run = useCallback(
    async (mode: JobMode) => {
      setBusy(mode);
      setError(null);
      try {
        const { job_id } = await api.rerunJob(job.id, mode);
        nav(`/jobs/${job_id}`);
      } catch (e: any) {
        setError(e.message ?? "Не удалось запустить обработку");
      } finally {
        setBusy(null);
      }
    },
    [job.id, nav]
  );

  return (
    <div className="card">
      <h2>Обработать в другом режиме</h2>
      <div className="muted small rerun-hint">
        Файл уже распознан — транскрипция переиспользуется, распознавание заново
        не запускается. Результат появится как новая задача.
      </div>
      {error && <div className="error">{error}</div>}
      <div className="downloads">
        {others.map((m) => (
          <button
            key={m}
            className="btn btn-lg"
            disabled={busy !== null}
            onClick={() => run(m)}
          >
            {busy === m ? "Запуск…" : `${MODE_ICON[m]} ${MODE_LABELS[m]}`}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ---------- Report / AI-выжимка ---------- */

function ReportCard({ mode, html }: { mode: JobDetail["mode"]; html: string }) {
  const clean = useMemo(() => stripCodeFences(html), [html]);
  const isExtract = mode === "ai_extract";

  return (
    <div className="card">
      <h2>{isExtract ? "AI-выжимка" : "Деловой отчёт"}</h2>
      {isExtract ? (
        <Markdown text={clean} className="extract-md" />
      ) : (
        <ReportFrame html={clean} />
      )}
    </div>
  );
}

/** Render the model's full HTML document in an isolated iframe so its
 *  page-level styles don't leak into the app, and auto-fit the height. */
function ReportFrame({ html }: { html: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(400);

  const resize = useCallback(() => {
    const doc = ref.current?.contentDocument;
    if (doc?.body) {
      setHeight(doc.documentElement.scrollHeight || doc.body.scrollHeight);
    }
  }, []);

  return (
    <iframe
      ref={ref}
      className="report-frame"
      title="Отчёт"
      srcDoc={html}
      sandbox="allow-same-origin"
      style={{ height }}
      onLoad={resize}
    />
  );
}

/* ---------- Transcript ---------- */

function TranscriptCard({
  segments,
  text,
  note,
}: {
  segments: Segment[] | null;
  text: string | null;
  note: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const hasSegments = !!segments && segments.length > 0;

  const grouped = useMemo(
    () => (hasSegments ? groupSegments(segments!) : null),
    [hasSegments, segments]
  );

  const copyText = useMemo(() => {
    if (grouped) {
      return grouped.groups
        .map((g) => `${g.name}: ${g.texts.join(" ")}`)
        .join("\n\n");
    }
    return text ?? "";
  }, [grouped, text]);

  const copy = useCallback(() => {
    navigator.clipboard?.writeText(copyText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [copyText]);

  const speakerCount = grouped?.speakers.size ?? 0;
  const isDialogue = speakerCount === 2;
  // Multiple turns collapsed into one speaker usually means diarization was
  // disabled or produced nothing — surface a gentle hint (the hard error, if
  // any, comes from the backend via `note`).
  const singleSpeaker =
    !note && speakerCount === 1 && (grouped?.groups.length ?? 0) > 1;

  return (
    <div className="card">
      <div className="transcript-head">
        <h2>Транскрипция</h2>
        <button className="btn small copy-btn" onClick={copy}>
          {copied ? "✓ Скопировано" : "📋 Копировать"}
        </button>
      </div>

      {note && <div className="notice warn">⚠️ {note}</div>}
      {singleSpeaker && (
        <div className="notice info">
          ℹ️ Распознавание отнесло всю запись к одному говорящему. Если в записи
          несколько голосов — проверьте, включено ли разделение по спикерам
          (диаризация) на сервере распознавания.
        </div>
      )}

      {grouped && speakerCount > 0 && (
        <div className="speaker-legend">
          {[...grouped.speakers.values()]
            .sort((a, b) => a.order - b.order)
            .map((sp) => (
              <span className="legend-chip" key={sp.order}>
                <span
                  className="legend-dot"
                  style={{ background: sp.color.accent }}
                />
                {sp.name}
              </span>
            ))}
        </div>
      )}

      {grouped ? (
        <div className={isDialogue ? "dialogue" : "segments"}>
          {grouped.groups.map((g, i) => {
            const side = isDialogue && g.order === 1 ? "right" : "left";
            return (
              <SpeakerBubble
                key={i}
                name={g.name}
                color={g.color}
                start={g.start_ms}
                end={g.end_ms}
                texts={g.texts}
                dialogue={isDialogue}
                side={side}
              />
            );
          })}
        </div>
      ) : (
        <pre className="transcript">{text}</pre>
      )}
    </div>
  );
}

function SpeakerBubble({
  name,
  color,
  start,
  end,
  texts,
  dialogue,
  side,
}: {
  name: string;
  color: SpeakerColor;
  start: number | null;
  end: number | null;
  texts: string[];
  dialogue: boolean;
  side: "left" | "right";
}) {
  const avatar = (
    <div className="sp-avatar" style={{ background: color.accent }} title={name}>
      {initials(name)}
    </div>
  );

  const body = (
    <div
      className="sp-body"
      style={
        dialogue
          ? { background: color.bubble }
          : { borderLeftColor: color.accent }
      }
    >
      <div className="sp-head">
        <span className="speaker" style={{ color: color.name }}>
          {name}
        </span>
        <span className="muted small ts">
          {fmtTime(start)}
          {end !== null && end !== start ? `–${fmtTime(end)}` : ""}
        </span>
      </div>
      {texts.map((t, i) => (
        <p className="sp-text" key={i}>
          {t}
        </p>
      ))}
    </div>
  );

  if (dialogue) {
    return (
      <div className={`bubble-row ${side}`}>
        {side === "left" && avatar}
        {body}
        {side === "right" && avatar}
      </div>
    );
  }

  return (
    <div className="segment-row">
      {avatar}
      {body}
    </div>
  );
}
