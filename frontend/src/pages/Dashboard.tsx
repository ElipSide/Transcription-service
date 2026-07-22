import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, downloadArchive, type ArchiveKind } from "../api";
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

/** Jobs uploaded together (same batch_id) shown as one downloadable group. */
interface JobGroup {
  key: string;
  jobs: JobListItem[];
}

export default function Dashboard() {
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [archiveKind, setArchiveKind] = useState<ArchiveKind>("all");
  const [archiving, setArchiving] = useState<string | null>(null);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const nav = useNavigate();

  const completedIds = useMemo(
    () => jobs.filter((j) => j.status === "completed").map((j) => j.id),
    [jobs]
  );
  // Selection must not outlive the jobs it points at (deleted / still running).
  const picked = useMemo(
    () => completedIds.filter((id) => selected.has(id)),
    [completedIds, selected]
  );

  // Group by batch_id (keyed by map, so an interleaved list still groups right).
  const groups = useMemo(() => {
    const out: JobGroup[] = [];
    const byBatch = new Map<string, JobGroup>();
    for (const j of jobs) {
      if (j.batch_id) {
        let g = byBatch.get(j.batch_id);
        if (!g) {
          g = { key: j.batch_id, jobs: [] };
          byBatch.set(j.batch_id, g);
          out.push(g);
        }
        g.jobs.push(j);
      } else {
        out.push({ key: j.id, jobs: [j] });
      }
    }
    return out;
  }, [jobs]);

  const toggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }, []);

  const saveArchive = useCallback(
    async (key: string, ids: string[], kind: ArchiveKind) => {
      if (ids.length === 0) return;
      setArchiving(key);
      setArchiveError(null);
      try {
        await downloadArchive(ids, kind);
      } catch (e: any) {
        setArchiveError(e.message ?? "Не удалось скачать архив");
      } finally {
        setArchiving(null);
      }
    },
    []
  );

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

  function row(j: JobListItem) {
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
        {done && (
          <input
            type="checkbox"
            className="row-check"
            checked={selected.has(j.id)}
            onClick={(e) => e.stopPropagation()}
            onChange={() => toggle(j.id)}
            aria-label={`Выбрать «${j.original_filename}» для архива`}
          />
        )}
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
          <button className="icon-btn" title="Удалить" onClick={(e) => remove(e, j.id)}>
            🗑
          </button>
        </div>
      </li>
    );
  }

  return (
    <div className="dashboard">
      <Upload onDone={load} />

      <div className="card">
        <div className="jobs-head">
          <h2>Мои распознавания</h2>
          {completedIds.length > 0 && (
            <button
              className="btn small"
              onClick={() =>
                setSelected(
                  picked.length === completedIds.length ? new Set() : new Set(completedIds)
                )
              }
            >
              {picked.length === completedIds.length ? "Снять выделение" : "Выбрать все готовые"}
            </button>
          )}
        </div>

        {picked.length > 0 && (
          <div className="batch-bar">
            <span className="small">
              Выбрано: <strong>{picked.length}</strong>
            </span>
            <select
              value={archiveKind}
              onChange={(e) => setArchiveKind(e.target.value as ArchiveKind)}
              disabled={archiving !== null}
            >
              <option value="all">Все файлы</option>
              <option value="transcript">Транскрибации</option>
              <option value="report">Отчёты / выдержки</option>
            </select>
            <button
              className="btn primary small"
              onClick={() => saveArchive("selection", picked, archiveKind)}
              disabled={archiving !== null}
            >
              {archiving === "selection" ? "Готовим архив…" : "⬇️ Скачать архивом"}
            </button>
            <button className="btn small" onClick={() => setSelected(new Set())}>
              Снять
            </button>
          </div>
        )}
        {archiveError && <div className="error">{archiveError}</div>}

        {loading ? (
          <div className="muted">Загрузка…</div>
        ) : jobs.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">🗂</div>
            <div>Пока нет загрузок. Загрузите первый файл выше.</div>
          </div>
        ) : (
          <ul className="job-list">
            {groups.map((g) => {
              if (g.jobs.length === 1) return row(g.jobs[0]);
              const ready = g.jobs.filter((j) => j.status === "completed");
              return (
                <li className="job-group" key={g.key}>
                  <div className="group-head">
                    <div className="group-title">
                      📦 Загружено группой · {g.jobs.length} файлов
                      <span className="muted small group-date">
                        {fmtDate(g.jobs[0].created_at)}
                      </span>
                    </div>
                    <span className="muted small">
                      готово {ready.length} из {g.jobs.length}
                    </span>
                    <button
                      className="btn primary small"
                      disabled={ready.length === 0 || archiving !== null}
                      onClick={() =>
                        saveArchive(g.key, ready.map((j) => j.id), "all")
                      }
                      title="Скачать готовые документы этой группы"
                    >
                      {archiving === g.key ? "Готовим архив…" : "⬇️ Скачать группу"}
                    </button>
                  </div>
                  <ul className="job-list nested">{g.jobs.map(row)}</ul>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
