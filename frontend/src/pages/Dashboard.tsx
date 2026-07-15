import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import Upload from "../components/Upload";
import type { JobListItem } from "../types";
import { MODE_LABELS, STATUS_LABELS } from "../types";

function fmtDate(s: string) {
  return new Date(s).toLocaleString("ru-RU");
}

const ACTIVE = new Set(["queued", "waiting", "processing"]);
const STATUS_ICON: Record<string, string> = {
  completed: "✅",
  failed: "⚠️",
  processing: "⏳",
  queued: "🕘",
  waiting: "⏸️",
  uploading: "⬆️",
};

export default function Dashboard() {
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const timer = useRef<number | null>(null);
  const nav = useNavigate();

  const load = useCallback(async () => {
    try {
      setJobs(await api.listJobs());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while any job is still being processed
  useEffect(() => {
    const anyActive = jobs.some((j) => ACTIVE.has(j.status));
    if (anyActive && timer.current === null) {
      timer.current = window.setInterval(load, 3000);
    } else if (!anyActive && timer.current !== null) {
      clearInterval(timer.current);
      timer.current = null;
    }
    return () => {
      if (timer.current !== null) {
        clearInterval(timer.current);
        timer.current = null;
      }
    };
  }, [jobs, load]);

  async function remove(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    if (!confirm("Удалить эту задачу и её результаты?")) return;
    await api.deleteJob(id);
    load();
  }

  return (
    <div className="dashboard">
      <Upload onDone={load} />

      <div className="card">
        <h2>Мои распознавания</h2>
        {loading ? (
          <div className="muted">Загрузка…</div>
        ) : jobs.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">🗂</div>
            <div>Пока нет загрузок. Загрузите первый файл выше.</div>
          </div>
        ) : (
          <ul className="job-list">
            {jobs.map((j) => {
              const active = ACTIVE.has(j.status);
              const done = j.status === "completed";
              return (
                <li
                  key={j.id}
                  className={`job-row ${j.status}`}
                  onClick={() => nav(`/jobs/${j.id}`)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && nav(`/jobs/${j.id}`)}
                >
                  <div className="job-status-icon">{STATUS_ICON[j.status] ?? "•"}</div>

                  <div className="job-main">
                    <div className="job-name">{j.original_filename}</div>
                    <div className="job-sub muted small">
                      {MODE_LABELS[j.mode]} · {fmtDate(j.created_at)}
                    </div>
                    {active && (
                      <div className="progress mini">
                        <div className="progress-bar" style={{ width: `${j.progress}%` }} />
                        <span className="progress-label">
                          {j.stage_message ?? STATUS_LABELS[j.status]} · {j.progress}%
                        </span>
                      </div>
                    )}
                  </div>

                  <div className="job-actions">
                    <span className={`badge ${j.status}`}>{STATUS_LABELS[j.status]}</span>
                    {done ? (
                      <span className="btn primary small open-btn">Открыть →</span>
                    ) : (
                      <span className="btn ghost small open-btn">Открыть</span>
                    )}
                    <button
                      className="icon-btn"
                      title="Удалить"
                      onClick={(e) => remove(e, j.id)}
                    >
                      🗑
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
