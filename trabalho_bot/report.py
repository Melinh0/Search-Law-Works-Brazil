"""Geracao do relatorio Markdown por secoes com base em recuperacao documental."""

from __future__ import annotations

import datetime
import re
import unicodedata

from .config import Settings
from .llm import OllamaClient
from .rag import SearchIndex, build_index

SYSTEM_PROMPT = (
    "Voce e um analista juridico brasileiro especializado em direito do trabalho. "
    "Escreva em portugues do Brasil, tom objetivo e direto, paragrafos curtos, "
    "sem advogar em causa propria e sem inventar leis ou artigos inexistentes. "
    "Cite expressoes legais apenas quando aparecerem no contexto fornecido. "
    "Quando citar uma fonte do contexto, use a notacao [Fonte: titulo exato "
    "do documento no contexto]."
)

CITATION_RE = re.compile(r"\[Fonte:\s*([^\]]+)\]")
BARE_URL_RE = re.compile(r"(?<!\()https?://[^\s)\]]+")


def _modality_queries(section: dict) -> list[str]:
    """Retorna a lista de modalidades do outline usada como queries de busca focada."""
    return [str(item).strip() for item in section.get("modalities", []) if str(item).strip()]


def _search_per_modality(section: dict, index: SearchIndex, per_query: int = 4) -> list[dict]:
    """Faz uma busca focada por modalidade e merge os resultados sem duplicatas."""
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for query in _modality_queries(section):
        for c in index.search(query, top_k=per_query):
            key = (c["source_id"], c["text"][:60])
            if key not in seen:
                seen.add(key)
                merged.append(c)
    return merged


def _with_state_enforcement_context(chunks: list[dict], index: SearchIndex) -> list[dict]:
    """Garante fontes de fiscalizacao e do Estado no contexto das secoes estruturadas."""
    have = {c["source_id"] for c in chunks}
    keep = {"mte-fiscalizacao", "auditoria-fiscal"}
    extra: list[dict] = []
    for source_id in keep - have:
        found = [c for c in index.chunks if c["source_id"] == source_id]
        extra.extend(found[:2])
    return chunks + extra


def _format_context(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"--- Contexto {i} | {c['title']} | {c['url']} | {c['collected_at']}\n{c['text']}"
        )
    return "\n\n".join(parts)


def _section_prompt(section: dict, context: str, defaults: dict) -> str:
    min_words, max_words = section.get(
        "words", (defaults.get("min_words", 400), defaults.get("max_words", 900))
    )
    structure = section.get("structure") or []
    modality_lines = "\n".join(f"- {m}" for m in section.get("modalities", []))
    modalities_block = ""
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
    return (
        f"Secao {section['number']}: {section['title']}\n\n"
        f"Objetivo da secao:\n{section['brief'].strip()}\n\n"
        f"{modalities_block}"
        "Contexto documental coletado de fontes oficiais:\n"
        f"{context}\n\n"
        "Escreva esta secao do relatorio em Markdown. Requisitos:\n"
        "1. Cabecalho em nivel 2 com o titulo exato da secao.\n"
        f"2. Entre {min_words} e {max_words} palavras, objetivas e faceis de ler.\n"
        "3. Use listas e tabelas Markdown quando ajudar a comparar.\n"
        "4. Use apenas informacoes presentes no contexto; se o contexto for "
        "insuficiente, diga explicitamente que nao ha dado suficiente na base.\n"
        "5. Cite cada afirmacao relevante com a notacao [Fonte: titulo exato "
        "do documento].\n"
        + structure_block
        + "6. Termine com uma lista 'Fontes consultadas:' em Markdown no formato "
        "'* [titulo](url)' para cada fonte do contexto que voce efetivamente usou. "
        "Use os titulos e URLs exatamente como aparecem no contexto.\n"
        "7. Nao repita o cabecalho de nenhuma outra secao."
    )


def _norm(text: str) -> str:
    """Normaliza texto para comparacao de titulos sem distincao de acentos/case."""
    stripped = unicodedata.normalize("NFKD", text.lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(ch for ch in stripped if not unicodedata.combining(ch))).strip()


def build_citation_map(chunks: list[dict]) -> dict[str, str]:
    """Mapeia titulos (exatos e variantes) de fontes para suas URLs."""
    mapping: dict[str, str] = {}
    for c in chunks:
        title = c["title"].strip()
        mapping[_norm(title)] = c["url"]
        short = re.split(r"\s+-\s+", title)[0]
        if len(short) >= 12:
            mapping.setdefault(_norm(short), c["url"])
    return mapping


def link_citations(text: str, citation_map: dict[str, str]) -> str:
    """Converte citacoes [Fonte: titulo] em links Markdown clicaveis."""
    def replace(match: re.Match) -> str:
        cited = match.group(1).strip()
        key = _norm(cited)
        url = citation_map.get(key)
        if url is None:
            for known_key, known_url in citation_map.items():
                if key and (key in known_key or known_key in key):
                    url = known_url
                    break
        if url:
            return f"[Fonte: {cited}]({url})"
        return match.group(0)

    return CITATION_RE.sub(replace, text)


def normalize_source_list(text: str) -> str:
    """Converte linhas soltas 'titulo: url' no bloco Fontes consultadas para links."""
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
            elif line.strip().startswith("*") and BARE_URL_RE.search(line):
                bullet = re.sub(r"^(\s*\*\s*)", "", line)
                url_match = BARE_URL_RE.search(bullet)
                if url_match:
                    url = url_match.group(0)
                    title = bullet.replace(url, "").strip().rstrip(":").strip()
                    title = re.sub(r"^\[|\]$", "", title)
                    if title:
                        line = f"* [{title}]({url})"
        out.append(line)
    return "\n".join(out)


def collect_used_sources(text: str) -> list[tuple[str, str]]:
    """Extrai pares (titulo, url) das citacoes e listas de fontes ja linkadas."""
    sources: list[tuple[str, str]] = []
    for match in re.finditer(r"\[Fonte:\s*([^\]]+)\]\((https?://[^)]+)\)", text):
        sources.append((match.group(1).strip(), match.group(2).strip()))
    for match in re.finditer(r"^\*\s*\[([^\]]+)\]\((https?://[^)]+)\)", text, flags=re.MULTILINE):
        sources.append((match.group(1).strip(), match.group(2).strip()))
    return sources


def generate_section(
    settings: Settings,
    section: dict,
    index: SearchIndex,
    client: OllamaClient,
    top_k: int = 10,
) -> tuple[str, list[dict]]:
    """Recupera contexto relevante, redige a secao e devolve texto e fontes usadas."""
    query = f"{section['title']} {section['brief']}"
    if section.get("structure"):
        chunks = _search_per_modality(section, index)
        chunks = _with_state_enforcement_context(chunks, index)
    else:
        chunks = index.search(query, top_k=top_k)
    context = _format_context(chunks) if chunks else "Nenhum documento encontrado."
    defaults = settings.outline.get("defaults", {})
    prompt = _section_prompt(section, context, defaults)
    max_words = section.get("words", (0, defaults.get("max_words", 900)))[1]
    factor = 3.0 if section.get("structure") else 2.2
    text = client.chat_with_retry(
        prompt,
        system=SYSTEM_PROMPT,
        max_tokens=max(3500, int(max_words * factor)),
        temperature=0.2,
    )
    text = _clean_section(text, section)
    citation_map = build_citation_map(chunks)
    text = link_citations(text, citation_map)
    text = normalize_source_list(text)
    return text, chunks


def _clean_section(text: str, section: dict) -> str:
    """Normaliza cabecalho e remove artefatos de resposta do modelo."""
    text = re.sub(r"^```(?:markdown)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    heading = f"## {section['number']}. {section['title']}"
    if not text.startswith("#"):
        text = f"{heading}\n\n{text}"
    else:
        text = re.sub(r"^#+\s*.*$", heading, text, count=1, flags=re.MULTILINE)
    return text.strip()


def _references_section(sources: dict[str, dict]) -> str:
    """Monta a secao final de referencias com todas as fontes usadas, ordenadas."""
    lines = ["## Referencias", "", "Todas as fontes citadas neste relatorio, com link de acesso:", ""]
    ordered = sorted(sources.values(), key=lambda s: _norm(s["title"]))
    for i, src in enumerate(ordered, 1):
        date = f" (coletado em {src['date']})" if src.get("date") else ""
        lines.append(f"{i}. [{src['title']}]({src['url']}){date}")
    lines.append("")
    return "\n".join(lines)


def generate_report(
    settings: Settings,
    client: OllamaClient,
    log=print,
    sections_filter: list[str] | None = None,
) -> str:
    """Gera o relatorio completo em Markdown seguindo o outline configurado."""
    index = build_index(settings)
    outline = settings.outline
    today = datetime.date.today().strftime("%d/%m/%Y")

    header = [
        f"# {outline['title']}",
        "",
        f"*{outline['subtitle']}*",
        "",
        f"Data de geracao: {today} | Gerado automaticamente por Search-Law-Works-Brazil",
        "",
        "> Documento de carater informativo, elaborado a partir de fontes publicas "
        "coletadas automaticamente. Nao substitui orientacao juridica profissional. "
        "Toda afirmacao acompanha citacao clicavel da fonte e a lista completa de "
        "referencias esta ao final do documento.",
        "",
        "---",
        "",
        "## Sumario",
        "",
    ]
    body: list[str] = []
    used_sources: dict[str, dict] = {}

    def register(chunks: list[dict], text: str) -> None:
        linked = collect_used_sources(text)
        for title, url in linked:
            used_sources.setdefault(url, {"title": title, "url": url, "date": ""})
        for c in chunks:
            entry = used_sources.get(c["url"])
            if entry is None:
                continue
            if not entry.get("date"):
                entry["date"] = c.get("collected_at", "")
            if len(c["title"]) < len(entry["title"]):
                entry["title"] = c["title"]

    for section in outline["sections"]:
        if sections_filter and section["id"] not in sections_filter:
            continue
        log(f"[relatorio] secao {section['number']}: {section['title']}")
        try:
            text, chunks = generate_section(settings, section, index, client)
            register(chunks, text)
        except Exception as exc:
            log(f"[relatorio] ERRO na secao {section['number']}: {exc}")
            text = (
                f"## {section['number']}. {section['title']}\n\n"
                f"_Secao nao disponivel no momento (falha na geracao: {exc})._"
            )
        body.append(text)
        header.append(
            f"{section['number']}. [{section['title']}]"
            f"(#{_anchor(section['number'], section['title'])})"
        )

    if used_sources:
        header.append(f"R. [Referencias](#{_anchor(0, 'referencias')})")
    footer = ["", "---", ""]
    if used_sources:
        footer.append(_references_section(used_sources))
    footer += ["*Fim do relatorio.*", ""]
    return "\n".join(header + [""] + body + footer)


def _anchor(number: int, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{number} {title}".lower()).strip("-")
    return slug
