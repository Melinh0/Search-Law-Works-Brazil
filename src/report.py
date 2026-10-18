from __future__ import annotations

import datetime
import json
import re
import unicodedata

from .annex import build_charts_annex
from .charts import GRAFICO_RE, MATH_TYPES, ChartError, ChartRenderer, count_graficos, parse_spec
from .config import Settings
from .llm import OllamaClient
from .rag import SearchIndex, build_index

SYSTEM_PROMPT = (
    "Voce e um analista juridico brasileiro especializado em direito do trabalho. "
    "Escreva em portugues do Brasil, tom objetivo e direto, paragrafos curtos, "
    "sem advogar em causa propria e sem inventar leis, artigos ou numeros inexistentes. "
    "REGRAS DE CITACAO (obrigatorias, padrao ABNT hibrido):\n"
    "1. Toda afirmacao factual termina com o codigo de uma das fontes fornecidas, "
    "entre parenteses, no final da frase, por exemplo: ... (BRASIL, 1943, art. 58). "
    "ou (IBGE, 2026). ou (FOLHA DE S.PAULO, 2026).\n"
    "2. Use APENAS os codigos exatos da lista 'Fontes disponiveis' deste prompt; "
    "nunca invente codigos, leis ou artigos.\n"
    "3. PROIBIDO escrever frases como 'nao ha dados suficientes', 'base insuficiente', "
    "'nao ha fontes suficientes', 'informacoes nao disponiveis', 'lacunas de informacao' "
    "ou qualquer desculpa por falta de fonte. Se algum ponto nao estiver no contexto, "
    "aproxime os pontos cobertos com profundidade e cite sempre.\n"
    "4. Termine cada secao com um bloco '**Em termos simples:**' explicando o assunto "
    "para um leigo, em linguagem coloquial e correta, com citacao ao final.\n"
    "5. Quando pedir grafico, use o bloco JSON ```grafico``` exatamente no formato "
    "solicitado; os valores devem vir APENAS dos numeros presentes no contexto e o "
    "campo 'fonte' deve ser o codigo exato de uma fonte do contexto. O campo "
    "'unidade' e OBRIGATORIO e deve descrever a escala de forma clara para o leigo "
    "(ex.: 'pessoas', 'pessoas (milhoes)', 'R$ (media mensal)', 'percentual (%)').\n"
    "   REGRA DAS UNIDADES: todos os valores de UM grafico devem estar na MESMA unidade "
    "e na MESMA ordem de grandeza. Proibido misturar escalas no mesmo grafico de barras "
    "(ex.: NAO juntar '8 horas por dia' e '44 horas por semana' na mesma serie). Se os "
    "numeros forem de unidades ou escalas diferentes, use tabelas ou graficos separados.\n"
    "   REGRA DA INTERPRETACAO: TODO grafico deve trazer o campo 'nota' com UMA frase "
    "que interprete o que o grafico mostra (tendencia, comparacao ou regra). PROIBIDO "
    "grafico com apenas UM valor ou UMA categoria - use comparativo ou tabela.\n"
    "   REGRA DAS SERIES TEMPORAIS: graficos de linha de series temporais devem usar a "
    "serie historica LONGA completa do contexto (ex.: medias anuais de 2012 a 2026), "
    "nunca apenas os periodos recentes ou uma recorte curto. Se um grafico usar um "
    "recorte de pontos selecionados, justifique a escolha na nota da figura e remeta "
    "o leitor a figura com a serie completa.\n"
    "   REGRA DOS NUMEROS EM DIAGRAMAS: nos itens de comparativo e nas tabelas, escreva "
    "numeros formatados em portugues com unidade (ex.: '1,2 milhao', 'R$ 3.777,50', "
    "'180 meses'); NAO escreva numeros crus como '1200000'.\n"
    "   REGRA DE COBERTURA DAS SERIES: quando a fonte cobrir apenas poucos anos "
    "(ex.: PNS 2013 e 2019, pesquisa quinquenal) ou houver anos sem dado na serie, "
    "use nos graficos APENAS os anos existentes no contexto e explique NO TEXTO o "
    "motivo da cobertura (periodicidade da pesquisa, edicoes disponiveis, coleta em "
    "andamento); NUNCA invente valores, anos intermediarios ou interpolacoes.\n"
    "   REGRA DE VALORES E PODER DE COMPRA: quando graficar salario minimo ou "
    "rendimento medio, traga pelo menos um grafico com os valores EM REAIS (R$) do "
    "contexto; se usar indice (ex.: 2012=100), explique a base na 'unidade' e na "
    "'nota'. Sempre que houver inflacao (IPCA) no contexto, faca tambem a leitura "
    "de poder de compra desses valores - NAO misture reais e indice no mesmo "
    "grafico.\n"
    "6. Escreva TODO o texto com a acentuacao, ortografia e gramatica corretas do "
    "portugues do Brasil (secao, nao, direito, previdencia com acentos corretos). "
    "NUNCA entregue texto sem os acentos corretos."
)

BANNED_PHRASES = [
    r"n[ãa]o h[áa] dados? suficiente",
    r"n[ãa]o h[áa] (?:dado|fonte|informa[çc][ãa]o) (?:suficiente|dispon[íi]vel)",
    r"base (?:de dados )?(?:de contexto )?(?:e|é) insuficiente",
    r"insuficiente para (?:detalhar|especificar|responder)",
    r"informa[çc][õo]es? n[ãa]o dispon[íi]vel",
    r"lacunas? de informa[çc][ãa]o",
    r"n[ãa]o h[áa] dados suficientes",
    r"contexto (?:fornecido )?(?:n[ãa]o )?(?:detalha|informa|cobre)",
    r"n[ãa]o (?:foram|foi) encontrad[oa]s? dados? suficiente",
    r"limitando-se (?:ao|a) contexto",
]
BANNED_RE = re.compile("|".join(f"(?:{p})" for p in BANNED_PHRASES), re.IGNORECASE)

TABLE_DIRECTIVE_RE = re.compile(
    r"^TABELA:\s*(.+?)\s*\|\s*fonte:\s*(.+?)\s*$", re.IGNORECASE
)
LAW_PATTERNS = [
    (re.compile(r"Constitui[çc][ãa]o Federal", re.I), "BRASIL, 1988"),
    (re.compile(r"Decreto-Lei\s+5\.452", re.I), "BRASIL, 1943"),
    (re.compile(r"Emenda Constitucional\s+(\d+)/(\d{4})", re.I), "EC {0}/{1}"),
    (re.compile(r"Lei Complementar\s+(\d[\d.]*)/(\d{4})", re.I), "LC {0}/{1}"),
    (re.compile(r"\bLei\s+(\d[\d.]*)/(\d{4})", re.I), "LEI {0}/{1}"),
    (re.compile(r"\bLei\s+(\d[\d.]*?),\s*de\s+\d+\s+de\s+\w+\s+de\s+(\d{4})", re.I), "LEI {0}/{1}"),
]

MONTHS_PT = [
    "jan.", "fev.", "mar.", "abr.", "mai.", "jun.",
    "jul.", "ago.", "set.", "out.", "nov.", "dez.",
]


def _norm(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text.lower())
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        "".join(ch for ch in stripped if not unicodedata.combining(ch)),
    ).strip()


def _access_date(date_str: str) -> str:
    try:
        d = datetime.date.fromisoformat(date_str[:10])
    except ValueError:
        d = datetime.date.today()
    return f"{d.day:02d} {MONTHS_PT[d.month - 1]} {d.year}"


def _code_from_title(title: str, source_id: str, url: str, year: str, chunk_text: str = "") -> str:
    if "ibge" in url.lower() or title.upper().startswith("IBGE"):
        match_year = re.search(r"\b(19|20)\d{2}\b", title)
        if match_year:
            return f"IBGE, {match_year.group(0)}"
        return f"IBGE, {year}"
    if "ipea.gov.br" in url.lower() or re.match(r"^IPEA\b", title, re.IGNORECASE):
        return f"IPEA, {year}"
    if "ebc.com.br" in url.lower():
        return f"AGENCIA BRASIL, {year}"
    if "dieese.org.br" in url.lower():
        return f"DIEESE, {year}"
    if "stf.jus.br" in url.lower():
        return f"STF, {year}"
    if "tst.jus.br" in url.lower():
        return f"TST, {year}"
    if "g1.globo.com" in url.lower():
        return f"G1, {year}"
    if "planalto.gov.br" in url or "gov.br" in url or "camara.leg.br" in url or "senado.leg.br" in url:
        for pattern, template in LAW_PATTERNS:
            match = pattern.search(title)
            if match:
                if "{0}" in template:
                    return f"BRASIL, {template.format(*match.groups())}"
                return template
        bill = re.match(r"^(P[A-Z]{1,3})\s*(\d{1,5})\s*/\s*(\d{4})", title)
        bill_code = (
            f"{bill.group(1)} {bill.group(2)}/{bill.group(3)}" if bill else None
        )
        if source_id == "senado-materias" or "senado.leg.br" in url:
            if bill_code:
                return f"SENADO FEDERAL, {bill_code}"
            return f"SENADO FEDERAL, {year}"
        if (
            source_id == "camara-proposicoes"
            or source_id == "camara-proposicoes-flex"
            or "camara.leg.br" in url
            or re.match(r"^(PL|PLP|PEC|PDL|PRS|REQ)\b", title)
        ):
            if bill_code:
                return f"CAMARA DOS DEPUTADOS, {bill_code}"
            return f"CAMARA DOS DEPUTADOS, {year}"
        if "esocial" in url.lower() or "esocial" in title.lower():
            return f"ESOCIAL, {year}"
        if "trabalho-e-emprego" in url or "Minist" in title or title.startswith("MTE"):
            return f"MTE, {year}"
        return f"BRASIL, {year}"
    if "scielo" in url.lower() or source_id == "artigos-academicos":
        authors = re.search(r"Autores?:\s*([^\n,]+)", chunk_text)
        if authors:
            surname = authors.group(1).strip().split()[-1]
            return f"{surname.upper()}, {year}"
        return f"PERIODICO ACADEMICO, {year}"
    outlet = re.split(r"\s+-\s+", title)[-1].strip()
    outlet = re.sub(r"\s*-\s*\(.*$", "", outlet)
    if len(outlet) >= 3 and outlet.lower() != title.lower():
        return f"{outlet.upper()}, {year}"
    return f"IMPRENSA, {year}"


IBGE_PUBLIC_URL = (
    "https://www.ibge.gov.br/estatisticas/sociais/trabalho/17270-pnad-continua.html"
)


def _public_url(url: str) -> str:
    if "servicodados.ibge.gov.br/api/" in url:
        return IBGE_PUBLIC_URL
    return url


def build_citation_registry(chunks: list[dict]) -> dict[str, dict]:
    by_code: dict[str, list[dict]] = {}
    seen_urls: set[str] = set()
    for c in chunks:
        raw_url = c["url"]
        url = _public_url(raw_url)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        year = (c.get("collected_at") or "")[:4] or str(datetime.date.today().year)
        code = _code_from_title(c["title"], c["source_id"], url, year, c.get("text", ""))
        entry = {
            "code": code,
            "title": c["title"],
            "url": url,
            "date": c.get("collected_at", ""),
            "source_id": c["source_id"],
            "priority": int(c.get("priority", 1)),
        }
        by_code.setdefault(code, []).append(entry)

    registry: dict[str, dict] = {}
    for code, entries in by_code.items():
        entries.sort(
            key=lambda e: (-e["priority"], e.get("date", ""), e["url"])
        )
        if len(entries) == 1:
            entries[0]["code"] = code
            registry[entries[0]["url"]] = entries[0]
            continue
        for i, entry in enumerate(entries):
            if i < 26:
                suffix = chr(ord("a") + i)
            else:
                suffix = (
                    chr(ord("a") + (i // 26) - 1) + chr(ord("a") + (i % 26))
                )
            entry["code"] = f"{code}{suffix}"
            registry[entry["url"]] = entry
    return registry


def format_sources_block(urls: list[str], registry: dict[str, dict]) -> str:
    lines = ["Fontes disponiveis (cite exatamente pelo codigo):"]
    seen: set[str] = set()
    for url in urls:
        entry = registry.get(url)
        if not entry or entry["code"] in seen:
            continue
        seen.add(entry["code"])
        lines.append(f"- Codigo: ({entry['code']}) | {entry['title']} | {entry['url']}")
    return "\n".join(lines)


def link_citations(text: str, registry: dict[str, dict]) -> str:
    by_norm: dict[str, str] = {}
    for entry in sorted(registry.values(), key=lambda e: -int(e.get("priority", 1))):
        by_norm.setdefault(_norm(entry["code"]), entry["url"])

    def link_one(inner: str) -> str:
        key = _norm(inner)
        for code_norm, url in by_norm.items():
            if key == code_norm or key.startswith(code_norm + " "):
                return f"[({inner})]({url})"
            if (
                code_norm.startswith(key)
                and len(code_norm) == len(key) + 1
                and code_norm[-1].isalpha()
            ):
                return f"[({inner})]({url})"
        return f"({inner})"

    def split_label(label: str) -> str:
        parts = [part.strip() for part in label.split(";")]
        if len(parts) < 2:
            return ""
        linked = []
        for part in parts:
            if part.startswith("(") and part.endswith(")"):
                part = part[1:-1].strip()
            linked.append(link_one(part))
        return "; ".join(linked)

    pieces: list[tuple[bool, str]] = []
    last = 0
    for match in re.finditer(r"\[([^\]]+)\]\((https?://[^)]+)\)", text):
        pieces.append((True, text[last : match.start()]))
        label = match.group(1)
        joined = split_label(label)
        pieces.append((False, joined or match.group(0)))
        last = match.end()
    pieces.append((True, text[last:]))

    def plain(match: re.Match) -> str:
        inner = match.group(1).strip()
        joined = split_label(inner)
        if joined:
            return joined
        linked = link_one(inner)
        return linked if linked != f"({inner})" else match.group(0)

    return "".join(
        re.sub(r"\(([^()]{3,80})\)", plain, piece) if is_text else piece
        for is_text, piece in pieces
    )


def collect_used_sources(text: str) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    for match in re.finditer(r"\[\(([^)]+)\)\]\((https?://[^)]+)\)", text):
        sources.append((match.group(1).strip(), match.group(2).strip()))
    for match in re.finditer(r"\[Fonte:\s*([^\]]+)\]\((https?://[^)]+)\)", text):
        sources.append((match.group(1).strip(), match.group(2).strip()))
    for match in re.finditer(r"^\*\s*\[([^\]]+)\]\((https?://[^)]+)\)", text, flags=re.MULTILINE):
        sources.append((match.group(1).strip(), match.group(2).strip()))
    return sources


def normalize_source_list(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    in_sources = False
    for line in lines:
        if re.match(r"^\**\s*Fontes consultadas", line, flags=re.IGNORECASE):
            in_sources = True
            out.append(line)
            continue
        if in_sources:
            if line.strip() and re.match(r"^#+\s", line):
                in_sources = False
            else:
                bare = re.search(r"(?<!\()https?://[^\s)\]]+", line)
                if line.strip().startswith("*") and bare:
                    url = bare.group(0)
                    title = line.replace(url, "").strip().rstrip(":").strip()
                    title = re.sub(r"^\*?\s*\[?|\]$", "", title).strip()
                    if title:
                        line = f"* [{title}]({url})"
        out.append(line)
    return "\n".join(out)


def _modality_queries(section: dict) -> list[str]:
    return [str(item).strip() for item in section.get("modalities", []) if str(item).strip()]


def retrieve_for_section(section: dict, index: SearchIndex, settings: Settings) -> list[dict]:
    defaults = settings.outline.get("defaults", {})
    top_k = 16 if section.get("structure") else 14
    queries = [section["title"], str(section.get("brief", ""))[:400]]
    queries.extend(_modality_queries(section))
    queries.extend(
        str(q).strip() for q in section.get("extra_queries", []) if str(q).strip()
    )
    chunks = index.search_many(queries, top_k=top_k)

    limit = 26 if section.get("structure") else 18
    if len(section.get("must_include", [])) > 3:
        limit = max(limit, 22)
    selected: list[dict] = [c for c in chunks]
    have = {(c["source_id"], c.get("chunk_index", 0)) for c in selected}
    forced: list[dict] = []
    for source_id in section.get("must_include", []):
        found = [c for c in index.chunks if c["source_id"] == source_id]
        found.sort(key=lambda c: int(c.get("priority", 1)), reverse=True)
        quota = 3 if source_id == "ibge-pnad" else 2
        added = 0
        for c in found:
            key = (c["source_id"], c.get("chunk_index", 0))
            if key in have:
                continue
            forced.append(c)
            have.add(key)
            added += 1
            if added >= quota:
                break
    if forced:
        selected = selected[: max(limit - len(forced), 0)] + forced
    return selected[:limit]


def _format_context(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"--- Contexto {i} | {c['title']} | {c['url']} | {c['collected_at']}\n{c['text']}"
        )
    return "\n\n".join(parts)


def _section_prompt(
    section: dict,
    context: str,
    defaults: dict,
    registry: dict[str, dict],
    feedback: str = "",
) -> str:
    min_words, max_words = section.get(
        "words", (defaults.get("min_words", 400), defaults.get("max_words", 900))
    )
    min_charts = section.get("charts", defaults.get("charts", 1))
    structure = section.get("structure") or []
    modalities_block = ""
    modality_lines = "\n".join(f"- {m}" for m in section.get("modalities", []))
    if modality_lines:
        modalities_block = (
            "Modalidades que DEVEM aparecer nesta secao (uma para cada item):\n"
            f"{modality_lines}\n\n"
        )
    structure_block = ""
    if structure:
        structure_block = (
            "Estrutura obrigatoria desta secao:\n"
            + "\n".join(f"{line}" for line in structure)
            + "\n\n"
        )
    feedback_block = ""
    if feedback:
        feedback_block = (
            "SUA VERSAO ANTERIOR VIOLOU REGRAS. Corrija tudo nesta versao:\n"
            f"{feedback}\n\n"
        )
    chart_rules = str(defaults.get("chart_rules", "")).strip()
    chart_rules_block = ""
    if chart_rules:
        chart_rules_block = f"Regras obrigatorias de graficos desta secao:\n{chart_rules}\n\n"
    return (
        feedback_block
        + f"Secao {section['number']}: {section['title']}\n\n"
        f"Objetivo da secao:\n{str(section['brief']).strip()}\n\n"
        f"{modalities_block}"
        "Contexto documental coletado de fontes oficiais, academicas e de imprensa:\n"
        f"{context}\n\n"
        f"{format_sources_block(list(registry.keys()), registry)}\n\n"
        "Escreva esta secao do relatorio em Markdown. Requisitos:\n"
        "1. Cabecalho em nivel 2 com o titulo exato da secao (o codigo ajusta a numeracao).\n"
        f"2. Entre {min_words} e {max_words} palavras, objetivas e faceis de ler.\n"
        "3. Use tabelas Markdown quando ajudar a comparar. ANTES de cada tabela, escreva "
        "uma linha no formato exato: TABELA: titulo da tabela | fonte: CODIGO\n"
        "4. Use apenas informacoes presentes no contexto. NUNCA escreva que falta dado "
        "ou fonte; se faltar algo do briefing, aprofunde o que existe e cite tudo.\n"
        "5. Cite CADA frase afirmativa, CADA item de lista e CADA linha de tabela com o "
        "codigo da fonte entre parenteses, por exemplo (BRASIL, 1943, art. 3o). "
        "Copie o codigo EXATAMENTE como aparece no contexto, incluindo a letra final "
        "quando houver (ex.: IBGE, 2026a e nao IBGE, 2026).\n"
        "6. Inclua exatamente NO MINIMO "
        f"{min_charts} bloco(s) grafico(s) ```grafico``` com dados reais do contexto. "
        "SE o contexto contiver 'Media anual (serie historica longa 2012-2026)', use "
        "OBRIGATORIAMENTE esses valores (todos os anos de 2012 ate 2026) nos graficos "
        "de linha, e NAO use apenas os meses recentes. "
        "TODO grafico deve trazer o campo 'nota' com UMA frase de interpretacao; "
        "PROIBIDO grafico com apenas UM valor (use comparativo ou tabela). "
        "Varie os tipos ao longo do relatorio (barra, hbar, pizza, donut, linha, "
        "barra_empilhada, comparativo). Para sequencias e etapas use tabela Markdown, "
        "NAO diagramas de fluxo. No bloco use JSON com chaves: tipo, "
        "titulo, rotulos, valores, unidade, fonte, nota. O campo 'fonte' e "
        "OBRIGATORIO em TODOS os tipos de grafico, inclusive 'comparativo' "
        "(coloque o codigo da fonte no nivel superior do JSON, ao lado de 'titulo'). "
        "Para comparativo use colunas: "
        "[{titulo, itens:[]}] com NO MINIMO 2 colunas. Os campos 'valores' e "
        "'series[].valores' devem conter APENAS numeros puros de JSON (ex.: 63.4, "
        "1621, 3500.7) - NUNCA textos: converta qualquer numero formatado em "
        "portugues ('R$ 1.621,00', '63,4%') para numero puro com ponto decimal e SEM "
        "separador de milhar (1621, 63.4); 'rotulos' e 'colunas' sim aceitam texto.\n"
        + chart_rules_block
        + structure_block
        + "7. Termine com '**Em termos simples:**' e depois com a lista 'Fontes consultadas:' "
        "em Markdown no formato '* (CODIGO, ano)' usando APENAS os codigos exatos das "
        "fontes do contexto que voce citou; NAO escreva URLs nem titulos - o sistema "
        "insere o link correto automaticamente.\n"
        "8. Nao repita o cabecalho de nenhuma outra secao."
    )


def _clean_section(text: str, section: dict) -> str:
    text = re.sub(r"^```(?:markdown)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"```grafico\s*\n(.*?)\n```", lambda m: f"\n```grafico\n{m.group(1)}\n```\n", text, flags=re.DOTALL)
    heading = f"## {section['number']}. {section['title']}"
    if not text.startswith("#"):
        text = f"{heading}\n\n{text}"
    else:
        text = re.sub(r"^#+\s*.*$", heading, text, count=1, flags=re.MULTILINE)
    return text.strip()


def _renumber_headings(text: str, number: int) -> str:
    counters = {3: 0, 4: 0, 5: 0}
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        match = re.match(r"^(#{2,6})\s+(.*)$", line)
        if not match:
            out.append(line)
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        title = re.sub(rf"^{re.escape(str(number))}(\.\d+)*\.?\s*", "", title)
        if level == 2:
            out.append(f"## {number}. {title}")
            counters = {3: 0, 4: 0, 5: 0}
        elif level in counters:
            counters[level] += 1
            for parent in range(3, level):
                if counters[parent] == 0:
                    counters[parent] = 1
            prefix = str(number) + "".join(
                f".{counters[i]}" for i in range(3, level + 1)
            )
            out.append(f"{'#' * level} {prefix} {title}")
        else:
            out.append(line)
    return "\n".join(out)


def _iter_blocks(text: str) -> list[tuple[int, int, str]]:
    blocks: list[tuple[int, int, str]] = []
    lines = text.split("\n")
    start = None
    buf: list[str] = []

    def flush(end_line: int) -> None:
        nonlocal start, buf
        if start is not None and any(b.strip() for b in buf):
            blocks.append((start, end_line, "\n".join(buf)))
        start = None
        buf = []

    for i, line in enumerate(lines):
        if not line.strip():
            flush(i)
        else:
            if start is None:
                start = i
            buf.append(line)
    flush(len(lines))
    return blocks


def _has_citation(block: str, registry: dict[str, dict]) -> bool:
    codes = [_norm(e["code"]) for e in registry.values()]
    for match in re.finditer(r"\(([^()]{3,80})\)", block):
        key = _norm(match.group(1))
        for code in codes:
            if key == code or key.startswith(code + " ") or code.startswith(key + " "):
                return True
    return bool(re.search(r"\[[^\]]+\]\(https?://", block))


def validate_section(
    text: str, section: dict, registry: dict[str, dict], min_charts: int
) -> list[str]:
    violations: list[str] = []
    body = re.sub(r"^##\s.*$", "", text, flags=re.MULTILINE)

    for phrase in BANNED_RE.finditer(text):
        violations.append(f"frase proibida: ...{phrase.group(0)[:60]}...")

    charts = count_graficos(text)
    if charts < min_charts:
        violations.append(f"minimo de {min_charts} bloco(s) grafico(s); encontrados: {charts}")
    for raw in GRAFICO_RE.findall(text):
        try:
            spec = parse_spec(raw)
        except ChartError as exc:
            violations.append(f"grafico invalido: {exc}")
            continue
        fonte = spec.get("fonte", "")
        if fonte:
            key = _norm(fonte)
            known = {_norm(e["code"]) for e in registry.values()}
            if key not in known and not any(k.startswith(key) or key.startswith(k) for k in known):
                violations.append(f"grafico com fonte desconhecida: {fonte!r}")
        else:
            violations.append(f"grafico sem fonte: {spec['titulo']!r}")
        if spec["tipo"] in MATH_TYPES and not spec.get("unidade"):
            violations.append(
                f"grafico {spec['tipo']} sem a unidade obrigatoria: {spec['titulo']!r}"
            )
        if spec["tipo"] in MATH_TYPES and not spec.get("nota"):
            violations.append(
                f"grafico {spec['tipo']} sem a nota de interpretacao (campo 'nota'): "
                f"{spec['titulo']!r}"
            )
        if spec["tipo"] == "linha":
            rotulos_linha = [str(r) for r in spec.get("rotulos", [])]
            pontos = len(spec.get("series", [{}])[0].get("valores", spec.get("valores", []))) \
                if spec.get("series") else len(spec.get("valores", []))
            eh_anual = len(rotulos_linha) >= 4 and all(
                re.fullmatch(r"(19|20)\d{2}", r) for r in rotulos_linha
            )
            if eh_anual and pontos < 8 and min_charts >= 8:
                violations.append(
                    f"grafico de linha com serie temporal curta ({pontos} anos): "
                    f"{spec['titulo']!r} (use a serie historica longa completa do contexto)"
                )
            elif pontos < 4:
                violations.append(
                    f"grafico de linha com menos de 4 pontos: {spec['titulo']!r}"
                )
        if spec["tipo"] in ("pizza", "donut") and len(spec.get("valores", [])) < 2:
            violations.append(
                f"grafico {spec['tipo']} com menos de 2 valores: {spec['titulo']!r} "
                "(use comparativo, tabela ou texto para unico valor)"
            )
        if spec["tipo"] == "comparativo":
            for col in spec.get("colunas", []):
                for item in col.get("itens", []):
                    raw = str(item).strip()
                    eh_ano = bool(re.fullmatch(r"(19|20)\d{2}", raw))
                    if re.fullmatch(r"\d{4,}", raw) and not eh_ano:
                        violations.append(
                            f"item numerico sem formatacao no comparativo {spec['titulo']!r}: "
                            f"{item!r} (escreva '1,2 milhao', '102,5 milhoes' etc.)"
                        )
        if spec["tipo"] in ("barra", "hbar", "barra_empilhada", "barra_agrupada") and len(spec.get("valores", [])) < 2:
            violations.append(
                f"grafico {spec['tipo']} com menos de 2 valores: {spec['titulo']!r} "
                "(use comparativo, tabela ou texto para unico valor)"
            )
        if spec["tipo"] in MATH_TYPES:
            rotulos = [str(r).lower() for r in spec.get("rotulos", [])]
            dia = any(re.search(r"\b(diari|/ dia|\bpor dia\b|\bdia\b)", r) for r in rotulos)
            semana = any(re.search(r"\b(seman|/ semana|\bpor semana\b)", r) for r in rotulos)
            if dia and semana:
                violations.append(
                    f"grafico {spec['titulo']!r} mistura unidade diaria e semanal "
                    "(proibido pela regra das unidades)"
                )

    if "Em termos simples" not in text:
        violations.append("bloco '**Em termos simples:**' ausente")

    modalities = _modality_queries(section)
    if modalities:
        low = text.lower()
        low_flat = _norm(low)
        if low.count("direit") < len(modalities):
            violations.append(
                f"bloco 'Direitos' ausente em algumas modalidades "
                f"(encontrado {low.count('direit')}x, esperado >= {len(modalities)})"
            )
        if low.count("dever") < len(modalities):
            violations.append(
                f"bloco 'Deveres' ausente em algumas modalidades "
                f"(encontrado {low.count('dever')}x, esperado >= {len(modalities)})"
            )
        for modality in modalities:
            head = _norm(modality.split("(")[0])
            if head and head[:25] not in low_flat:
                violations.append(f"modalidade ausente no texto: {modality}")

    in_fence = False
    blocks = _iter_blocks(body)
    for bidx, block in enumerate(blocks):
        first = block[2].split("\n", 1)[0].strip()
        if first.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or first.startswith("```"):
            continue
        if not first or first in ("---", "***"):
            continue
        if first.startswith("#") or first.startswith("![") or first.startswith("<figure"):
            continue
        if re.match(r"^\**\s*Fontes consultadas", first, re.IGNORECASE):
            continue
        if first.startswith("![") or first.startswith('<div class="diagrama'):
            continue
        lines = [ln for ln in block[2].split("\n") if ln.strip()]
        if all(ln.lstrip().startswith("|") for ln in lines):
            for row in lines:
                stripped = row.strip()
                if set(stripped) <= set("|-: "):
                    continue
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                headerish = cells and all(
                    c.lower().rstrip(":") in ("direitos", "direitos/garantias do prestador", "deveres")
                    or "direito" == c.lower().rstrip(":")
                    or "deveres" == c.lower().rstrip(":")
                    or set(c.lower()) <= set("direitos/garantiasdoprestaç") and len(c) < 40
                    for c in cells[:1]
                )
                if not _has_citation(row, registry):
                    if cells and cells[0].lower() in (
                        "direitos",
                        "direitos do trabalhador",
                        "direitos/garantias do prestador",
                        "deveres (trabalhador e contratante)",
                        "deveres (prestador e contratante)",
                        "item",
                        "direito",
                        "garantia",
                    ):
                        continue
                    if headerish:
                        continue
                    if re.search(r"(Modalidade|Indicador|Ano|Per[íi]odo|Proposi[çc][ãa]o|Norma|Categoria)", cells[0] if cells else ""):
                        continue
                    violations.append(f"linha de tabela sem citacao: {stripped[:80]}")
            continue
        if re.match(r"^(TABELA:)", first):
            continue
        if re.match(r"^(\s*([-*+]|\d+\.)\s+)", block[2].split("\n")[0]):
            for item in lines:
                if re.match(r"^\s*([-*+]|\d+\.)\s+", item) and not _has_citation(item, registry):
                    if re.match(r"^\s*([-*+]|\d+\.)\s+(\*\*)?(Direitos|Deveres)", item):
                        continue
                    violations.append(f"item de lista sem citacao: {item.strip()[:80]}")
            continue
        nxt = blocks[bidx + 1][2].lstrip() if bidx + 1 < len(blocks) else ""
        if nxt.startswith(
            (
                '<figure',
                '<div class="diagrama',
                '<div class="tabela-legenda',
                "![",
                "|",
                "- ",
                "* ",
                "+ ",
            )
        ):
            continue
        if not _has_citation(block[2], registry):
            violations.append(f"paragrafo sem citacao: {first[:80]}")

    tail = [ln.strip() for ln in body.split("\n") if ln.strip()]
    if tail:
        last = tail[-1]
        if _is_incomplete_prose(last):
            violations.append(f"texto truncado no final da secao: {last[:80]}")
    return violations


TERMINAL_RE = re.compile(r"[.!?:;)\]\"'\u2019\u201d\u2026]$")


def _is_incomplete_prose(line: str) -> bool:
    if re.match(r"^(#{1,6}\s|!|\||<|\*|-|\+|\d+[.)])", line):
        return False
    return not TERMINAL_RE.search(line)


def repair_section(
    text: str, registry: dict[str, dict], fallback_code: str
) -> tuple[str, int]:
    removed = 0
    lines = text.split("\n")
    cleaned: list[str] = []
    in_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            cleaned.append(line)
            continue
        if not in_fence and BANNED_RE.search(line):
            removed += 1
            continue
        cleaned.append(line)
    text = "\n".join(cleaned)
    text = re.sub(r"\n{3,}", "\n\n", text)

    tail_lines = text.split("\n")
    while tail_lines and not tail_lines[-1].strip():
        tail_lines.pop()
    if tail_lines and _is_incomplete_prose(tail_lines[-1].strip()):
        tail_lines.pop()
    text = "\n".join(tail_lines)

    out: list[str] = []
    in_fence = False
    for block_text in text.split("\n\n"):
        lines = block_text.split("\n")
        first = lines[0].strip() if lines else ""
        if first.startswith("```"):
            in_fence = not in_fence
            out.append(block_text)
            continue
        if in_fence or not first or first.startswith("#") or first.startswith("![") or first.startswith("<"):
            out.append(block_text)
            continue
        if re.match(r"^\**\s*Fontes consultadas", first, re.IGNORECASE) or first.startswith("TABELA:"):
            out.append(block_text)
            continue
        if all(ln.strip().startswith("|") for ln in lines if ln.strip()):
            fixed_rows = []
            for row in lines:
                stripped = row.strip()
                if not stripped.startswith("|") or set(stripped) <= set("|-: ") or _has_citation(row, registry):
                    fixed_rows.append(row)
                    continue
                if stripped.endswith("|"):
                    fixed_rows.append(stripped[:-1].rstrip() + f" ({fallback_code}) |")
                else:
                    fixed_rows.append(row + f" ({fallback_code})")
            out.append("\n".join(fixed_rows))
            continue
        if re.match(r"^\s*([-*+]|\d+\.)\s+", first):
            fixed_items = []
            for item in lines:
                if re.match(r"^\s*([-*+]|\d+\.)\s+", item) and not _has_citation(item, registry):
                    fixed_items.append(item.rstrip() + f" ({fallback_code})")
                else:
                    fixed_items.append(item)
            out.append("\n".join(fixed_items))
            continue
        if not _has_citation(block_text, registry):
            block_text = block_text.rstrip() + f" ({fallback_code})"
        out.append(block_text)
    return "\n\n".join(out), removed


def _render_charts(text: str, renderer: ChartRenderer) -> str:

    def replace(match: re.Match) -> str:
        try:
            spec = parse_spec(match.group(1))
            return renderer.render(spec)
        except ChartError as exc:
            print(f"[relatorio] grafico descartado ({exc})")
            return ""

    return GRAFICO_RE.sub(replace, text)


def _render_table_captions(text: str, renderer: ChartRenderer) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        match = TABLE_DIRECTIVE_RE.match(lines[i].strip())
        if not match:
            out.append(lines[i])
            i += 1
            continue
        titulo, fonte = match.group(1), match.group(2)
        m_parens = re.fullmatch(r"\((.+)\)", fonte.strip())
        if m_parens:
            fonte = m_parens.group(1).strip()
        number = renderer.next_table_number()
        while i + 1 < len(lines) and not lines[i + 1].strip().startswith("|"):
            i += 1
        out.append(
            f'<div class="tabela-legenda"><strong>Tabela {number} \u2013 {titulo}.</strong> '
            f"<em>Fonte: ({fonte}).</em></div>"
        )
        i += 1
    return "\n".join(out)


def _tidy_markdown(text: str) -> str:
    text = re.sub(
        r"\(\((\[[^\]]+\]\([^)]+\)|[^\n()]{3,80})\)\)", r"(\1)", text
    )
    _link = r"\[[^\]]+\]\(https?://[^)]+\)"
    _chain = rf"{_link}(?:\s*[e,]\s*{_link})*"
    text = re.sub(rf"Fonte: \(({_chain})\)\.", r"Fonte: \1.", text)
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and re.match(r"^---\s*([\[(].*)?$", line):
            continue
        out.append(line)
    text = "\n".join(out)
    lines = text.split("\n")
    out = []
    in_fence = False
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        if (
            not in_fence
            and re.match(r"^#{1,6}\s", line)
            and out
            and out[-1].strip()
        ):
            out.append("")
        out.append(line)
    return "\n".join(out)


def generate_section(
    settings: Settings,
    section: dict,
    index: SearchIndex,
    client: OllamaClient,
    full_registry: dict[str, dict] | None = None,
) -> tuple[str, list[dict]]:
    chunks = retrieve_for_section(section, index, settings)
    context = _format_context(chunks) if chunks else "Nenhum documento encontrado."
    registry = full_registry or build_citation_registry(chunks)
    prompt_registry = {
        _public_url(c["url"]): registry[_public_url(c["url"])]
        for c in chunks
        if _public_url(c["url"]) in registry
    }
    defaults = settings.outline.get("defaults", {})
    min_charts = section.get("charts", defaults.get("charts", 1))
    max_words = section.get("words", (0, defaults.get("max_words", 900)))[1]
    factor = 3.6 if section.get("structure") else 2.4
    fallback_code = _pick_fallback_code(section, registry)

    text = ""
    violations: list[str] = []
    for attempt in range(3):
        prompt = _section_prompt(
            section, context, defaults, prompt_registry, feedback=_format_feedback(violations)
        )
        text = client.chat_with_retry(
            prompt,
            system=SYSTEM_PROMPT,
            max_tokens=min(8000, max(5600, int(max_words * factor) + 400 * min_charts + 600)),
            temperature=0.2,
        )
        text = _clean_section(text, section)
        violations = validate_section(text, section, registry, min_charts)
        if not violations:
            break
    else:
        text, removed = repair_section(text, registry, fallback_code)
        if removed:
            print(f"[relatorio] secao {section['number']}: removidas {removed} linha(s) com frases proibidas")
        still = validate_section(text, section, registry, min_charts)
        if still:
            print(
                f"[relatorio] secao {section['number']}: {len(still)} aviso(s) residual(is): "
                + "; ".join(still[:3])
            )
    text = _renumber_headings(text, section["number"])
    return text, chunks


def _pick_fallback_code(section: dict, registry: dict[str, dict]) -> str:
    wanted = [str(s).lower() for s in section.get("must_include", [])]
    for source_id in wanted:
        for entry in registry.values():
            if entry["source_id"] == source_id and entry["priority"] >= 2:
                return entry["code"]
    for entry in sorted(registry.values(), key=lambda e: -e["priority"]):
        if entry["priority"] >= 2:
            return entry["code"]
    if registry:
        return next(iter(registry.values()))["code"]
    return "BRASIL, 1943"


def _format_feedback(violations: list[str]) -> str:
    if not violations:
        return ""
    return "\n".join(f"- {v}" for v in violations[:20])


def _nbr_reference(entry: dict) -> str:
    author = re.split(r",\s*", entry["code"], maxsplit=1)[0]
    title = str(entry["title"])
    m = re.match(
        rf"^{re.escape(author)}(?:\s*/\s*[\w ]+)?\s*[-–—:]\s*(.+)$",
        title,
        re.IGNORECASE,
    )
    if m and m.group(1).strip():
        title = m.group(1).strip()
        title = title[0].upper() + title[1:]
    return f"{author}. {title}. Disponível em: {entry['url']}. Acesso em: {_access_date(entry['date'])}."


def _references_section(sources: dict[str, dict]) -> str:
    lines = [
        "## Referências",
        "",
        "Todas as fontes citadas neste relatório, em formato ABNT (NBR 6023):",
        "",
    ]
    ordered = sorted(sources.values(), key=lambda s: _norm(_nbr_reference(s)))
    for entry in ordered:
        lines.append(f"* {_nbr_reference(entry)}")
    lines.append("")
    return "\n".join(lines)


def _cover_html(outline: dict) -> str:
    year = datetime.date.today().year
    inst = str(outline.get("institution", "")).strip()
    genero = str(outline.get("genre", "")).strip().upper()
    partes = ['<div class="capa">']
    if genero:
        partes.append(f'<p class="capa-genero">{genero}</p>')
    if inst:
        partes.append(f'<p class="capa-instituicao">{inst}</p>')
    partes += [
        f'<p class="capa-autor">{outline.get("author", "")}</p>',
        f'<h1 class="capa-titulo">{outline["title"]}</h1>',
        f'<p class="capa-subtitulo">{outline.get("subtitle", "")}</p>',
        '<div class="capa-espaco"></div>',
        f'<p class="capa-local">{outline.get("place", "Brasil")}</p>',
        f"<p class=\"capa-data\">{year}</p>",
        "</div>",
    ]
    return "".join(partes)


def _generate_resumo(client: OllamaClient, sections: list[dict]) -> str:
    listing = "\n".join(f"- {s['number']}. {s['title']}: {str(s.get('brief',''))[:120]}" for s in sections)
    prompt = (
        "Gere o Resumo do relatorio abaixo em portugues do Brasil, entre 180 e 250 palavras, "
        "em um unico paragrafo, tom objetivo, terminando com a linha "
        "'Palavras-chave: ' seguida de 5 palavras-chave separadas por ponto e virgula. "
        "Nao escreva frases como 'nao ha dados suficientes'. Nao use citacoes.\n\n"
        f"Titulo: Trabalho no Brasil 2026\nSumário com os temas:\n{listing}"
    )
    try:
        text = client.chat_with_retry(prompt, system=SYSTEM_PROMPT, max_tokens=900, temperature=0.2)
    except Exception:
        return ""
    text = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", text.strip())
    return text.strip()


def generate_appendix(client: OllamaClient, registry: dict[str, dict]) -> str:
    norms = [
        e for e in registry.values()
        if e["priority"] >= 3 and e["source_id"] not in ("camara-proposicoes", "senado-materias")
    ]
    if not norms:
        return ""
    listing = "\n".join(f"- ({e['code']}) {e['title']} | {e['url']}" for e in sorted(norms, key=lambda x: x["code"]))
    prompt = (
        "Escreva o Apêndice A do relatório em Markdown, com cabecalho em nivel 2 exatamente assim: "
        "'## Apêndice A - Legislação citada e o que ela muda na prática'.\n"
        "Crie UMA tabela comparativa com colunas: Norma | O que regula | Quem protege ou obriga | "
        "Onde encontrar. ANTES da tabela, escreva uma linha no formato exato: "
        "TABELA: Legislacao consultada e efeitos praticos | fonte: BRASIL, 1988\n"
        "Use APENAS as normas da lista abaixo, em linguagem simples para leigos. "
        "Cada linha de tabela deve citar o codigo da norma entre parenteses na coluna 'Norma'. "
        "Nao escreva frases de falta de dados. Nao invente normas fora da lista.\n\n"
        f"Normas disponiveis:\n{listing}"
    )
    try:
        text = client.chat_with_retry(prompt, system=SYSTEM_PROMPT, max_tokens=3500, temperature=0.2)
    except Exception:
        return ""
    return re.sub(r"^```(?:markdown)?\s*|\s*```$", "", text.strip())


def audit_report(md_text: str, outline: dict) -> dict:
    stats = {
        "paragrafos_sem_citacao": 0,
        "frases_proibidas": len(BANNED_RE.findall(md_text)),
        "graficos_bloco": count_graficos(md_text),
        "graficos_figuras": len(re.findall(r'<figure class="figura">', md_text)),
        "tabelas_legenda": len(re.findall(r"Tabela \d+ \u2013", md_text)),
        "tem_capa": '<div class="capa">' in md_text,
        "tem_resumo": bool(re.search(r"^##\s+Resumo", md_text, re.MULTILINE)),
        "tem_referencias": bool(re.search(r"^##\s+Refer[eê]ncias", md_text, re.MULTILINE)),
        "citacoes": len(re.findall(r"\[\([^]]{3,80}\)\]\(https?://", md_text)),
    }
    stats["graficos"] = stats["graficos_bloco"] + stats["graficos_figuras"]
    sumario = re.search(r"^##\s+Sum[aá]rio", md_text, re.MULTILINE)
    scan_from = sumario.end() if sumario else 0
    body = re.sub(r"^##\s.*$", "", md_text[scan_from:], flags=re.MULTILINE)
    in_fence = False
    blocks = _iter_blocks(body)
    for idx, block in enumerate(blocks):
        first = block[2].split("\n", 1)[0].strip()
        if first.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not first or first.startswith(("#", "![", "<", "|", "TABELA:", "- [", "* [")):
            continue
        if first in ("---", "***", "--"):
            continue
        if re.match(r"^\**\s*Fontes consultadas", first, re.IGNORECASE):
            continue
        if re.match(r"^(Palavras-chave|\*Fim do relatorio)", first, re.IGNORECASE):
            continue
        if re.match(r"^\s*([-*+]|\d+\.)\s+", first):
            continue
        nxt = blocks[idx + 1][2].lstrip() if idx + 1 < len(blocks) else ""
        if nxt.startswith(
            (
                '<figure',
                '<div class="diagrama',
                '<div class="tabela-legenda',
                "![",
                "|",
                "- ",
                "* ",
                "+ ",
            )
        ):
            continue
        if re.match(r"^\d+\.\s", nxt):
            continue
        if not re.search(r"\([\wÀ-ÿ][^()]{2,80}\)", block[2]) and "Disponível em" not in block[2]:
            stats["paragrafos_sem_citacao"] += 1
    min_charts = sum(int(s.get("charts", 1)) for s in outline.get("sections", []))
    min_charts += int((outline.get("annex") or {}).get("charts", 0))
    stats["graficos_minimos_esperados"] = min_charts
    return stats


PREFERRED_REPORT_STEM = "relatorio-trabalho-brasil-2026-10-16"


def report_base_name(output_dir) -> str:
    if (output_dir / f"{PREFERRED_REPORT_STEM}.md").exists():
        return PREFERRED_REPORT_STEM
    return f"relatorio-trabalho-brasil-{datetime.date.today().isoformat()}"


def generate_report(
    settings: Settings,
    client: OllamaClient,
    log=print,
    sections_filter: list[str] | None = None,
) -> str:
    index = build_index(settings)
    outline = settings.outline
    today = datetime.date.today()
    renderer = ChartRenderer(settings.output_dir / "assets")
    full_registry = build_citation_registry(index.chunks)

    header = [
        _cover_html(outline),
        "",
        "## Resumo",
        "",
        "_Resumo gerado apos a montagem das secoes._",
        "",
        "---",
        "",
        "## Sumário",
        "",
    ]
    body: list[str] = []
    used_sources: dict[str, dict] = {}

    def register(chunks: list[dict], text: str) -> None:
        linked = collect_used_sources(text)
        for _, url in linked:
            entry = full_registry.get(url)
            if entry:
                used_sources.setdefault(url, dict(entry))
        for c in chunks:
            entry = used_sources.get(c["url"])
            if entry is None:
                continue
            if not entry.get("date"):
                entry["date"] = c.get("collected_at", "")
            if len(c["title"]) < len(entry["title"]):
                entry["title"] = c["title"]

    sections_done: list[dict] = []
    for section in outline["sections"]:
        if sections_filter and section["id"] not in sections_filter:
            continue
        log(f"[relatorio] secao {section['number']}: {section['title']}")
        try:
            text, chunks = generate_section(settings, section, index, client, full_registry)
        except Exception as exc:
            log(f"[relatorio] ERRO na secao {section['number']}: {exc}")
            text = (
                f"## {section['number']}. {section['title']}\n\n"
                f"_Secao nao disponivel no momento (falha na geracao: {exc})._"
            )
            chunks = []
        text = _render_charts(text, renderer)
        text = _render_table_captions(text, renderer)
        text = link_citations(text, full_registry)
        text = normalize_source_list(text)
        register(chunks, text)
        body.append(text)
        sections_done.append(section)
        header.append(
            f"{section['number']}. [{section['title']}]"
            f"(#{_anchor(section['number'], section['title'])})"
        )

    appendix_conf = outline.get("appendix") or {}
    if appendix_conf and not sections_filter:
        log("[relatorio] apendice A: legislacao explicada")
        try:
            appendix = generate_appendix(client, full_registry)
            if appendix:
                appendix = _render_table_captions(appendix, renderer)
                appendix = link_citations(appendix, full_registry)
                register([], appendix)
                body.append(appendix)
                header.append(f"A. [{appendix_conf.get('title', 'Apêndice A')}]")
        except Exception as exc:
            log(f"[relatorio] ERRO no apendice: {exc}")

    if not sections_filter:
        log("[relatorio] gerando resumo")
        resumo = _generate_resumo(client, sections_done or outline["sections"])
        if resumo:
            for i, line in enumerate(header):
                if line == "_Resumo gerado apos a montagem das secoes._":
                    header[i] = resumo
                    break

    if used_sources:
        header.append(f"R. [Referências](#{_anchor(0, 'referências')})")
    footer = [""]
    if used_sources:
        footer.append(_references_section(used_sources))
    footer.append("")
    report = "\n".join(header + [""] + body + footer)
    if not sections_filter:
        try:
            report, _n_anexo = build_charts_annex(settings, report, log)
        except Exception as exc:
            log(f"[relatorio] ERRO no anexo: {exc}")
    report = _tidy_markdown(report)
    stats = audit_report(report, outline)
    log(
        "[relatorio] auditoria: "
        f"{stats['citacoes']} citacoes linkadas, {stats['graficos']} graficos, "
        f"{stats['tabelas_legenda']} tabelas, {stats['frases_proibidas']} frases proibidas, "
        f"{stats['paragrafos_sem_citacao']} paragrafo(s) sem citacao, "
        f"capa={stats['tem_capa']}, resumo={stats['tem_resumo']}, referencias={stats['tem_referencias']}"
    )
    return report


def _anchor(number: int, title: str) -> str:
    text = title.lower() if number == 0 else f"{number} {title}".lower()
    keep = "".join(
        ch for ch in text if unicodedata.category(ch)[0] not in ("P", "S") or ch == "-"
    )
    slug = re.sub(r"\s+", "-", keep).strip("-")
    return slug
