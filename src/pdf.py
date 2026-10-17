"""Exportacao do relatorio Markdown para PDF via HTML e Edge headless (padrao ABNT)."""

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
@page { size: A4; margin: 20mm 20mm 20mm 30mm; }

html { -webkit-print-color-adjust: exact; }

body {
  font-family: Arial, Helvetica, sans-serif;
  font-size: 12pt;
  line-height: 1.5;
  color: #000;
  text-align: justify;
  padding-top: 10mm;
}

.cabecalho-corrido {
  position: fixed;
  top: 0;
  left: 0;
  font-family: Arial, sans-serif;
  font-size: 11pt;
  color: #333;
  z-index: 1;
}

.numero-pagina {
  position: fixed;
  top: 0;
  right: 0;
  font-family: Arial, sans-serif;
  font-size: 11pt;
  z-index: 1;
}

.numero-pagina::after { content: counter(page); }

p {
  margin: 0;
  text-indent: 1.25cm;
  text-align: justify;
}

h1, h2, h3, h4, h5, h6 {
  font-family: Arial, sans-serif;
  font-weight: bold;
  text-align: left;
  page-break-after: avoid;
  orphans: 3;
  widows: 3;
}

h2 { font-size: 14pt; margin: 22px 0 8px; }
h3 { font-size: 13pt; margin: 18px 0 6px; }
h4 { font-size: 12pt; margin: 14px 0 4px; }
h5, h6 { font-size: 12pt; margin: 12px 0 4px; }

strong, b { font-weight: bold; }

ul, ol { margin: 8px 0 8px 1.25cm; padding-left: 0; }
li { margin-bottom: 4px; text-align: justify; }

table {
  border-collapse: collapse;
  width: 100%;
  margin: 4px 0 16px;
  font-size: 10pt;
  line-height: 1.3;
  page-break-inside: avoid;
}
th, td {
  border: 1px solid #888;
  padding: 5px 7px;
  text-align: left;
  vertical-align: top;
}
th { background: #e6e6e6; font-weight: bold; }
tr:nth-child(even) td { background: #f6f6f6; }

.tabela-legenda {
  font-family: Arial, sans-serif;
  font-size: 11pt;
  margin-top: 16px;
  text-align: left;
  page-break-after: avoid;
}

figure.figura {
  margin: 14px auto;
  text-align: center;
  page-break-inside: avoid;
  width: 92%;
}
figure.figura img { max-width: 100%; max-height: 95mm; height: auto; }
figure.figura figcaption {
  font-family: Arial, sans-serif;
  font-size: 10pt;
  line-height: 1.3;
  text-align: center;
  margin-top: 6px;
}

.diagrama {
  margin: 14px 0;
  page-break-inside: avoid;
  font-family: Arial, sans-serif;
  font-size: 10.5pt;
}
.diagrama.comparativo { display: flex; gap: 10px; align-items: stretch; }
.diag-coluna {
  flex: 1;
  border: 1px solid #888;
  padding: 7px 10px;
  background: #f4f4f4;
}
.diag-coluna h4 { margin: 0 0 6px; font-size: 11pt; text-align: center; }
.diag-coluna ul { margin: 0 0 0 14px; }
.diagrama.fluxo { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
.diag-etapa {
  border: 1px solid #888;
  background: #f4f4f4;
  padding: 5px 9px;
}
.diag-num {
  display: inline-block;
  min-width: 17px;
  text-align: center;
  font-weight: bold;
  margin-right: 5px;
}
.diag-seta { font-size: 11pt; color: #777; padding: 0 1px; }

blockquote {
  border-left: 3px solid #666;
  margin: 12px 0;
  padding: 4px 14px;
  color: #222;
  background: none;
}

code { background: #f0f0f0; padding: 1px 4px; font-size: 10pt; }

a { color: #000; text-decoration: none; word-break: break-all; }

hr { border: none; border-top: 1px solid #999; margin: 18px 0; }

.capa {
  position: relative;
  z-index: 5;
  background: #fff;
  margin-top: -10mm;
  height: 257mm;
  display: flex;
  flex-direction: column;
  page-break-after: always;
  font-family: Arial, sans-serif;
  text-align: center;
}
.capa p, .capa h1 { text-indent: 0; }
.capa-instituicao { font-size: 12pt; margin-top: 12mm; font-weight: bold; }
.capa-autor { font-size: 12pt; margin-top: 10mm; font-weight: bold; }
.capa-titulo {
  font-size: 20pt;
  font-weight: bold;
  color: #000;
  border: none;
  margin: auto 8mm;
  line-height: 1.4;
}
.capa-subtitulo { font-size: 12pt; margin: 0 10mm 40mm; }
.capa-espaco { display: none; }
.capa-local { font-size: 12pt; margin-bottom: 28mm; }
.capa-data { font-size: 12pt; margin-bottom: 12mm; }
"""


def markdown_to_html(md_text: str, title: str, author: str = "") -> str:
    """Converte Markdown em pagina HTML estilizada para impressao ABNT."""
    body = markdown.markdown(md_text, extensions=["tables", "toc", "sane_lists"])
    surname = re.split(r"\s+", author.strip())[-1].upper() if author.strip() else ""
    cabecalho = f'<div class="cabecalho-corrido">{surname}</div>' if surname else ""
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{CSS}</style>
</head>
<body>
{cabecalho}
<div class="numero-pagina"></div>
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
    from .latex import export_latex, latex_available

    md_path = settings.output_dir / f"{base_name}.md"
    title = re.search(r"^# (.+)$", md_text, flags=re.MULTILINE)
    outline_title = settings.outline.get("title", "")
    doc_title = title.group(1) if title else (outline_title or base_name)
    author = str(settings.outline.get("author", ""))

    md_path.write_text(md_text, encoding="utf-8")
    html_path = settings.output_dir / f"{base_name}.html"
    html_path.write_text(
        markdown_to_html(md_text, doc_title, author=author), encoding="utf-8"
    )

    if latex_available():
        try:
            result = export_latex(settings, md_text, base_name)
        except Exception as exc:
            print(f"[pdf/latex] erro inesperado: {exc}; usando navegador como fallback")
            result = None
        if result:
            print(f"[pdf] gerado via LaTeX (Pandoc+Tectonic): {result[1]}")
            return md_path, result[1]
        print("[pdf] LaTeX indisponivel ou falhou; usando navegador como fallback")

    pdf_path = settings.output_dir / f"{base_name}.pdf"

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
