export type JobMode = "transcription" | "report" | "ai_extract";
export type JobStatus =
  | "uploading"
  | "queued"
  | "waiting"
  | "processing"
  | "completed"
  | "failed";

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  is_admin: boolean;
  created_at: string;
}

export interface Segment {
  id: number | null;
  speaker: string | null;
  start_ms: number | null;
  end_ms: number | null;
  text: string | null;
}

export interface JobListItem {
  id: string;
  original_filename: string;
  file_size: number;
  mode: JobMode;
  status: JobStatus;
  progress: number;
  stage_message: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface JobDetail extends JobListItem {
  result_text: string | null;
  result_segments: Segment[] | null;
  report_html: string | null;
  diarization_note: string | null;
  has_report_pdf: boolean;
  has_transcript_pdf: boolean;
}

export const MODE_LABELS: Record<JobMode, string> = {
  transcription: "Транскрипция",
  report: "Деловой отчёт",
  ai_extract: "AI-выжимка",
};

export const STATUS_LABELS: Record<JobStatus, string> = {
  uploading: "Загрузка",
  queued: "В очереди",
  waiting: "Ожидает сервис",
  processing: "Обработка",
  completed: "Готово",
  failed: "Ошибка",
};
