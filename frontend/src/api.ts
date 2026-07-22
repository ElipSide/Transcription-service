import type { JobDetail, JobListItem, JobMode, User } from "./types";

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

// Base path the app is served under ("" at root, "/transcribe" behind a prefix).
// Derived from Vite's BASE_URL so a single build arg controls everything.
const ROOT = import.meta.env.BASE_URL.replace(/\/$/, "");

const ACCESS_KEY = "ts_access";
const REFRESH_KEY = "ts_refresh";

/** Guest sessions live in sessionStorage so they die when the tab closes;
 *  real accounts persist in localStorage. */
export const tokens = {
  get access() {
    return sessionStorage.getItem(ACCESS_KEY) ?? localStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    return sessionStorage.getItem(REFRESH_KEY) ?? localStorage.getItem(REFRESH_KEY);
  },
  get isGuestSession() {
    return sessionStorage.getItem(ACCESS_KEY) !== null;
  },
  set(pair: TokenPair, guest = false) {
    this.clear();
    const store = guest ? sessionStorage : localStorage;
    store.setItem(ACCESS_KEY, pair.access_token);
    store.setItem(REFRESH_KEY, pair.refresh_token);
  },
  clear() {
    for (const s of [sessionStorage, localStorage]) {
      s.removeItem(ACCESS_KEY);
      s.removeItem(REFRESH_KEY);
    }
  },
};

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) return data.detail.map((d: any) => d.msg).join("; ");
  } catch {
    /* ignore */
  }
  return `Ошибка ${res.status}`;
}

async function refreshAccess(): Promise<boolean> {
  const r = tokens.refresh;
  if (!r) return false;
  const wasGuest = tokens.isGuestSession; // keep guests out of localStorage
  const res = await fetch(`${ROOT}/api/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: r }),
  });
  if (!res.ok) return false;
  tokens.set(await res.json(), wasGuest);
  return true;
}

interface RequestOptions {
  method?: string;
  body?: any;
  isForm?: boolean;
  retry?: boolean;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, isForm = false, retry = true } = opts;
  const headers: Record<string, string> = {};
  if (tokens.access) headers["Authorization"] = `Bearer ${tokens.access}`;
  if (!isForm && body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(`${ROOT}${path}`, {
    method,
    headers,
    body: isForm ? body : body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401 && retry && tokens.refresh) {
    if (await refreshAccess()) {
      return request<T>(path, { ...opts, retry: false });
    }
    tokens.clear();
  }
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ---------- Auth ----------
export const api = {
  register: (email: string, password: string, full_name?: string) =>
    request<TokenPair>("/api/auth/register", {
      method: "POST",
      body: { email, password, full_name },
    }),
  login: (email: string, password: string) =>
    request<TokenPair>("/api/auth/login", { method: "POST", body: { email, password } }),
  /** Anonymous session — no registration required. */
  guest: () => request<TokenPair>("/api/auth/guest", { method: "POST" }),
  me: () => request<User>("/api/auth/me"),

  listJobs: () => request<JobListItem[]>("/api/jobs"),
  getJob: (id: string) => request<JobDetail>(`/api/jobs/${id}`),
  deleteJob: (id: string) => request<void>(`/api/jobs/${id}`, { method: "DELETE" }),

  /** Re-process an existing transcript in another mode (no re-recognition). */
  rerunJob: (id: string, mode: JobMode) =>
    request<{ job_id: string }>(`/api/jobs/${id}/rerun`, {
      method: "POST",
      body: { mode },
    }),

  diarStatus: () => request<{ available: boolean }>("/api/diar/status"),
};

// ---------- Chunked upload ----------
export interface UploadInitOut {
  upload_id: string;
  chunk_size: number;
}

/** RFC4122 v4. crypto.randomUUID needs a secure context — the app may be served
 *  over plain http, so fall back rather than crash. */
export function uuid(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export async function uploadFile(
  file: File,
  mode: JobMode,
  onProgress: (pct: number) => void,
  batchId?: string
): Promise<string> {
  const init = await request<UploadInitOut>("/api/jobs/upload/init", {
    method: "POST",
    body: { filename: file.name, file_size: file.size, mode, batch_id: batchId ?? null },
  });

  const chunkSize = init.chunk_size;
  const total = Math.ceil(file.size / chunkSize) || 1;
  for (let i = 0; i < total; i++) {
    const blob = file.slice(i * chunkSize, (i + 1) * chunkSize);
    const form = new FormData();
    form.append("chunk", blob, "chunk");
    await request(`/api/jobs/upload/${init.upload_id}/chunk?index=${i}`, {
      method: "POST",
      body: form,
      isForm: true,
    });
    onProgress(Math.round(((i + 1) / total) * 100));
  }

  const done = await request<{ job_id: string }>(
    `/api/jobs/upload/${init.upload_id}/complete`,
    { method: "POST" }
  );
  return done.job_id;
}

// ---------- Authenticated file download ----------
export type ArchiveKind = "all" | "transcript" | "report";

/** "Транскрибация - meeting.pdf" — mirrors the backend naming. */
export function resultFileName(
  job: { original_filename: string; mode: JobMode },
  kind: "transcript" | "report"
): string {
  const stem = job.original_filename.replace(/\.[^.]+$/, "") || "file";
  const prefix =
    kind === "transcript"
      ? "Транскрибация"
      : job.mode === "ai_extract"
        ? "Выдержка"
        : "Отчёт";
  return `${prefix} - ${stem}.pdf`;
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function downloadPdf(
  jobId: string,
  kind: "transcript" | "report",
  filename?: string
) {
  const res = await fetch(`${ROOT}/api/jobs/${jobId}/download/${kind}`, {
    headers: tokens.access ? { Authorization: `Bearer ${tokens.access}` } : {},
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  saveBlob(await res.blob(), filename ?? `${kind}.pdf`);
}

/** Results of several jobs in one ZIP. */
export async function downloadArchive(jobIds: string[], kind: ArchiveKind) {
  const res = await fetch(`${ROOT}/api/jobs/archive`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(tokens.access ? { Authorization: `Bearer ${tokens.access}` } : {}),
    },
    body: JSON.stringify({ job_ids: jobIds, kind }),
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  const label =
    kind === "transcript" ? "Транскрибации" : kind === "report" ? "Отчёты" : "Разбор";
  saveBlob(await res.blob(), `${label} (${jobIds.length}).zip`);
}

// ---------- Live progress WebSocket ----------
export function openJobSocket(jobId: string, onMessage: (data: any) => void): WebSocket {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}${ROOT}/api/jobs/${jobId}/ws?token=${tokens.access}`;
  const ws = new WebSocket(url);
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data));
    } catch {
      /* ignore */
    }
  };
  return ws;
}
