"""Exportacao do relatorio Markdown para PDF via HTML e Edge headless."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import markdown

from .config import Settings

EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

CSS = """
@page { size: A4; margin: 20mm 18mm; }
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 11pt; line-height: 1.55; color: #1a1a1a; max-width: 900px; margin: 0 auto; padding: 24px; }
h1 { font-size: 22pt; color: #0b3d62; border-bottom: 3px solid #0b3d62; padding-bottom: 8px; }
h2 { font-size: 15pt; color: #0b3d62; margin-top: 28px; border-bottom: 1px solid #ccd9e3; padding-bottom: 4px; page-break-after: avoid; }
h3 { font-size: 12.5pt; color: #14507a; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 10pt; page-break-inside: avoid; }
th, td { border: 1px solid #b9c9d6; padding: 7px 9px; text-align: left; vertical-align: top; }
th { background: #e8f0f6; color: #0b3d62; }
tr:nth-child(even) td { background: #f7fafc; }
ul, ol { margin: 8px 0 8px 20px; }
li { margin-bottom: 4px; }
blockquote { border-left: 4px solid #0b3d62; margin: 12px 0; padding: 6px 14px; background: #f2f7fb; color: #33475a; }
code { background: #f0f3f5; padding: 1px 4px; border-radius: 3px; font-size: 9.5pt; }
a { color: #0b3d62; text-decoration: none; }
hr { border: none; border-top: 1px solid #ccd9e3; margin: 22px 0; }
"""


def markdown_to_html(md_text: str, title: str) -> str:
    """Converte Markdown em pagina HTML estilizada para impressao."""
    body = markdown.markdown(md_text, extensions=["tables", "toc", "sane_lists"])
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


def find_browser() -> Path | None:
    """Localiza Edge ou Chrome instalados para uso como renderizador de PDF."""
    for path in EDGE_PATHS:
        if Path(path).exists():
            return Path(path)
    return None


def html_to_pdf(html_path: Path, pdf_path: Path, browser: Path) -> None:
    """Imprime a pagina HTML em PDF usando o browser em modo headless."""
    cmd = [
        str(browser),
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        f"--print-to-pdf={pdf_path}",
        "--no-pdf-header-footer",
        html_path.resolve().as_uri(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not pdf_path.exists():
        raise RuntimeError(f"Falha ao gerar PDF: {result.stderr[:300]}")


def export(settings: Settings, md_text: str, base_name: str) -> tuple[Path, Path]:
    """Gera .md e .pdf do relatorio no diretorio de saida, retornando os caminhos."""
    md_path = settings.output_dir / f"{base_name}.md"
    html_path = settings.output_dir / f"{base_name}.html"
    pdf_path = settings.output_dir / f"{base_name}.pdf"
    title = re.search(r"^# (.+)$", md_text, flags=re.MULTILINE)
    doc_title = title.group(1) if title else base_name

    md_path.write_text(md_text, encoding="utf-8")
    html_path.write_text(markdown_to_html(md_text, doc_title), encoding="utf-8")

    browser = find_browser()
    if browser is None:
        print("[pdf] navegador nao encontrado; apenas Markdown e HTML gerados")
        return md_path, html_path
    try:
        html_to_pdf(html_path, pdf_path, browser)
        print(f"[pdf] gerado: {pdf_path}")
    except Exception as exc:
        print(f"[pdf] erro: {exc}; mantendo apenas Markdown e HTML")
        return md_path, html_path
    return md_path, pdf_path
