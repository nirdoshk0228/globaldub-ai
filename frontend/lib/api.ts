export const API = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export type Severity = "low" | "medium" | "high";
export type LineStatus = "pending" | "auto_ok" | "flagged" | "approved" | "edited" | "kept";

export interface Flag {
  check: string;
  severity: Severity;
  message: string;
  suggestion: string;
  source: "rule" | "llm" | "audio";
}

export interface TrackSummary {
  lang: string;
  status: "queued" | "running" | "ready" | "failed";
  stage: string;
  progress: number;
  error: string | null;
  lines: number;
  auto_ok: number;
  flagged: number;
  approved: number;
  kept_original: number;
  needs_review_pct: number;
  flags_by_check: Record<string, number>;
  stretched_lines: number;
  cost_usd: number;
  cost_per_minute_usd: number;
  processing_seconds: number;
  dub_url?: string | null;
  srt_url?: string | null;
}

export interface Job {
  id: string;
  title: string;
  status: "queued" | "processing" | "ready" | "partial" | "failed";
  error: string | null;
  is_demo: boolean;
  created_at: string | null;
  source_lang: string;
  target_langs: string[];
  runtime_seconds: number;
  lines: number;
  synopsis: string;
  source_audio_url: string | null;
  background: string;
  bed_url: string | null;
  cost_usd: number;
  tracks: TrackSummary[];
}

export interface TrackLine {
  seg_id: string;
  start: number;
  end: number;
  speaker: string;
  source_text: string;
  source_emotion: string;
  is_song: boolean;
  audio_hint: string;
  source_clip_url: string | null;
  target_text: string;
  back_translation: string;
  adaptation_note: string;
  delivery: { emotion: string; intensity: number; pace: string };
  flags: Flag[];
  status: LineStatus;
  attempts: number;
  version: number;
  tts_version: number;
  rendering: boolean;
  tts_url: string | null;
  tts_duration: number | null;
  stretch: number;
}

export interface TrackDetail {
  job_id: string;
  title: string;
  lang: string;
  language: string;
  direction: "ltr" | "rtl";
  summary: TrackSummary;
  dub_url: string | null;
  srt_url: string | null;
  source_audio_url: string | null;
  characters: { name: string; name_in_target: string; gender: string; age: string; persona: string; voice: string | null }[];
  lines: TrackLine[];
}

export interface Language {
  code: string;
  name: string;
  native_name: string;
  market: "india" | "international";
  script: string;
  direction: "ltr" | "rtl";
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Storage URLs may be relative (local dev) or absolute signed URLs (R2). */
export function fileUrl(u: string | null | undefined): string | undefined {
  if (!u) return undefined;
  return u.startsWith("http") ? u : `${API}${u}`;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(0, "Can't reach the GlobalDub server. If it was idle it may be waking up - try again in a minute.");
  }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

// ---- jobs created in this browser (no accounts in the demo; the edit token proves ownership) ----
const KEY = "gd:jobs";

export function myJobs(): string[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

export function rememberJob(id: string, token: string) {
  try {
    const ids = [id, ...myJobs().filter((x) => x !== id)].slice(0, 30);
    localStorage.setItem(KEY, JSON.stringify(ids));
    localStorage.setItem(`gd:token:${id}`, token);
  } catch {}
}

export function editToken(id: string): string | null {
  try {
    return localStorage.getItem(`gd:token:${id}`);
  } catch {
    return null;
  }
}

export const NATIVE: Record<string, string> = {
  "hi-IN": "हिन्दी",
  "en-US": "English",
  "ta-IN": "தமிழ்",
  "te-IN": "తెలుగు",
  "bn-IN": "বাংলা",
  "mr-IN": "मराठी",
  "ar-SA": "العربية",
  "pt-BR": "Português",
  "es-MX": "Español",
};

export const LANG_NAME: Record<string, string> = {
  "hi-IN": "Hindi",
  "en-US": "US English",
  "ta-IN": "Tamil",
  "te-IN": "Telugu",
  "bn-IN": "Bengali",
  "mr-IN": "Marathi",
  "ar-SA": "Arabic",
  "pt-BR": "Brazilian Portuguese",
  "es-MX": "Mexican Spanish",
};

export const CHECK_LABEL: Record<string, string> = {
  omission: "Missing dialogue",
  name_mismatch: "Wrong name",
  number_mismatch: "Number changed",
  untranslated: "Untranslated text",
  script_mismatch: "Wrong script",
  literal_idiom: "Literal idiom",
  duration_overrun: "Too long for the scene",
  glossary: "Glossary term",
  meaning_drift: "Meaning changed",
  tone_mismatch: "Tone mismatch",
  register: "Formality / gender",
  cultural: "Cultural issue",
  grammar: "Grammar",
  back_translation: "Back-translation mismatch",
  render_error: "Voice render failed",
};

export const BACKGROUND_TEXT: Record<string, string> = {
  none: "Dialogue only - no background track",
  uploaded: "Mixed over the uploaded background track",
  "separated:demucs": "Background kept from the original (separated with Demucs)",
  "separated:center": "Background kept from the original (centre-channel separation)",
};

export function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${r.toString().padStart(2, "0")}`;
}

export function money(x: number): string {
  if (x === 0) return "$0";
  if (x < 0.01) return `$${x.toFixed(4)}`;
  return `$${x.toFixed(2)}`;
}
