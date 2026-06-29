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

export const tokens = {
  get access() {
    return localStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  set(pair: TokenPair) {
    localStorage.setItem(ACCESS_KEY, pair.access_token);
    localStorage.setItem(REFRESH_KEY, pair.refresh_token);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
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
  const res = await fetch(`${ROOT}/api/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: r }),
  });
  if (!res.ok) return false;
  tokens.set(await res.json());
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
  me: () => request<User>("/api/auth/me"),

  listJobs: () => request<JobListItem[]>("/api/jobs"),
  getJob: (id: string) => request<JobDetail>(`/api/jobs/${id}`),
  deleteJob: (id: string) => request<void>(`/api/jobs/${id}`, { method: "DELETE" }),
};

// ---------- Chunked upload ----------
export interface UploadInitOut {
  upload_id: string;
  chunk_size: number;
}

export async function uploadFile(
  file: File,
  mode: JobMode,
  onProgress: (pct: number) => void
): Promise<string> {
  const init = await request<UploadInitOut>("/api/jobs/upload/init", {
    method: "POST",
    body: { filename: file.name, file_size: file.size, mode },
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
export async function downloadPdf(jobId: string, kind: "transcript" | "report") {
  const res = await fetch(`${ROOT}/api/jobs/${jobId}/download/${kind}`, {
    headers: tokens.access ? { Authorization: `Bearer ${tokens.access}` } : {},
  });
  if (!res.ok) throw new ApiError(res.status, await parseError(res));
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${kind}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
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
