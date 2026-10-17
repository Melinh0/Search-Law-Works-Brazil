from __future__ import annotations

import hashlib
import html as html_module
import json
import re
import time
import urllib.parse
import warnings
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..config import Settings

warnings.filterwarnings("ignore", message="Unverified HTTPS request")


@dataclass
class Document:

    source_id: str
    title: str
    url: str
    text: str
    tags: list[str]
    collected_at: str
    priority: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


def _get(url: str, settings: Settings, **kwargs) -> requests.Response:
    headers = {"User-Agent": settings.user_agent}
    try:
        return requests.get(url, headers=headers, timeout=settings.timeout, **kwargs)
    except requests.exceptions.SSLError:
        return requests.get(
            url, headers=headers, timeout=settings.timeout, verify=False, **kwargs
        )


def fetch(settings: Settings, url: str, encoding: str = "utf-8", retries: int = 3) -> str:
    cache_path = settings.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace")
    last_error: Exception | None = None
    resp = None
    for attempt in range(retries):
        try:
            resp = _get(url, settings)
            resp.raise_for_status()
            break
        except (requests.RequestException, ConnectionError) as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    else:
        raise ConnectionError(f"falha apos {retries} tentativas: {url}") from last_error
    if resp.encoding and resp.encoding.lower() in ("iso-8859-1", "latin-1", "windows-1252"):
        resp.encoding = encoding
    text = resp.text
    cache_path.write_text(text, encoding="utf-8")
    time.sleep(0.4)
    return text


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def collect_planalto_like(settings: Settings, source: dict) -> list[Document]:
    html = fetch(settings, source["url"], source.get("encoding", "iso-8859-1"))
    text = html_to_text(html)
    if not text.strip():
        return []
    return [
        Document(
            source_id=source["id"],
            title=source["name"],
            url=source["url"],
            text=text,
            tags=source.get("tags", []),
            collected_at=time.strftime("%Y-%m-%d"),
            priority=int(source.get("priority", 3)),
        )
    ]


def collect_camara(settings: Settings, source: dict) -> list[Document]:
    docs: list[Document] = []
    seen: set[int] = set()
    for kw in source.get("keywords", []):
        resp = _get(
            "https://dadosabertos.camara.leg.br/api/v2/proposicoes",
            settings,
            params={"keywords": kw, "itens": 10, "ordem": "DESC", "ordenarPor": "id"},
        )
        if resp.status_code != 200:
            continue
        for item in resp.json().get("dados", []):
            pid = item["id"]
            if pid in seen:
                continue
            seen.add(pid)
            ementa = item.get("ementa") or ""
            if not ementa:
                continue
            uri = item.get("uri") or ""
            detail = _camara_detail(uri, settings) if uri else {}
            parts = [
                f"{item.get('siglaTipo')} {item.get('numero')}/{item.get('ano')}",
                ementa,
                detail.get("situacao", ""),
                " ".join(
                    f"Tramitacao: {x.get('dataHora','')} - {x.get('descricao','')}"
                    for x in detail.get("tramitacoes", [])[:5]
                ),
            ]
            docs.append(
                Document(
                    source_id=source["id"],
                    title=parts[0],
                    url=f"https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={pid}",
                    text="\n".join(p for p in parts if p),
                    tags=source.get("tags", []) + [kw],
                    collected_at=time.strftime("%Y-%m-%d"),
                    priority=int(source.get("priority", 3)),
                )
            )
        time.sleep(0.3)
    return docs


def _camara_detail(uri: str, settings: Settings) -> dict:
    try:
        resp = _get(uri, settings)
        if resp.status_code != 200:
            return {}
        data = resp.json().get("dados", {})
        situacao = (data.get("situacao") or {}).get("descricao", "")
        tram_url = uri.rstrip("/") + "/tramitacoes?itens=5&ordem=DESC&ordenarPor=id"
        tram = _get(tram_url, settings)
        tramitacoes = tram.json().get("dados", []) if tram.status_code == 200 else []
        return {"situacao": f"Situacao: {situacao}", "tramitacoes": tramitacoes}
    except (requests.RequestException, ValueError):
        return {}


def collect_senado(settings: Settings, source: dict) -> list[Document]:
    docs: list[Document] = []
    for kw in source.get("keywords", []):
        try:
            resp = _get(
                "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista",
                settings,
                params={"palavraChave": kw},
            )
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError):
            continue
        for materia in root.findall(".//Materia")[:8]:
            codigo = materia.findtext("Codigo", "")
            sigla = materia.findtext("Identificacao/Sigla", "") or materia.findtext(
                "Sigla", ""
            )
            numero = materia.findtext("Identificacao/Numero", "") or materia.findtext(
                "Numero", ""
            )
            ano = materia.findtext("Identificacao/Ano", "") or materia.findtext("Ano", "")
            ementa = materia.findtext("Ementa", "") or materia.findtext(
                "Materia/DadosBasicos/Ementa", ""
            )
            if not ementa:
                continue
            docs.append(
                Document(
                    source_id=source["id"],
                    title=f"{sigla} {numero}/{ano}".strip(),
                    url=f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{codigo}",
                    text=ementa,
                    tags=source.get("tags", []) + [kw],
                    collected_at=time.strftime("%Y-%m-%d"),
                    priority=int(source.get("priority", 3)),
                )
            )
        time.sleep(0.3)
    return docs


def _clean_rss_description(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _gnews_cache_path(settings: Settings) -> Path:
    return settings.cache_dir / "gnews_resolved.json"


def _load_gnews_cache(settings: Settings) -> dict[str, str]:
    path = _gnews_cache_path(settings)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def _save_gnews_cache(settings: Settings, cache: dict[str, str]) -> None:
    _gnews_cache_path(settings).write_text(
        json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8"
    )


def resolve_google_news_url(settings: Settings, article_id: str) -> str | None:
    cache = _load_gnews_cache(settings)
    if article_id in cache:
        return cache[article_id]
    try:
        page = _get(f"https://news.google.com/rss/articles/{article_id}", settings)
        sig_m = re.search(r'data-n-a-sg="([^"]+)"', page.text)
        ts_m = re.search(r'data-n-a-ts="([^"]+)"', page.text)
        if not (sig_m and ts_m):
            return None
        inner = (
            '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,'
            "null,null,0,1],\"X\",\"X\",1,[1,1,1],1,1,null,0,0,null,0],"
            f'"{article_id}",{ts_m.group(1)},"{sig_m.group(1)}"]'
        )
        payload = {"f.req": json.dumps([[["Fbv4je", inner, None, "generic"]]])}
        resp = requests.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",
            data=payload,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout,
        )
        if resp.status_code != 200:
            return None
        urls = re.findall(r"https?://[^\"\\]+", resp.text)
        external = [u for u in urls if "google" not in u]
        if not external:
            return None
        cache[article_id] = external[0]
        _save_gnews_cache(settings, cache)
        return external[0]
    except (requests.RequestException, ConnectionError, ValueError):
        return None


def _gnews_id_from_link(link: str) -> str | None:
    match = re.search(r"/articles/([^?]+)", link)
    return match.group(1) if match else None


def collect_news_rss(settings: Settings, source: dict) -> list[Document]:
    docs: list[Document] = []
    for query in source.get("queries", []):
        url = (
            "https://news.google.com/rss/search?q="
            + urllib.parse.quote(query)
            + "&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        )
        try:
            resp = _get(url, settings)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError):
            continue
        for item in root.findall(".//item")[:8]:
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub = item.findtext("pubDate", "")
            desc = _clean_rss_description(item.findtext("description", ""))
            if not title:
                continue
            resolved = link
            gid = _gnews_id_from_link(link)
            if gid:
                real = resolve_google_news_url(settings, gid)
                if real:
                    resolved = real
                else:
                    resolved = re.sub(r"\?.*$", "", link)
            docs.append(
                Document(
                    source_id=source["id"],
                    title=title,
                    url=resolved,
                    text=f"{title}\n{desc}\nPublicado: {pub}",
                    tags=source.get("tags", []) + [query],
                    collected_at=time.strftime("%Y-%m-%d"),
                    priority=int(source.get("priority", 1)),
                )
            )
        time.sleep(0.3)
    return docs


def _abstract_from_openalex(work: dict) -> str:
    inverted = work.get("abstract_inverted_index")
    if not isinstance(inverted, dict) or not inverted:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inverted.items():
        for i in idxs:
            positions[i] = word
    words = [positions[i] for i in sorted(positions)]
    return " ".join(words[:80])


def collect_academic(settings: Settings, source: dict) -> list[Document]:
    docs: list[Document] = []
    seen: set[str] = set()
    for query in source.get("queries", []):
        for work in _openalex_search(settings, query, 4) + _crossref_search(settings, query, 3):
            title = re.sub(r"\s+", " ", work.get("title", "")).strip()
            if not title or len(title) < 15:
                continue
            key = re.sub(r"[^a-z0-9]+", "", title.lower())[:60]
            if key in seen:
                continue
            seen.add(key)
            year = work.get("year") or ""
            authors = ", ".join(work.get("authors", [])[:3])
            journal = work.get("journal") or ""
            abstract = work.get("abstract", "")
            parts = [
                f"Artigo academico: {title}",
                f"Autores: {authors}" if authors else "",
                f"Periodico: {journal}" if journal else "",
                f"Ano: {year}" if year else "",
                f"Resumo (trecho): {abstract}" if abstract else "",
                "Fonte indexada: OpenAlex/Crossref (periodicos academicos).",
            ]
            docs.append(
                Document(
                    source_id=source["id"],
                    title=f"{title} - {journal or 'periodico academico'} ({year})",
                    url=work["url"],
                    text="\n".join(p for p in parts if p),
                    tags=source.get("tags", []) + [query],
                    collected_at=time.strftime("%Y-%m-%d"),
                    priority=int(source.get("priority", 2)),
                )
            )
        time.sleep(0.3)
    return docs


def _openalex_search(settings: Settings, query: str, limit: int) -> list[dict]:
    try:
        resp = _get(
            "https://api.openalex.org/works",
            settings,
            params={
                "search": query,
                "filter": "language:pt,type:article",
                "per_page": limit,
            },
        )
        if resp.status_code != 200:
            return []
        out: list[dict] = []
        for w in resp.json().get("results", []):
            authors = [
                a.get("author", {}).get("display_name") or ""
                for a in w.get("authorships", [])[:3]
            ]
            src = ((w.get("primary_location") or {}).get("source") or {}).get(
                "display_name", ""
            )
            doi = w.get("doi") or ""
            url = doi if doi.startswith("http") else (f"https://doi.org/{doi}" if doi else w.get("id", ""))
            out.append(
                {
                    "title": w.get("display_name") or "",
                    "authors": [a for a in authors if a],
                    "journal": src,
                    "year": w.get("publication_year"),
                    "url": url,
                    "abstract": _abstract_from_openalex(w),
                }
            )
        return out
    except (requests.RequestException, ConnectionError, ValueError):
        return []


def _crossref_search(settings: Settings, query: str, limit: int) -> list[dict]:
    try:
        resp = _get(
            "https://api.crossref.org/works",
            settings,
            params={"query.bibliographic": query, "rows": limit},
        )
        if resp.status_code != 200:
            return []
        out: list[dict] = []
        for item in resp.json().get("message", {}).get("items", []):
            title = (item.get("title") or [""])[0]
            authors = [
                f"{a.get('given','')} {a.get('family','')}".strip()
                for a in item.get("author", [])[:3]
            ]
            journal = (item.get("container-title") or [""])[0]
            parts = (item.get("issued", {}).get("date-parts") or [[None]])[0]
            year = parts[0] if parts else None
            doi = item.get("DOI") or ""
            url = f"https://doi.org/{doi}" if doi else item.get("URL", "")
            if not url:
                continue
            out.append(
                {
                    "title": title,
                    "authors": [a for a in authors if a],
                    "journal": journal,
                    "year": year,
                    "url": url,
                    "abstract": "",
                }
            )
        return out
    except (requests.RequestException, ConnectionError, ValueError):
        return []


IBGE_SERIES = [
    (4099, "Taxa de desocupacao (%) - pessoas de 14 anos ou mais"),
    (6386, "Pessoas ocupadas na semana de referencia (mil)"),
    (6390, "Rendimento medio mensal real do trabalho principal (R$)"),
]

IBGE_PUBLIC_URL = (
    "https://www.ibge.gov.br/estatisticas/sociais/trabalho/17270-pnad-continua.html"
)

IPEA_API = "http://www.ipeadata.gov.br/api/odata4"
IPEA_PUBLIC_URL = "https://www.ipeadata.gov.br/"

IPEA_SERIES = [
    ("PNADC12_ESPCC12", "Empregados no setor privado com carteira assinada (mil)"),
    ("PNADC12_ESPSC12", "Empregados no setor privado sem carteira assinada (mil)"),
    ("PNADCT_TXINFORMUF", "Taxa de informalidade - Brasil (%)"),
    ("PNADC12_RRPHESPCC12", "Rendimento real medio habitual - empregado com carteira (R$)"),
    ("PNADC12_RRPHESPSC12", "Rendimento real medio habitual - empregado sem carteira (R$)"),
    ("GAC12_SALMINRE12", "Salario minimo real (R$)"),
]


def _get_json_cached(settings: Settings, url: str, retries: int = 3):
    cache_path = settings.cache_dir / (
        hashlib.sha1(url.encode()).hexdigest() + ".json"
    )
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except ValueError:
            cache_path.unlink(missing_ok=True)
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            resp = _get(url, settings)
            resp.raise_for_status()
            data = resp.json()
            cache_path.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            time.sleep(0.4)
            return data
        except (requests.RequestException, ConnectionError, ValueError) as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise ConnectionError(f"falha apos {retries} tentativas: {url}") from last_error


def fetch_ibge_series(
    settings: Settings, agregado: int, periodos: str = "201201-202602"
) -> list[tuple[str, str]]:
    url = (
        f"https://servicodados.ibge.gov.br/api/v3/agregados/{agregado}"
        f"/periodos/{periodos}/variaveis?localidades=N1[all]"
    )
    data = _get_json_cached(settings, url)
    serie = data[0]["resultados"][0]["series"][0]["serie"]
    return [(k, v) for k, v in serie.items() if v not in (None, "...", "-")]


def fetch_sidra_table(
    settings: Settings,
    agregado: int,
    periodos: str,
    variavel: int,
    classificacao: str | None = None,
) -> dict[str, dict[str, str]]:
    query = "?localidades=N1[all]"
    if classificacao:
        query += f"&classificacao={classificacao}[all]"
    data = _get_json_cached(
        settings,
        f"https://servicodados.ibge.gov.br/api/v3/agregados/{agregado}"
        f"/periodos/{periodos}/variaveis/{variavel}{query}",
    )
    out: dict[str, dict[str, str]] = {}
    for resultado in (data[0].get("resultados") or []) if data else []:
        categoria = "Total"
        for classe in resultado.get("classificacoes") or []:
            if classificacao and str(classe.get("id")) != str(classificacao):
                continue
            nomes = list((classe.get("categoria") or {}).values())
            if nomes:
                categoria = str(nomes[0])
            if classificacao:
                break
        valores: dict[str, str] = {}
        for serie in resultado.get("series") or []:
            for periodo, valor in (serie.get("serie") or {}).items():
                if valor not in (None, "", "...", "-"):
                    valores[str(periodo)] = str(valor)
        if valores:
            out[categoria] = valores
    return out


def annual_average(items: list[tuple[str, object]]) -> list[tuple[str, float]]:
    by_year: dict[str, list[float]] = {}
    for periodo, valor in items:
        try:
            num = float(valor)
        except (TypeError, ValueError):
            continue
        by_year.setdefault(str(periodo)[:4], []).append(num)
    return [(ano, sum(v) / len(v)) for ano, v in sorted(by_year.items()) if v]


def fetch_ipea_series(settings: Settings, code: str) -> list[tuple[str, float]]:
    data = _get_json_cached(
        settings, f"{IPEA_API}/ValoresSerie(SERCODIGO='{code}')"
    )
    out: list[tuple[str, float]] = []
    for item in data.get("value", []):
        nivnome = str(item.get("NIVNOME") or "")
        if nivnome and nivnome != "Brasil":
            continue
        valor = item.get("VALVALOR")
        if valor is None:
            continue
        periodo = str(item.get("VALDATA") or "")[:7]
        if len(periodo) == 7:
            out.append((periodo, float(valor)))
    return out


def _fmt_periodo(periodo: str) -> str:
    p = str(periodo)
    if len(p) == 4 and p.isdigit():
        return p
    if len(p) == 6 and p.isdigit():
        ano, mes = p[:4], int(p[4:])
        if mes in (1, 2, 3):
            tri = (mes - 1) // 3 + 1
            return f"T{tri} {ano}"
        return f"{ano}-{p[4:]}"
    return p


def _sample_annual(items: list) -> list:
    by_year: dict[str, list] = {}
    for periodo, valor in items:
        try:
            num = float(valor)
        except (TypeError, ValueError):
            continue
        by_year.setdefault(str(periodo)[:4], []).append(num)
    return [(ano, f"{sum(v) / len(v):.1f}") for ano, v in sorted(by_year.items()) if v]


def collect_ibge(settings: Settings, source: dict) -> list[Document]:
    lines = [
        "IBGE - PNAD Continua: series nacionais do mercado de trabalho, 2012 a 2026.",
        "Periodos trimestrais no formato 'T1 2012'; medias anuais no formato '2012'.",
        "Use as medias anuais para graficos de serie historica longa (2012-2026).",
    ]
    for agregado, label in IBGE_SERIES:
        try:
            valid = fetch_ibge_series(settings, agregado)
        except (ConnectionError, ValueError, KeyError, IndexError):
            continue
        lines.append(f"Indicador: {label} [agregado IBGE {agregado}]")
        lines.append("  Media anual (serie historica longa 2012-2026):")
        lines.extend(
            f"    {ano}: {valor}" for ano, valor in _sample_annual(valid)
        )
        lines.append("  Detalhe trimestral recente:")
        lines.extend(
            f"    {_fmt_periodo(periodo)}: {valor}"
            for periodo, valor in valid[-24:]
        )
    if len(lines) <= 2:
        return []
    return [
        Document(
            source_id=source["id"],
            title=source["name"],
            url=IBGE_PUBLIC_URL,
            text="\n".join(lines),
            tags=source.get("tags", []),
            collected_at=time.strftime("%Y-%m-%d"),
            priority=int(source.get("priority", 3)),
        )
    ]


def collect_ipea(settings: Settings, source: dict) -> list[Document]:
    lines = [
        "Ipea - SERIES (Ipeadata): series do mercado de trabalho replicadas da PNAD Continua.",
        "Periodos mensais no formato 'AAAA-MM'; medias anuais no formato 'AAAA'.",
        "Formalidade: empregados com carteira vs sem carteira; taxa de informalidade; rendimentos reais.",
    ]
    for code, label in IPEA_SERIES:
        try:
            valid = fetch_ipea_series(settings, code)
        except (ConnectionError, ValueError):
            continue
        if not valid:
            continue
        lines.append(f"Indicador: {label} [serie Ipea {code}]")
        lines.append("  Media anual (2012 em diante):")
        lines.extend(
            f"    {ano}: {valor:.1f}"
            for ano, valor in _sample_annual(
                [(p, str(v)) for p, v in valid if p >= "2012-01"]
            )
        )
        lines.append("  Detalhe mensal recente:")
        lines.extend(
            f"    {periodo}: {valor:.1f}" for periodo, valor in valid[-12:]
        )
    if len(lines) <= 3:
        return []
    return [
        Document(
            source_id=source["id"],
            title=source["name"],
            url=IPEA_PUBLIC_URL,
            text="\n".join(lines),
            tags=source.get("tags", []),
            collected_at=time.strftime("%Y-%m-%d"),
            priority=int(source.get("priority", 3)),
        )
    ]


COLLECTORS = {
    "html": collect_planalto_like,
    "camara_api": collect_camara,
    "senado_api": collect_senado,
    "news_rss": collect_news_rss,
    "ibge_api": collect_ibge,
    "ipea_api": collect_ipea,
    "academic": collect_academic,
}


def collect_all(settings: Settings) -> list[Document]:
    documents: list[Document] = []
    for source in settings.sources:
        collector = COLLECTORS.get(source.get("type"))
        if not collector:
            continue
        try:
            found = collector(settings, source)
            documents.extend(found)
            print(f"[coleta] {source['id']}: {len(found)} documento(s)")
        except Exception as exc:
            print(f"[coleta] {source['id']}: ERRO {type(exc).__name__}: {exc}")
    return documents


def save_corpus(settings: Settings, documents: list[Document]) -> Path:
    path = settings.corpus_dir / "documents.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for doc in documents:
            fh.write(json.dumps(doc.to_dict(), ensure_ascii=False) + "\n")
    return path


def load_corpus(settings: Settings) -> list[dict]:
    path = settings.corpus_dir / "documents.jsonl"
    if not path.exists():
        return []
    docs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs
