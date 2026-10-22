from __future__ import annotations

import hashlib
import html as html_module
import json
import re
import time
import unicodedata
import urllib.parse
import warnings
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..config import Settings

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

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


def fetch_pdf(settings: Settings, url: str, retries: int = 3) -> str:
    if PdfReader is None:
        raise RuntimeError("pypdf nao instalado: rode 'pip install -r requirements.txt'")
    key = hashlib.sha1(url.encode()).hexdigest()
    text_path = settings.cache_dir / (key + ".pdf.txt")
    if text_path.exists():
        return text_path.read_text(encoding="utf-8", errors="replace")
    pdf_path = settings.cache_dir / (key + ".pdf")
    if not pdf_path.exists():
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
        pdf_path.write_bytes(resp.content)
        time.sleep(0.4)
    reader = PdfReader(str(pdf_path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    text = "\n".join(parts)
    text = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
    if len(text) > 400_000:
        text = text[:400_000]
    text_path.write_text(text, encoding="utf-8")
    return text


def collect_pdf(settings: Settings, source: dict) -> list[Document]:
    text = fetch_pdf(settings, source["url"])
    if len(text.strip()) < 200:
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
    ("PNADCT_PCT14UF_HOM", "Pessoas de 14 anos ou mais - homens (% do total por sexo)"),
    ("PNADCT_PCT14UF_MUL", "Pessoas de 14 anos ou mais - mulheres (% do total por sexo)"),
    ("PNADCT_PCTDFTUF_HOM", "Forca de trabalho - homens (% do total por sexo)"),
    ("PNADCT_PCTDFTUF_MUL", "Forca de trabalho - mulheres (% do total por sexo)"),
    ("PNADCT_RRHPUF_HOM", "Rendimento medio real mensal do trabalho principal - homens (R$)"),
    ("PNADCT_RRHPUF_MUL", "Rendimento medio real mensal do trabalho principal - mulheres (R$)"),
    ("PNADC12_ESPCC12", "Empregados no setor privado com carteira assinada (mil)"),
    ("PNADC12_ESPSC12", "Empregados no setor privado sem carteira assinada (mil)"),
    ("PNADCT_TXINFORMUF", "Taxa de informalidade - Brasil (%)"),
    ("PNADC12_RRPHESPCC12", "Rendimento real medio habitual - empregado com carteira (R$)"),
    ("PNADC12_RRPHESPSC12", "Rendimento real medio habitual - empregado sem carteira (R$)"),
    ("GAC12_SALMINRE12", "Salario minimo real (R$)"),
    ("PRECOS12_IPCA12", "IPCA - indice nacional de precos ao consumidor amplo"),
]

REGIOES_BR = {
    "1": "Norte",
    "2": "Nordeste",
    "3": "Sudeste",
    "4": "Sul",
    "5": "Centro-Oeste",
}

IPEA_REGIOES = [
    ("PNADCT_TXDSCUPUF", "Taxa de desocupacao (PNAD Continua, %)"),
    ("PNADCT_TXINFORMUF", "Taxa de informalidade (PNAD Continua, %)"),
    ("PNADCT_RRHPUF", "Rendimento medio real mensal do trabalho principal (R$)"),
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


def ultimo_periodo_sidra(settings: Settings, agregado: int) -> str | None:
    data = _get_json_cached(
        settings,
        f"https://servicodados.ibge.gov.br/api/v3/agregados/{agregado}/periodos",
    )
    periodos = data.get("periodos") if isinstance(data, dict) else data
    ids = [
        str(p["id"])
        for p in (periodos or [])
        if isinstance(p, dict) and p.get("id")
    ]
    return ids[-1] if ids else None


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


def fetch_ipea_series_regioes(settings: Settings, code: str) -> dict[str, list[tuple[str, float]]]:
    data = _get_json_cached(
        settings, f"{IPEA_API}/ValoresSerie(SERCODIGO='{code}')"
    )
    out: dict[str, list[tuple[str, float]]] = {}
    for item in data.get("value", []):
        if str(item.get("NIVNOME") or "") != "Regiões":
            continue
        valor = item.get("VALVALOR")
        if valor is None:
            continue
        tercod = str(item.get("TERCODIGO") or "")
        periodo = str(item.get("VALDATA") or "")[:7]
        if tercod and len(periodo) == 7:
            out.setdefault(tercod, []).append((periodo, float(valor)))
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
    sidra_idade = [
        (1641, "Populacao de 14 anos ou mais (mil pessoas)"),
        (4090, "Ocupadas (mil pessoas)"),
        (4092, "Desocupadas (mil pessoas)"),
        (4094, "Fora da forca de trabalho (mil pessoas)"),
        (4097, "Nivel de ocupacao (%)"),
    ]
    ultimo = "202602"
    try:
        encontrado = ultimo_periodo_sidra(settings, 4094)
        if encontrado:
            ultimo = encontrado
    except (ConnectionError, ValueError, KeyError, TypeError):
        pass
    for var, label in sidra_idade:
        try:
            tab = fetch_sidra_table(settings, 4094, ultimo, var, "58")
        except (ConnectionError, ValueError, KeyError):
            continue
        if not tab:
            continue
        tri, ano = ultimo[4], ultimo[:4]
        lines.append(
            f"Composicao da populacao por idade ({tri}o trimestre de {ano}, "
            f"tabela SIDRA 4094): {label}"
        )
        for faixa, vals in tab.items():
            periodo = sorted(vals)[-1]
            lines.append(f"  {faixa}: {vals[periodo]}")
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
        "Genero: composicao por sexo da populacao de 14 anos ou mais e da forca de trabalho,",
        "e rendimento medio real mensal de homens e mulheres (series _HOM e _MUL).",
        "Regioes: as series com sufixo por Grande Regiao (Norte, Nordeste, Sudeste, Sul,",
        "Centro-Oeste) trazem desocupacao, informalidade e rendimento por regiao.",
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
            f"    {ano}: {valor}"
            for ano, valor in _sample_annual(
                [(p, str(v)) for p, v in valid if p >= "2012-01"]
            )
        )
        lines.append("  Detalhe mensal recente:")
        lines.extend(
            f"    {periodo}: {valor:.1f}" for periodo, valor in valid[-12:]
        )
    for code, label in IPEA_REGIOES:
        try:
            por_regiao = fetch_ipea_series_regioes(settings, code)
        except (ConnectionError, ValueError):
            continue
        for tercod in sorted(por_regiao):
            nome = REGIOES_BR.get(tercod, tercod)
            serie = por_regiao[tercod]
            if not serie:
                continue
            lines.append(f"Indicador: {label} - Regiao {nome} [serie Ipea {code}]")
            lines.append("  Media anual (2012 em diante):")
            lines.extend(
                f"    {ano}: {valor}"
                for ano, valor in _sample_annual(
                    [(p, str(v)) for p, v in serie if p >= "2012-01"]
                )
            )
    if len(lines) <= 7:
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


def collect_ibge_pns(settings: Settings, source: dict) -> list[Document]:
    lines = [
        "IBGE - Pesquisa Nacional de Saude (PNS) 2019: saude autoavaliada boa ou muito boa,",
        "comparando 2013 e 2019. Percentual de pessoas (variavel 4738), Brasil, por grupo.",
    ]
    casos = (
        (4757, "653", "por situacao ocupacional (Ocupada, Desocupada, Fora da forca de trabalho)"),
        (4759, "652", "por faixa de renda domiciliar per capita"),
        (4756, "86", "por cor ou raca (Branca, Preta, Parda)"),
    )
    tabelas = 0
    for tabela, classificacao, desc in casos:
        try:
            dados = fetch_sidra_table(settings, tabela, "2013,2019", 4738, classificacao)
        except (ConnectionError, ValueError, KeyError, IndexError):
            continue
        tabelas += 1
        lines.append(f"Tabela SIDRA {tabela}: saude autoavaliada boa ou muito boa {desc}")
        for categoria, por_ano in dados.items():
            if categoria == "Total":
                continue
            v13 = por_ano.get("2013", "-")
            v19 = por_ano.get("2019", "-")
            lines.append(f"    {categoria}: 2013 = {v13}% | 2019 = {v19}%")
    if not tabelas:
        return []
    return [
        Document(
            source_id=source["id"],
            title=source["name"],
            url=source["url"],
            text="\n".join(lines),
            tags=source.get("tags", []),
            collected_at=time.strftime("%Y-%m-%d"),
            priority=int(source.get("priority", 3)),
        )
    ]


SIDRA_CATEGORIA_CLASS = "11913"

REGRAS_CATEGORIA_CURTA = (
    ("Empregado no setor privado, exclusive trabalhador doméstico - com carteira", "Privado com carteira"),
    ("Empregado no setor privado, exclusive trabalhador doméstico - sem carteira", "Privado sem carteira"),
    ("Empregado no setor privado, exclusive trabalhador doméstico", "Privado (sem doméstico)"),
    ("Trabalhador doméstico - com carteira", "Doméstico com carteira"),
    ("Trabalhador doméstico - sem carteira", "Doméstico sem carteira"),
    ("Trabalhador doméstico", "Trabalhador doméstico"),
    ("Empregado no setor público, exclusive militar e funcionário público estatutário - com carteira", "Servidor público civil com carteira"),
    ("Empregado no setor público, exclusive militar e funcionário público estatutário - sem carteira", "Servidor público civil sem carteira"),
    ("Empregado no setor público - militar e funcionário público estatutário", "Militar e estatutário"),
    ("Empregado no setor público", "Servidor público"),
    ("Empregador com CNPJ", "Empregador com CNPJ"),
    ("Empregador sem CNPJ", "Empregador sem CNPJ"),
    ("Empregador", "Empregador"),
    ("Conta própria com CNPJ", "Conta própria com CNPJ"),
    ("Conta própria sem CNPJ", "Conta própria sem CNPJ"),
    ("Conta própria", "Conta própria"),
    ("Empregado", "Empregado (todas as categorias)"),
    ("Total", "Total"),
)


def categoria_curta(nome: str) -> str:
    texto = str(nome or "").strip()
    for prefixo, rotulo in REGRAS_CATEGORIA_CURTA:
        if texto.startswith(prefixo):
            return rotulo
    return texto


def collect_ibge_categorias(settings: Settings, source: dict) -> list[Document]:
    lines = [
        "IBGE - PNAD Continua Anual: rendimento medio e composicao por CATEGORIA de",
        "emprego no trabalho principal (posicao na ocupacao e categoria do emprego).",
        "Tabela SIDRA 4659 (anual 2012-2025) e tabela SIDRA 4097 (trimestral 2012-2026).",
        "Rendimentos em REAIS (valores deflatorados, medias anuais).",
        "Use os rotulos curtos de categoria; nao repita a serie total de rendimento",
        "medio (que ja foi graficada na secao 1).",
    ]
    series = 0

    try:
        renda = fetch_sidra_table(
            settings, 4659, "2012-2025", 5932, SIDRA_CATEGORIA_CLASS
        )
    except (ConnectionError, ValueError, KeyError, IndexError):
        renda = {}
    for categoria, por_ano in sorted(renda.items()):
        rotulo = categoria_curta(categoria)
        linhas = [
            f"    {ano}: {valor}" for ano, valor in sorted(por_ano.items())
        ]
        if not linhas:
            continue
        series += 1
        lines.append(
            f"Rendimento medio mensal real - {rotulo} (R$): media anual 2012-2025"
        )
        lines.extend(linhas)

    for var, titulo in ((4090, "Pessoas ocupadas (mil)"), (4108, "Participacao no total de ocupados (%)")):
        try:
            tabela = fetch_sidra_table(
                settings, 4097, "201201-202602", var, SIDRA_CATEGORIA_CLASS
            )
        except (ConnectionError, ValueError, KeyError, IndexError):
            continue
        for categoria, por_periodo in sorted(tabela.items()):
            rotulo = categoria_curta(categoria)
            medias = annual_average(sorted(por_periodo.items()))
            if not medias:
                continue
            series += 1
            linhas = [f"    {ano}: {valor:.1f}" for ano, valor in medias]
            lines.append(
                f"{titulo} - {rotulo}: media anual dos trimestres 2012-2026"
            )
            lines.extend(linhas)

    if not series:
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


DIEESE_CESTA_BASE_URL = "https://www.dieese.org.br/analisecestabasica"

CAPITAIS_POR_REGIAO: dict[str, list[str]] = {
    "Norte": [
        "Manaus", "Belem", "Macapa", "Palmas",
        "Boa Vista", "Porto Velho", "Rio Branco",
    ],
    "Nordeste": [
        "Salvador", "Fortaleza", "Recife", "Natal", "Joao Pessoa",
        "Maceio", "Aracaju", "Teresina", "Sao Luis",
    ],
    "Sudeste": ["Sao Paulo", "Rio de Janeiro", "Belo Horizonte", "Vitoria"],
    "Sul": ["Curitiba", "Porto Alegre", "Florianopolis"],
    "Centro-Oeste": ["Brasilia", "Goiania", "Campo Grande", "Cuiaba"],
}

REGIAO_DE_CAPITAL: dict[str, str] = {
    capital.upper(): regiao
    for regiao, capitais in CAPITAIS_POR_REGIAO.items()
    for capital in capitais
}

PARTICIPACAO_ALIMENTACAO = 0.3571
FATOR_FAMILIA = 3

_LINHA_CESTA_RE = re.compile(
    r"^(?P<cap>[A-Z][A-Z\s]{2,26}?)\s+"
    r"(?P<cesta>\d{1,4},\d{2})\s+"
    r"(?P<var>-?\d{1,3},\d{1,2})\s+"
    r"(?P<pct>\d{1,3},\d{2})\s+"
    r"(?P<horas>\d{1,3}H\d{1,2}M)"
)

_HORAS_RE = re.compile(r"(\d{1,3})\s*[hH]\s*(\d{1,2})\s*[mM]")
_NUM_RE = re.compile(r"-?\d{1,4}(?:,\d{1,2})?")
_DIGITO_RE = re.compile(r"\d")


def _sem_acento(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _num_br(texto: str) -> float:
    return float(str(texto).replace(".", "").replace(",", "."))


def _nome_conhecido(nome: str) -> str | None:
    limpo = _sem_acento(nome).upper()
    limpo = re.sub(r"[^A-Z ]", " ", limpo)
    limpo = " ".join(limpo.split())
    if not limpo:
        return None
    if limpo in REGIAO_DE_CAPITAL:
        return limpo
    for capital in REGIAO_DE_CAPITAL:
        if capital.endswith(" " + limpo):
            return capital
    return None


def _extrair_linhas_cesta(texto: str) -> dict[str, dict]:
    inicio = texto.find("TABELA 1")
    if inicio < 0:
        return {}
    trecho = texto[inicio:inicio + 8000]
    fim = trecho.find("Fonte:")
    if fim > 0:
        trecho = trecho[:fim]
    saida: dict[str, dict] = {}
    pendente = ""
    for bruta in trecho.splitlines():
        linha = bruta.strip()
        if not linha:
            continue
        if not _DIGITO_RE.search(linha):
            pendente = linha
            continue
        corte = _DIGITO_RE.search(linha)
        assert corte is not None
        cabeca = linha[: corte.start()]
        cauda = linha[corte.start():]
        nome = _nome_conhecido(cabeca)
        if nome is None and pendente:
            nome = _nome_conhecido(f"{pendente} {cabeca}")
        if nome is None:
            pendente = ""
            continue
        pendente = ""
        horas_m = _HORAS_RE.search(cauda)
        horas = ""
        if horas_m:
            horas = f"{int(horas_m.group(1)):03d}h{int(horas_m.group(2)):02d}m"
            cauda = cauda.replace(horas_m.group(0), " ")
        numeros = [_num_br(n) for n in _NUM_RE.findall(cauda)]
        if len(numeros) < 4:
            continue
        numeros.sort(reverse=True)
        cesta = numeros[0]
        pct_sm = numeros[1]
        if cesta <= 0:
            continue
        saida[nome] = {"cesta": cesta, "pct_sm": pct_sm, "horas": horas}
    return saida


def serie_cesta_basica(
    settings: Settings, anos: list[int]
) -> tuple[dict[str, dict[str, dict]], list[str]]:
    agora = time.localtime()
    ano_atual = agora.tm_year
    mes_atual = agora.tm_mon
    serie: dict[str, dict[str, dict]] = {}
    for ano in anos:
        for mes in range(1, 13):
            if ano == ano_atual and mes > mes_atual:
                break
            if ano > ano_atual:
                continue
            url = (
                f"{DIEESE_CESTA_BASE_URL}/{ano}/{ano}{mes:02d}cestabasica.pdf"
            )
            try:
                texto = fetch_pdf(settings, url, retries=2)
            except (ConnectionError, RuntimeError):
                continue
            linhas = _extrair_linhas_cesta(texto)
            if linhas:
                serie[f"{ano}-{mes:02d}"] = linhas
    return serie, sorted(serie)


def cesta_por_regiao(settings: Settings, anos: list[int]) -> dict:
    serie, periodos = serie_cesta_basica(settings, anos)
    if not periodos:
        return {"periodos": [], "series": {}, "base": {}, "ultimo": {}}
    contagem: dict[str, int] = {}
    for _, dados in serie.items():
        for capital in dados:
            contagem[capital] = contagem.get(capital, 0) + 1
    corte = max(2, int(len(periodos) * 0.9))
    base_comum = [c for c, n in contagem.items() if n >= corte]
    base_por_regiao: dict[str, list[str]] = {}
    for regiao, capitais in CAPITAIS_POR_REGIAO.items():
        base_por_regiao[regiao] = sorted(
            c for c in capitais if c.upper() in base_comum
        )

    series: dict[str, dict[str, dict[str, float]]] = {}
    for regiao, capitais in CAPITAIS_POR_REGIAO.items():
        cesta_por_periodo: dict[str, float] = {}
        sm_por_periodo: dict[str, float] = {}
        for periodo, dados in serie.items():
            valores = [
                dados[c.upper()]["cesta"]
                for c in base_por_regiao[regiao]
                if c.upper() in dados
            ]
            if not valores:
                continue
            media = sum(valores) / len(valores)
            cesta_por_periodo[periodo] = media
            sm_por_periodo[periodo] = (
                media * FATOR_FAMILIA / PARTICIPACAO_ALIMENTACAO
            )
        if cesta_por_periodo:
            series[regiao] = {"cesta": cesta_por_periodo, "sm_necessario": sm_por_periodo}

    ultimo_periodo = periodos[-1]
    ultimo: dict[str, dict] = {}
    for regiao, capitais in CAPITAIS_POR_REGIAO.items():
        dados = serie[ultimo_periodo]
        valores = [dados[c.upper()]["cesta"] for c in capitais if c.upper() in dados]
        if not valores:
            continue
        media = sum(valores) / len(valores)
        pcts = [dados[c.upper()]["pct_sm"] for c in capitais if c.upper() in dados]
        horas = [
            dados[c.upper()].get("horas", "")
            for c in capitais
            if c.upper() in dados
        ]
        ultimo[regiao] = {
            "cesta_media": media,
            "sm_necessario": media * FATOR_FAMILIA / PARTICIPACAO_ALIMENTACAO,
            "capitais": len(valores),
            "mais_cara": max(valores),
            "mais_barata": min(valores),
            "pct_sm": sum(pcts) / len(pcts) if pcts else 0.0,
            "horas": horas[0] if horas else "",
        }
    return {
        "periodos": periodos,
        "series": series,
        "base": base_por_regiao,
        "ultimo": ultimo,
        "ultimo_periodo": ultimo_periodo,
    }


def _media_anual(por_periodo: dict[str, float]) -> list[tuple[str, float]]:
    por_ano: dict[str, list[float]] = {}
    for periodo, valor in por_periodo.items():
        por_ano.setdefault(periodo[:4], []).append(valor)
    return [(ano, sum(v) / len(v)) for ano, v in sorted(por_ano.items()) if v]


def collect_dieese_cesta_regioes(settings: Settings, source: dict) -> list[Document]:
    from ..calculos import br

    anos = [int(a) for a in (source.get("anos") or [2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026])]
    dados = cesta_por_regiao(settings, anos)
    if not dados["periodos"]:
        return []
    linhas = [
        "DIEESE/Conab - Pesquisa Nacional da Cesta Basica de Alimentos: custo medio",
        "da cesta basica e salario minimo necessario nas CINCO Grandes Regioes do",
        "Brasil (elaboracao propria: media das capitais da regiao a partir da",
        "TABELA 1 dos boletins mensais do DIEESE).",
        "Salario minimo necessario = cesta media da regiao x 3 (familia de quatro",
        "pessoas) / 35,71% (participacao da alimentacao na cesta, POF) - mesma",
        "metodologia do DIEESE aplicada a media regional.",
        f"Serie: {dados['periodos'][0]} a {dados['periodos'][-1]} (mensal).",
    ]
    for regiao, series in dados["series"].items():
        bases = ", ".join(dados["base"].get(regiao, []))
        linhas.append(f"Regiao {regiao} (base fixa da serie: {bases}):")
        linhas.append("  Cesta basica media (R$) - media anual dos meses:")
        linhas.extend(
            f"    {ano}: {br(valor)}" for ano, valor in _media_anual(series["cesta"])
        )
        linhas.append("  Salario minimo necessario da regiao (R$):")
        linhas.extend(
            f"    {ano}: {br(valor)}"
            for ano, valor in _media_anual(series["sm_necessario"])
        )
    if dados["ultimo"]:
        linhas.append(f"Referencia mais recente ({dados['ultimo_periodo']}), todas as capitais:")
        for regiao, item in dados["ultimo"].items():
            linhas.append(
                f"  {regiao}: cesta media R$ {br(item['cesta_media'])} "
                f"({item['capitais']} capitais) | mais cara R$ {br(item['mais_cara'])} "
                f"| mais barata R$ {br(item['mais_barata'])} | salario minimo "
                f"necessario R$ {br(item['sm_necessario'])}"
            )
    return [
        Document(
            source_id=source["id"],
            title=source["name"],
            url=source.get("url") or f"{DIEESE_CESTA_BASE_URL}/",
            text="\n".join(linhas),
            tags=source.get("tags", []),
            collected_at=time.strftime("%Y-%m-%d"),
            priority=int(source.get("priority", 3)),
        )
    ]


def collect_custo_formal(settings: Settings, source: dict) -> list[Document]:
    from ..calculos import (
        ALIQUOTA_FGTS,
        FAIXAS_INSS_2026,
        FAIXAS_IRRF_2026,
        SALARIO_MINIMO_2026,
        TETO_INSS_2026,
        br,
        folha_formal,
        folha_informal,
        pacote_anual,
    )

    linhas = [
        "Custo do trabalho formal em 2026 - numeros calculados sobre as tabelas",
        "oficiais (Portaria Interministerial MPS/MF no 13/2026 para o INSS;",
        "tabelas de tributacao de 2026 da Receita Federal para o IRRF, com o",
        "redutor da Lei 15.270/2025; FGTS de 8% conforme a Lei 8.036/1977;",
        "13o salario de 1/12 e ferias com 1/3 de 4/3 de um mes conforme a CLT).",
        "Use estes valores EXATOS no texto e nas tabelas; nao recalcule.",
        f"Salario minimo de 2026: R$ {br(SALARIO_MINIMO_2026)} | teto do INSS: "
        f"R$ {br(TETO_INSS_2026)}.",
        "Tabela progressiva do INSS (Portaria 13/2026):",
    ]
    for teto, aliquota, deduzir in FAIXAS_INSS_2026:
        linhas.append(
            f"  ate R$ {br(teto)}: aliquota {aliquota * 100:.1f}% "
            f"(parcela a deduzir R$ {br(deduzir)})"
        )
    linhas.append("Tabela progressiva do IRRF mensal de 2026 (Receita Federal):")
    for teto, aliquota, deduzir in FAIXAS_IRRF_2026:
        teto_txt = "acima de R$ 4.664,68" if teto == float("inf") else br(teto)
        linhas.append(
            f"  base ate R$ {teto_txt}: aliquota {aliquota * 100:.1f}% "
            f"(parcela a deduzir R$ {br(deduzir)})"
        )
    linhas.append(
        "  redutor da Lei 15.270/2025: imposto zero ate R$ 5.000,00 e reducao "
        "decrescente (R$ 978,62 - 0,133145 x renda) de R$ 5.000,01 a R$ 7.350,00."
    )
    linhas.append("Folha mensal do empregado com carteira (calculo oficial):")
    for nivel in (1621.00, 2500.00, 3500.00, 5000.00, 7000.00, 12000.00):
        folha = folha_formal(nivel)
        linhas.append(
            f"  salario bruto R$ {br(nivel)}: INSS R$ {br(folha['inss'])} | "
            f"IRRF R$ {br(folha['irrf'])} | liquido R$ {br(folha['liquido'])}"
        )
    linhas.append(
        f"FGTS de {ALIQUOTA_FGTS * 100:.0f}% (Lei 8.036/1977, art. 20), 13o salario "
        "de um twelfthavo do salario, ferias com 1/3 de 4/3 de um mes e aviso previo "
        "nao incidem sobre a renda do trabalhador informal."
    )

    try:
        renda = fetch_sidra_table(
            settings, 4659, "2012-2025", 5932, SIDRA_CATEGORIA_CLASS
        )
    except (ConnectionError, ValueError, KeyError, IndexError):
        renda = {}
    pontos: dict[str, dict[str, float]] = {}
    for categoria, por_ano in renda.items():
        rotulo = categoria_curta(categoria)
        if rotulo == "Total":
            continue
        valores = {}
        for ano, valor in por_ano.items():
            try:
                valores[str(ano)] = float(valor)
            except (TypeError, ValueError):
                continue
        if valores:
            pontos[rotulo] = valores
    com_carteira = pontos.get("Privado com carteira") or {}
    sem_carteira = pontos.get("Privado sem carteira") or {}
    anos = sorted(set(com_carteira) & set(sem_carteira))
    if anos:
        ano = anos[-1]
        folha_f = folha_formal(com_carteira[ano])
        folha_i = folha_informal(sem_carteira[ano])
        pacote_f = pacote_anual(folha_f, formal=True)
        pacote_i = pacote_anual(folha_i, formal=False)
        diferenca = com_carteira[ano] - sem_carteira[ano]
        linhas.append(
            f"Teste do argumento informal x formal com os rendimentos medios "
            f"reais de {ano} (tabela SIDRA 4659):"
        )
        linhas.append(
            f"  empregado com carteira: R$ {br(com_carteira[ano])}/mes | "
            f"sem carteira: R$ {br(sem_carteira[ano])}/mes | "
            f"diferenca R$ {br(diferenca)} "
            f"({diferenca / sem_carteira[ano] * 100:.1f}%)"
        )
        linhas.append(
            f"  com carteira: INSS R$ {br(folha_f['inss'])} | IRRF "
            f"R$ {br(folha_f['irrf'])} | liquido R$ {br(folha_f['liquido'])} | "
            f"equivalente anual (12 meses + 13o + ferias + 1/3 + FGTS) "
            f"R$ {br(pacote_f['total'])}"
        )
        linhas.append(
            f"  sem carteira: sem INSS retido | IRPF R$ {br(folha_i['irrf'])} | "
            f"recebe R$ {br(folha_i['liquido'])} | equivalente anual "
            f"(12 meses, sem 13o, sem ferias e sem FGTS) R$ {br(pacote_i['total'])}"
        )
        linhas.append(
            "  o trabalhador sem carteira ganha menos e, mesmo sem os descontos, "
            "nao recebe 13o, ferias + 1/3 nem FGTS."
        )
    return [
        Document(
            source_id=source["id"],
            title=source["name"],
            url=source.get("url") or "https://www.gov.br/receitafederal/pt-br/assuntos/meu-imposto-de-renda/tabelas/2026",
            text="\n".join(linhas),
            tags=source.get("tags", []),
            collected_at=time.strftime("%Y-%m-%d"),
            priority=int(source.get("priority", 3)),
        )
    ]


COLLECTORS = {
    "html": collect_planalto_like,
    "pdf": collect_pdf,
    "camara_api": collect_camara,
    "senado_api": collect_senado,
    "news_rss": collect_news_rss,
    "ibge_api": collect_ibge,
    "ibge_pns": collect_ibge_pns,
    "ipea_api": collect_ipea,
    "academic": collect_academic,
    "ibge_categorias": collect_ibge_categorias,
    "dieese_cesta_regioes": collect_dieese_cesta_regioes,
    "custo_formal": collect_custo_formal,
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
