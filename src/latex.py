from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .config import Settings

PANDOC_CANDIDATES = [
    r"C:\Users\{}\AppData\Local\Pandoc\pandoc.exe",
    r"C:\Program Files\Pandoc\pandoc.exe",
    r"C:\Program Files (x86)\Pandoc\pandoc.exe",
]
TECTONIC_CANDIDATES = [
    r"C:\Users\{}\AppData\Local\tectonic\tectonic.exe",
]


def find_pandoc() -> Path | None:
    import getpass

    found = shutil.which("pandoc")
    if found:
        return Path(found)
    user = getpass.getuser()
    for template in PANDOC_CANDIDATES:
        path = Path(template.format(user))
        if path.exists():
            return path
    return None


def find_tectonic() -> Path | None:
    import getpass

    found = shutil.which("tectonic")
    if found:
        return Path(found)
    user = getpass.getuser()
    for template in TECTONIC_CANDIDATES:
        path = Path(template.format(user))
        if path.exists():
            return path
    return None


def _latex_escape(text: str) -> str:
    for ch, rep in (
        ("\\", r"\textbackslash{}"),
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ):
        text = text.replace(ch, rep)
    return text


def _md_links_to_href(text: str) -> str:

    def replace(match: re.Match) -> str:
        label = _latex_escape(match.group(1))
        url = match.group(2)
        for ch in "%#&":
            url = url.replace(ch, "\\" + ch)
        return rf"\href{{{url}}}{{{label}}}"

    return re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", replace, text)


def _fig_caption_html_to_latex(caption_html: str) -> str:
    strong = re.search(r"<strong>(.*?)</strong>", caption_html, re.S)
    em = re.search(r"<em>(.*?)</em>", caption_html, re.S)
    rest = caption_html
    if strong:
        rest = rest.replace(strong.group(0), "")
    if em:
        rest = rest.replace(em.group(0), "")
    rest = re.sub(r"<[^>]+>", "", rest).strip()
    parts = []
    if strong:
        parts.append(rf"\textbf{{{_latex_escape(strong.group(1))}}}")
    if em:
        parts.append(rf"\textit{{{_latex_escape(em.group(1))}}}")
    if rest:
        parts.append(_latex_escape(rest))
    joined = " ".join(parts)
    return _md_links_to_href(joined)


FIG_IMG_RE = re.compile(
    r'<figure class="figura"><img src="(?P<src>[^"]+)"[^>]*>'
    r"<figcaption>(?P<cap>.*?)</figcaption></figure>",
    re.S,
)
FIG_COMPARATIVO_RE = re.compile(
    r'<figure class="figura"><div class="diagrama comparativo">'
    r"(?P<cols>(?:<div class=\"diag-coluna\">.*?</ul></div>)+)"
    r"</div><figcaption>(?P<cap>.*?)</figcaption></figure>",
    re.S,
)
FIG_FLUXO_RE = re.compile(
    r'<figure class="figura"><div class="diagrama fluxo">'
    r"(?P<etapas>.*?)</div><figcaption>(?P<cap>.*?)</figcaption></figure>",
    re.S,
)
COLUNA_RE = re.compile(r"<h4>(?P<title>.*?)</h4><ul>(?P<items>.*?)</ul>", re.S)
ITEM_RE = re.compile(r"<li>(?P<item>.*?)</li>", re.S)
ETAPA_RE = re.compile(r'<div class="diag-etapa"><span class="diag-num">\d+</span>(?P<etapa>.*?)</div>', re.S)
TABELA_LEGENDA_RE = re.compile(r'<div class="tabela-legenda">(.*?)</div>', re.S)
CAPA_RE = re.compile(
    r'<div class="capa">.*?capa-data">\d{4}</p>\s*</div>', re.S
)


def _latex_path(path: str, base_dir: str = "output") -> str:
    import os
    p = path.replace("\\", "/")
    if not os.path.isabs(p):
        p = os.path.join(os.path.abspath(base_dir), p).replace("\\", "/")
    return p


def _replace_fig_img(match: re.Match) -> str:
    src = match.group("src")
    legend = _fig_caption_html_to_latex(match.group("cap"))
    return (
        "\\par\\vspace{8pt}\n"
        "\\begin{figure}[H]\n\\centering\n"
        "\\includegraphics[width=0.9\\linewidth]{assets/figura-"
        + src.split("figura-")[1].replace(".png", "")
        + ".png}\n"
        "\\par\\smallskip\n"
        f"\\begin{{flushleft}}\\small\\noindent {legend}\\par\\end{{flushleft}}\n"
        "\\end{figure}\n"
        "\\par\\vspace{5pt}\n"
    )


def _replace_fig_comparativo(match: re.Match) -> str:
    colunas = []
    for col in COLUNA_RE.finditer(match.group("cols")):
        titulo = _allowbreak_slashes(_md_links_to_href(_latex_escape(col.group("title"))))
        itens = [
            _allowbreak_slashes(_md_links_to_href(_latex_escape(i.group("item"))))
            for i in ITEM_RE.finditer(col.group("items"))
        ]
        colunas.append((titulo, itens))
    if not colunas:
        return ""
    n = len(colunas)
    width = 0.9 / n
    header = " & ".join(rf"\bfseries {t}" for t, _ in colunas) + r" \\"
    colspec = f">{{\\raggedright\\arraybackslash}}p{{{width:.3f}\\textwidth}}"
    lines = [
        "\\par\\vspace{8pt}",
        "\\begin{center}",
        "\\begin{tabular}{|" + "|".join([colspec] * n) + "|}",
        "\\hline",
        header,
        "\\hline",
    ]
    max_len = max((len(items) for _, items in colunas), default=0)
    for row in range(max_len):
        cells = []
        for _, itens in colunas:
            cells.append(itens[row] if row < len(itens) else "")
        lines.append(" & ".join(cells) + r" \\")
        lines.append("\\hline")
    lines += ["\\end{tabular}", "\\end{center}", "\\par\\vspace{4pt}", ""]
    legend = _fig_caption_html_to_latex(match.group("cap"))
    lines += [
        f"\\begin{{flushleft}}\\small\\noindent {legend}\\par\\end{{flushleft}}",
        "\\par\\vspace{5pt}",
        "",
    ]
    return "\n".join(lines)


def _replace_fig_fluxo(match: re.Match) -> str:
    etapas = [
        _md_links_to_href(_latex_escape(e.group("etapa")))
        for e in ETAPA_RE.finditer(match.group("etapas"))
    ]
    items = "\n".join(f"\\item {e}" for e in etapas)
    legend = _fig_caption_html_to_latex(match.group("cap"))
    return (
        "\\par\\vspace{8pt}\n"
        "\\begin{center}\n\\begin{minipage}{0.95\\textwidth}\n\\small\n"
        "\\begin{enumerate}\n" + items + "\\end{enumerate}\n"
        "\\end{minipage}\n\\end{center}\n"
        "\\par\\smallskip\n"
        f"\\begin{{flushleft}}\\small\\noindent {legend}\\par\\end{{flushleft}}\n"
        "\\par\\vspace{5pt}\n"
    )


def _replace_tabela_legend(match: re.Match) -> str:
    html = match.group(1)
    strong = re.search(r"<strong>(.*?)</strong>", html, re.S)
    em = re.search(r"<em>(.*?)</em>", html, re.S)
    parts = []
    if strong:
        parts.append(rf"\textbf{{{_latex_escape(strong.group(1))}}}")
    if em:
        parts.append(rf"\textit{{{_latex_escape(em.group(1))}}}")
    joined = _md_links_to_href(" ".join(parts))
    return f"\\par\\vspace{{18pt}}\\noindent {joined}\\par\\vspace{{8pt}}\n"


def _allowbreak_slashes(text: str) -> str:
    urls: list[str] = []

    def _hide(match: re.Match) -> str:
        urls.append(match.group(0))
        return f"\x00{len(urls) - 1}\x00"

    text = re.sub(r"\\(?:href|url)\{[^{}]*\}", _hide, text)
    text = re.sub(r"(?<=\w)/(?=\w)", r"/\\allowbreak{}", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: urls[int(m.group(1))], text)


def _table_cell_to_latex(cell: str) -> str:
    text = _md_links_to_href(_latex_escape(cell.strip()))
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)
    text = re.sub(r"(?<!\\)\*(?!\s)(.+?)(?<!\\)\*(?!\*)", r"\\emph{\1}", text)
    return _allowbreak_slashes(text)


def _md_tables_to_latex(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.lstrip().startswith("|"):
            out.append(line)
            i += 1
            continue
        block: list[str] = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            block.append(lines[i].strip())
            i += 1
        rows = [
            [c.strip() for c in row.strip("|").split("|")]
            for row in block
            if not set(row.replace("|", "").replace("-", "").replace(":", "").strip()) <= {""}
        ]
        if len(rows) < 2:
            out.extend(block)
            continue
        ncols = max(len(r) for r in rows)
        width = 0.92 / ncols
        colspec = f">{{\\raggedright\\arraybackslash}}p{{{width:.3f}\\textwidth}}"
        spec = "|" + "|".join([colspec] * ncols) + "|"
        header = " & ".join(
            rf"\bfseries {_table_cell_to_latex(c)}" for c in rows[0]
        ) + r" \\"
        out.append("\\par\\vspace{18pt}")
        out.append("\\begin{longtable}{" + spec + "}")
        out.append("\\hline")
        out.append(header)
        out.append("\\hline")
        out.append("\\endfirsthead")
        out.append("\\hline")
        out.append(header)
        out.append("\\hline")
        out.append("\\endhead")
        out.append("\\hline")
        out.append("\\endlastfoot")
        body_rows = rows[1:]
        for ri, row in enumerate(body_rows):
            cells = [_table_cell_to_latex(c) for c in row]
            cells += [""] * (ncols - len(cells))
            out.append(" & ".join(cells) + r" \\")
            if ri < len(body_rows) - 1:
                out.append("\\hline")
        out.append("\\end{longtable}")
        out.append("\\par\\vspace{18pt}")
        out.append("")
    return "\n".join(out)


def md_to_latex_body(md_text: str) -> str:
    text = CAPA_RE.sub("", md_text)
    text = re.sub(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", "", text, flags=re.MULTILINE)
    text = FIG_COMPARATIVO_RE.sub(_replace_fig_comparativo, text)
    text = FIG_FLUXO_RE.sub(_replace_fig_fluxo, text)
    text = FIG_IMG_RE.sub(_replace_fig_img, text)
    text = TABELA_LEGENDA_RE.sub(_replace_tabela_legend, text)
    text = _md_tables_to_latex(text)
    text = re.sub(r"\[([^\]]+)\]\(#[^)]+\)", r"\1", text)
    text = re.sub(r"\n(#{1,6} )", r"\n\n\1", text)
    text = re.sub(r"^(#{2,6}) ", lambda m: m.group(1)[1:] + " ", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _cover_tex(outline: dict) -> str:
    import datetime

    ano = datetime.date.today().year
    autor = str(outline.get("author", "")).strip()
    instituicao = str(outline.get("institution", "")).strip()
    titulo = str(outline.get("title", "")).strip()
    subtitulo = str(outline.get("subtitle", "")).strip()
    local = str(outline.get("place", "Brasil")).strip()
    return "\n".join(
        [
            r"\begin{titlepage}",
            r"\thispagestyle{empty}",
            r"\centering",
            r"\vspace*{12mm}",
            rf"{{\large\bfseries {_latex_escape(instituicao)}\par}}",
            r"\vspace{30mm}",
            rf"{{\large\bfseries {_latex_escape(autor)}\par}}",
            r"\vfill",
            rf"{{\LARGE\bfseries {_latex_escape(titulo)}\par}}",
            r"\vspace{10mm}",
            rf"{{\large {_latex_escape(subtitulo)}\par}}",
            r"\vfill",
            rf"{_latex_escape(local)}\\",
            rf"{ano}",
            r"\vspace*{20mm}",
            r"\end{titlepage}",
            "",
        ]
    )


def _header_tex(outline: dict) -> str:
    return "\n".join(
        [
            r"\usepackage[T1]{fontenc}",
            r"\usepackage[utf8]{inputenc}",
            r"\usepackage{textcomp}",
            r"\DeclareTextSymbolDefault{\textordmasculine}{TS1}",
            r"\DeclareTextSymbolDefault{\textordfeminine}{TS1}",
            r"\usepackage{mathptmx}",
            r"\usepackage{graphicx}",
            r"\graphicspath{{assets/}}",
            r"\usepackage{float}",
            r"\usepackage{longtable}",
            r"\usepackage{array}",
            r"\usepackage{xurl}",
            r"\usepackage{microtype}",
            r"\usepackage{fancyhdr}",
            r"\pagestyle{fancy}",
            r"\fancyhf{}",
            r"\fancyhead[R]{\thepage}",
            r"\renewcommand{\headrulewidth}{0pt}",
            r"\setlength{\headheight}{15pt}",
            r"\setlength{\parindent}{1.25cm}",
            r"\setlength{\parskip}{0pt}",
            r"\linespread{1.5}",
            r"\clubpenalty=10000",
            r"\widowpenalty=10000",
            r"\displaywidowpenalty=10000",
            r"\brokenpenalty=10000",
            r"\emergencystretch=3em",
            r"\usepackage{needspace}",
            r"\let\secaoAntiga\section",
            r"\renewcommand{\section}[1]{\clearpage\Needspace{8\baselineskip}\secaoAntiga{#1}}",
            r"\let\subsecaoAntiga\subsection",
            r"\renewcommand{\subsection}[1]{\Needspace{6\baselineskip}\subsecaoAntiga{#1}}",
            r"\let\subsubAntiga\subsubsection",
            r"\renewcommand{\subsubsection}[1]{\Needspace{5\baselineskip}\subsubAntiga{#1}}",
            r"\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}",
            "",
        ]
    )


def latex_available() -> bool:
    return find_pandoc() is not None and find_tectonic() is not None


def _postprocess_tex(tex: str) -> str:

    def _wrap_bare_url(match: re.Match) -> str:
        url = match.group(1)
        url = url.replace(r"\_", "_").replace(r"\%", "%")
        url = url.replace(r"\#", "#").replace(r"\&", "&").replace(r"\$", "$")
        trailing = ""
        while url and url[-1] in ".,;:":
            trailing = url[-1] + trailing
            url = url[:-1]
        return rf"\url{{{url}}}{trailing}"

    hidden: list[str] = []

    def _hide(match: re.Match) -> str:
        hidden.append(match.group(0))
        return f"\x00{len(hidden) - 1}\x00"

    tex = re.sub(r"\\(?:href|url)\{[^{}]*\}", _hide, tex)
    tex = re.sub(r"(?<![\w{\\])(https?://[^\s{}]+)", _wrap_bare_url, tex)
    tex = re.sub(
        r"\x00(\d+)\x00", lambda m: hidden[int(m.group(1))], tex
    )
    tex = re.sub(r"(?<=\S) +(\\href\{[^{}]*\}\{\()", r"~\1", tex)
    tex = re.sub(
        r"(?m)^((?:\\textbf\{)?Fontes consultadas:?\}?)( [^\n]*)$",
        r"{\\small \1\2\\par}",
        tex,
    )
    return tex


def export_latex(settings: Settings, md_text: str, base_name: str) -> tuple[Path, Path] | None:
    pandoc = find_pandoc()
    tectonic = find_tectonic()
    if not pandoc or not tectonic:
        return None

    output_dir = settings.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    build_dir = output_dir / "build-latex"
    build_dir.mkdir(exist_ok=True)

    body = md_to_latex_body(md_text)
    import re as _re
    assets_dir = output_dir / "assets"
    def _remove_missing_fig(m):
        src = m.group(2)
        return m.group(0) if (assets_dir / src).exists() else ""
    body = _re.sub(
        r"\\includegraphics\[width=0\.9\\linewidth\]\{(assets/)?(figura-\d+\.png)\}",
        _remove_missing_fig,
        body,
    )
    body_path = build_dir / "corpo.md"
    cover_path = build_dir / "capa.tex"
    header_path = build_dir / "cabecalho.tex"
    body_path.write_text(body, encoding="utf-8-sig")
    cover_path.write_text(_cover_tex(settings.outline), encoding="utf-8")
    header_path.write_text(_header_tex(settings.outline), encoding="utf-8")
    import shutil
    assets_src = output_dir / "assets"
    if assets_src.is_dir():
        shutil.copytree(assets_src, build_dir / "assets", dirs_exist_ok=True)

    pdf_path = output_dir / f"{base_name}.pdf"
    tex_path = build_dir / "corpo_completo.tex"
    cmd_tex = [
        str(pandoc),
        str(body_path),
        "-f", "markdown+raw_tex+smart",
        "-t", "latex",
        "--standalone",
        "--wrap=none",
        "-V", "documentclass=article",
        "-V", "classoption=12pt",
        "-V", "papersize=a4",
        "-V", "geometry:top=2cm,bottom=2cm,left=3cm,right=2cm",
        "-V", "colorlinks=true",
        "-V", "linkcolor=black",
        "-V", "urlcolor=black",
        "-V", "toccolor=black",
        "-H", str(header_path),
        "--include-before-body", str(cover_path),
        "-o", str(tex_path),
        "--metadata", "documentclass=article",
    ]
    subprocess.run(cmd_tex, capture_output=True, text=True, timeout=300, cwd=str(output_dir))
    if not tex_path.exists():
        print("[pdf/latex] falha ao gerar .tex via pandoc")
        return None
    tex = tex_path.read_text(encoding="utf-8")
    tex = tex.replace("º", r"\textordmasculine{}").replace("ª", r"\textordfeminine{}")
    tex = _postprocess_tex(tex)
    tex_path.write_text(tex, encoding="utf-8")
    cmd = [str(tectonic), str(tex_path), "--outdir", str(build_dir)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900, cwd=str(build_dir))
    built_pdf = build_dir / "corpo_completo.pdf"
    if result.returncode != 0 or not built_pdf.exists():
        print(f"[pdf/latex] falha tectonic: {(result.stderr or result.stdout)[-800:]}")
        return None
    shutil.copy(built_pdf, pdf_path)
    return pdf_path, pdf_path
