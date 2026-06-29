"""PDF generation. Primary engine is WeasyPrint (works headless on Linux).

PDF generation is best-effort: if it fails, the job still keeps the text/HTML
result so the user can read it in the web UI.
"""
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("transcribe.pdf")


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
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>{title}</title>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; margin: 40px; color: #333; }}
            .header {{ text-align: center; margin-bottom: 30px; border-bottom: 2px solid #ccc; padding-bottom: 20px; }}
            .content {{ white-space: pre-line; font-size: 12pt; background: #f9f9f9; padding: 20px; border-radius: 5px; border: 1px solid #ddd; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>{title}</h1>
            <p>Дата генерации: {datetime.now().strftime('%d.%m.%Y %H:%M')}</p>
        </div>
        <div class="content">{escaped}</div>
    </body>
    </html>
    """
    return html_to_pdf(html_content, out_path)
