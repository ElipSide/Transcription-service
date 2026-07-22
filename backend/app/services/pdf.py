"""PDF generation. Primary engine is WeasyPrint (works headless on Linux).

PDF generation is best-effort: if it fails, the job still keeps the text/HTML
result so the user can read it in the web UI.

The transcript and AI-выжимка PDFs mirror the web rendering: per-speaker colors,
a dialogue layout for two speakers, and rendered Markdown for the summary.
"""
import html as _html
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("transcribe.pdf")


# ---- Shared with the frontend (speakers.ts) --------------------------------
_SPEAKER_PALETTE = [
    {"accent": "#2563eb", "bubble": "#e8f0ff", "name": "#1d4ed8"},  # blue
    {"accent": "#0d9488", "bubble": "#d8f5ef", "name": "#0f766e"},  # teal
    {"accent": "#c026d3", "bubble": "#f8e2fc", "name": "#a21caf"},  # magenta
    {"accent": "#ea580c", "bubble": "#ffe6d6", "name": "#c2410c"},  # orange
    {"accent": "#4f46e5", "bubble": "#e6e5fb", "name": "#4338ca"},  # indigo
    {"accent": "#0891b2", "bubble": "#d5f2f9", "name": "#0e7490"},  # cyan
    {"accent": "#65a30d", "bubble": "#e8f5cf", "name": "#4d7c0f"},  # lime
    {"accent": "#e11d48", "bubble": "#ffe0e6", "name": "#be123c"},  # rose
]


def _esc(s: str) -> str:
    return _html.escape(s or "")


def _pretty_speaker(raw: str | None) -> str:
    if not raw:
        return "Спикер"
    m = re.match(r"^speaker[_\s-]*(\d+)$", raw.strip(), re.IGNORECASE)
    if m:
        return f"Спикер {int(m.group(1)) + 1}"
    return raw.strip()


def _initials(name: str) -> str:
    m = re.search(r"(\d+)\s*$", name)
    if m:
        return m.group(1)
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper()


def _fmt_time(ms) -> str:
    if ms is None:
        return ""
    try:
        s = int(ms) // 1000
    except (TypeError, ValueError):
        return ""
    return f"{s // 60}:{s % 60:02d}"


# ---- Page shell ------------------------------------------------------------
def _page(title: str, body: str, extra_css: str = "") -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>{_esc(title)}</title>
<style>
  @page {{ margin: 1.6cm; }}
  body {{ font-family: "DejaVu Sans", Arial, sans-serif; color: #1f2733; line-height: 1.5; font-size: 11pt; }}
  .header {{ text-align: center; margin-bottom: 22px; border-bottom: 2px solid #e3e8f0; padding-bottom: 14px; }}
  .header h1 {{ margin: 0 0 4px; font-size: 18pt; }}
  .header .date {{ color: #7a869a; font-size: 9pt; }}
  {extra_css}
</style></head>
<body>
  <div class="header">
    <h1>{_esc(title)}</h1>
    <div class="date">Дата генерации: {datetime.now().strftime('%d.%m.%Y %H:%M')}</div>
  </div>
  {body}
</body></html>"""


# ---- Transcript (speaker colors / dialogue) --------------------------------
def _group_segments(segments: list[dict]):
    """Collapse consecutive same-speaker segments and assign stable colors."""
    order: dict[str, int] = {}
    groups: list[dict] = []
    for s in segments:
        raw = s.get("speaker") or "Speaker"
        if raw not in order:
            order[raw] = len(order)
        idx = order[raw]
        color = _SPEAKER_PALETTE[idx % len(_SPEAKER_PALETTE)]
        text = (s.get("text") or "").strip()
        if groups and groups[-1]["raw"] == raw:
            if text:
                groups[-1]["texts"].append(text)
            groups[-1]["end"] = s.get("end_ms")
        else:
            groups.append({
                "raw": raw, "order": idx, "color": color,
                "name": _pretty_speaker(raw),
                "start": s.get("start_ms"), "end": s.get("end_ms"),
                "texts": [text] if text else [],
            })
    return groups, order


def _avatar(color: dict, name: str) -> str:
    return (
        f'<span class="avatar" style="background:{color["accent"]}">'
        f'{_esc(_initials(name))}</span>'
    )


def _turn_head(g: dict) -> str:
    ts = _fmt_time(g["start"])
    if g["end"] is not None and g["end"] != g["start"]:
        ts += f'–{_fmt_time(g["end"])}'
    return (
        f'<div class="head">{_avatar(g["color"], g["name"])}'
        f'<span class="speaker" style="color:{g["color"]["name"]}">{_esc(g["name"])}</span>'
        f'<span class="ts">{_esc(ts)}</span></div>'
    )


def _texts_html(texts: list[str]) -> str:
    return "".join(f"<p>{_esc(t)}</p>" for t in texts) or "<p></p>"


def _transcript_body(segments: list[dict]) -> str:
    groups, order = _group_segments(segments)
    dialogue = len(order) == 2

    legend = "".join(
        f'<span class="chip"><span class="dot" style="background:'
        f'{_SPEAKER_PALETTE[i % len(_SPEAKER_PALETTE)]["accent"]}"></span>'
        f'{_esc(_pretty_speaker(raw))}</span>'
        for raw, i in order.items()
    )

    rows = []
    for g in groups:
        if dialogue:
            side = "right" if g["order"] == 1 else "left"
            rows.append(
                f'<div class="turn dlg {side}">'
                f'<div class="bubble" style="background:{g["color"]["bubble"]}">'
                f'{_turn_head(g)}{_texts_html(g["texts"])}</div></div>'
            )
        else:
            rows.append(
                f'<div class="turn list" style="border-left:3px solid {g["color"]["accent"]}">'
                f'{_turn_head(g)}{_texts_html(g["texts"])}</div>'
            )

    return f'<div class="legend">{legend}</div>' + "".join(rows)


_TRANSCRIPT_CSS = """
  .legend { margin-bottom: 16px; }
  .chip { display: inline-block; margin: 0 12px 4px 0; font-size: 9pt; font-weight: bold; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; vertical-align: middle; }
  .avatar { display: inline-block; width: 20px; height: 20px; border-radius: 50%;
            color: #fff; text-align: center; line-height: 20px; font-size: 9pt;
            font-weight: bold; margin-right: 6px; vertical-align: middle; }
  .head { margin-bottom: 3px; }
  .speaker { font-weight: bold; }
  .ts { float: right; color: #7a869a; font-size: 8.5pt; }
  .turn { margin-bottom: 9px; }
  .turn p { margin: 2px 0 0; }
  .turn.list { background: #f8fafc; border-radius: 0 6px 6px 0; padding: 6px 10px; }
  .turn.dlg { overflow: hidden; }
  .turn.dlg.left { padding-right: 20%; }
  .turn.dlg.right { padding-left: 20%; }
  .bubble { border-radius: 12px; padding: 7px 11px; }
"""


def segments_to_pdf(
    segments: list[dict], out_path: Path, title: str = "Транскрипция"
) -> Path | None:
    if not segments:
        return None
    body = _transcript_body(segments)
    return html_to_pdf(_page(title, body, _TRANSCRIPT_CSS), out_path)


# ---- Markdown (AI-выжимка) -------------------------------------------------
def _md_inline(text: str) -> str:
    out = _esc(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return out


def _markdown_to_html(md: str) -> str:
    blocks: list[str] = []
    items: list[str] = []
    list_tag: str | None = None

    def flush():
        nonlocal items, list_tag
        if items and list_tag:
            blocks.append(f"<{list_tag}>" + "".join(items) + f"</{list_tag}>")
        items = []
        list_tag = None

    for raw in (md or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        h = re.match(r"^(#{1,4})\s+(.*)$", line)
        if h:
            flush()
            level = min(len(h.group(1)) + 1, 5)
            blocks.append(f"<h{level}>{_md_inline(h.group(2))}</h{level}>")
            continue
        b = re.match(r"^[-*•·—]\s+(.*)$", line)
        if b:
            if list_tag != "ul":
                flush()
                list_tag = "ul"
            items.append(f"<li>{_md_inline(b.group(1))}</li>")
            continue
        n = re.match(r"^(\d+)[.)]\s+(.*)$", line)
        if n:
            if list_tag != "ol":
                flush()
                list_tag = "ol"
            items.append(f"<li>{_md_inline(n.group(2))}</li>")
            continue
        flush()
        blocks.append(f"<p>{_md_inline(line)}</p>")
    flush()
    return "".join(blocks)


_MARKDOWN_CSS = """
  .md { background: #f8fafc; border: 1px solid #e3e8f0; border-radius: 8px; padding: 6px 20px; }
  .md h2, .md h3, .md h4, .md h5 { color: #1e3a5f; margin: 14px 0 7px; }
  .md ul, .md ol { margin: 8px 0; padding-left: 22px; }
  .md li { margin: 5px 0; }
  .md p { margin: 8px 0; }
  .md code { background: #eef1f6; padding: 1px 4px; border-radius: 4px; }
"""


def markdown_to_pdf(md: str, out_path: Path, title: str = "AI-выжимка") -> Path | None:
    body = f'<div class="md">{_markdown_to_html(md)}</div>'
    return html_to_pdf(_page(title, body, _MARKDOWN_CSS), out_path)


# ---- Business report (model returns full HTML) -----------------------------
def _extract_html(ai_response: str) -> str:
    """Pull an HTML document out of a model response (```html block or <html>…)."""
    m = re.search(r"```html\s*(.*?)\s*```", ai_response, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"<html[\s\S]*</html>", ai_response, re.IGNORECASE)
    if m:
        return m.group(0).strip()
    return ai_response.strip()


def html_to_pdf(html_content: str, out_path: Path) -> Path | None:
    try:
        from weasyprint import HTML  # imported lazily; heavy dependency

        out_path.parent.mkdir(parents=True, exist_ok=True)
        HTML(string=_extract_html(html_content)).write_pdf(str(out_path))
        return out_path
    except Exception as e:  # noqa: BLE001
        logger.error("html_to_pdf failed: %s", e)
        return None


def text_to_pdf(text: str, out_path: Path, title: str = "Транскрипция") -> Path | None:
    """Plain-text fallback (used when there are no diarized segments)."""
    body = f'<div class="content">{_esc(text).replace(chr(10), "<br>")}</div>'
    css = ".content { white-space: pre-line; background: #f8fafc; padding: 16px; border-radius: 8px; border: 1px solid #e3e8f0; }"
    return html_to_pdf(_page(title, body, css), out_path)
