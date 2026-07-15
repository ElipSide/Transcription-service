import type { Segment } from "./types";

export interface SpeakerColor {
  accent: string; // strong color: avatar bg, borders
  bubble: string; // soft bubble/background fill
  name: string; // speaker name text color
}

/** Distinct, readable palette. Cycles if there are more speakers than colors. */
const PALETTE: SpeakerColor[] = [
  { accent: "#2563eb", bubble: "#e8f0ff", name: "#1d4ed8" }, // blue
  { accent: "#0d9488", bubble: "#d8f5ef", name: "#0f766e" }, // teal
  { accent: "#c026d3", bubble: "#f8e2fc", name: "#a21caf" }, // magenta
  { accent: "#ea580c", bubble: "#ffe6d6", name: "#c2410c" }, // orange
  { accent: "#4f46e5", bubble: "#e6e5fb", name: "#4338ca" }, // indigo
  { accent: "#0891b2", bubble: "#d5f2f9", name: "#0e7490" }, // cyan
  { accent: "#65a30d", bubble: "#e8f5cf", name: "#4d7c0f" }, // lime
  { accent: "#e11d48", bubble: "#ffe0e6", name: "#be123c" }, // rose
];

/** "SPEAKER_00" / "SPEAKER 1" -> "Спикер 1"; other labels kept as-is. */
export function prettySpeaker(raw: string | null | undefined): string {
  if (!raw) return "Спикер";
  const m = /^speaker[_\s-]*(\d+)$/i.exec(raw.trim());
  if (m) return `Спикер ${parseInt(m[1], 10) + 1}`;
  return raw.trim();
}

export function initials(name: string): string {
  const m = /(\d+)\s*$/.exec(name);
  if (m) return m[1]; // "Спикер 1" -> "1"
  const parts = name.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

export interface SpeakerGroup {
  speaker: string; // raw label
  name: string; // pretty name
  color: SpeakerColor;
  order: number; // 0-based appearance order
  start_ms: number | null;
  end_ms: number | null;
  texts: string[];
}

export interface GroupedTranscript {
  groups: SpeakerGroup[];
  /** raw label -> color/order, in appearance order */
  speakers: Map<string, { color: SpeakerColor; order: number; name: string }>;
}

/** Collapse consecutive same-speaker segments into groups and assign colors. */
export function groupSegments(segments: Segment[]): GroupedTranscript {
  const speakers = new Map<
    string,
    { color: SpeakerColor; order: number; name: string }
  >();
  const groups: SpeakerGroup[] = [];

  for (const s of segments) {
    const raw = s.speaker ?? "Speaker";
    if (!speakers.has(raw)) {
      const order = speakers.size;
      speakers.set(raw, {
        color: PALETTE[order % PALETTE.length],
        order,
        name: prettySpeaker(raw),
      });
    }
    const info = speakers.get(raw)!;
    const text = (s.text ?? "").trim();
    const last = groups[groups.length - 1];
    if (last && last.speaker === raw) {
      if (text) last.texts.push(text);
      last.end_ms = s.end_ms ?? last.end_ms;
    } else {
      groups.push({
        speaker: raw,
        name: info.name,
        color: info.color,
        order: info.order,
        start_ms: s.start_ms,
        end_ms: s.end_ms,
        texts: text ? [text] : [],
      });
    }
  }

  return { groups, speakers };
}
