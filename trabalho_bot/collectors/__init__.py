"""Coletores de fontes oficiais e imprensa com cache em disco."""

from __future__ import annotations

import hashlib
import html as html_module
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..config import Settings


@dataclass
class Document:
    """Documento coletado de uma fonte, pronto para indexacao no corpus."""

    source_id: str
    title: str
    url: str
    text: str
    tags: list[str]
    collected_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def fetch(settings: Settings, url: str, encoding: str = "utf-8", retries: int = 3) -> str:
    """Baixa uma URL com cache em disco e novas tentativas antes de falhar."""
    cache_path = settings.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace")
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": settings.user_agent},
                timeout=settings.timeout,
            )
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    else:
        raise ConnectionError(f"falha apos {retries} tentativas: {url}") from last_error
    if resp.encoding.lower() in ("iso-8859-1", "latin-1", "windows-1252"):
        resp.encoding = encoding
    text = resp.text
    cache_path.write_text(text, encoding="utf-8")
    time.sleep(0.5)
    return text


def html_to_text(html: str) -> str:
    """Extrai texto legivel de HTML, removendo scripts, estilos e espacos extras."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")[:80] or "sem-titulo"


def collect_planalto_like(settings: Settings, source: dict) -> list[Document]:
    """Coleta paginas de leis do Planalto convertendo-as em documentos de texto."""
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
        )
    ]


def collect_camara(settings: Settings, source: dict) -> list[Document]:
    """Consulta a API de proposicoes da Camara por palavras-chave de trabalho."""
    docs: list[Document] = []
    seen: set[int] = set()
    for kw in source.get("keywords", []):
        resp = requests.get(
            "https://dadosabertos.camara.leg.br/api/v2/proposicoes",
            params={"keywords": kw, "itens": 10, "ordem": "DESC", "ordenarPor": "id"},
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout,
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
                )
            )
        time.sleep(0.3)
    return docs


def _camara_detail(uri: str, settings: Settings) -> dict:
    """Busca situacao e ultimas tramitacoes de uma proposicao da Camara."""
    try:
        resp = requests.get(
            uri,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json().get("dados", {})
        situacao = (data.get("situacao") or {}).get("descricao", "")
        tram_url = uri.rstrip("/") + "/tramitacoes?itens=5&ordem=DESC&ordenarPor=id"
        tram = requests.get(
            tram_url,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout,
        )
        tramitacoes = tram.json().get("dados", []) if tram.status_code == 200 else []
        return {"situacao": f"Situacao: {situacao}", "tramitacoes": tramitacoes}
    except (requests.RequestException, ValueError):
        return {}


def collect_senado(settings: Settings, source: dict) -> list[Document]:
    """Consulta a API de materias do Senado por palavras-chave de trabalho."""
    docs: list[Document] = []
    for kw in source.get("keywords", []):
        try:
            resp = requests.get(
                "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista",
                params={"palavraChave": kw},
                headers={"User-Agent": settings.user_agent},
                timeout=settings.timeout,
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
                )
            )
        time.sleep(0.3)
    return docs


def _clean_rss_description(html: str) -> str:
    """Remove tags HTML e entidades das descricoes de feeds RSS."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def collect_news_rss(settings: Settings, source: dict) -> list[Document]:
    """Coleta noticias via Google News RSS para cada consulta configurada."""
    docs: list[Document] = []
    for query in source.get("queries", []):
        url = (
            "https://news.google.com/rss/search?q="
            + requests.utils.quote(query)
            + "&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        )
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": settings.user_agent},
                timeout=settings.timeout,
            )
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError):
            continue
        for item in root.findall(".//item")[:10]:
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub = item.findtext("pubDate", "")
            desc = _clean_rss_description(item.findtext("description", ""))
            if not title:
                continue
            docs.append(
                Document(
                    source_id=source["id"],
                    title=title,
                    url=link,
                    text=f"{title}\n{desc}\nPublicado: {pub}",
                    tags=source.get("tags", []) + [query],
                    collected_at=time.strftime("%Y-%m-%d"),
                )
            )
        time.sleep(0.3)
    return docs


def collect_ibge(settings: Settings, source: dict) -> list[Document]:
    """Coleta indicadores de trabalho da PNAD Continua via API do IBGE."""
    endpoint = (
        "https://servicodados.ibge.gov.br/api/v3/agregados/6394/periodos"
        "?localidades=N1[all]"
    )
    try:
        resp = requests.get(
            endpoint,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout,
        )
        if resp.status_code != 200:
            return []
        periods = resp.json()
    except (requests.RequestException, ValueError):
        return []
    latest = [p for p in periods if isinstance(p, dict)][-4:]
    text = "PNAD Continua - periodos disponiveis: " + ", ".join(
        str(p.get("id", p)) for p in latest
    )
    detail_url = (
        "https://servicodados.ibge.gov.br/api/v3/agregados/6394"
        "/periodos/60602/variaveis/41?localidades=N1[all]"
    )
    try:
        r2 = requests.get(
            detail_url,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout,
        )
        if r2.status_code == 200:
            data = r2.json()
            if isinstance(data, list) and data:
                serie = data[0].get("serie", {})
                samples = list(serie.items())[-6:] if isinstance(serie, dict) else []
                text += "\nVariavel 41 (taxa de ocupacao): " + "; ".join(
                    f"{k}: {v}" for k, v in samples
                )
    except (requests.RequestException, ValueError):
        pass
    return [
        Document(
            source_id=source["id"],
            title=source["name"],
            url=endpoint,
            text=text,
            tags=source.get("tags", []),
            collected_at=time.strftime("%Y-%m-%d"),
        )
    ]


COLLECTORS = {
    "html": collect_planalto_like,
    "camara_api": collect_camara,
    "senado_api": collect_senado,
    "news_rss": collect_news_rss,
    "ibge_api": collect_ibge,
}


def collect_all(settings: Settings) -> list[Document]:
    """Executa todos os coletores configurados e retorna a lista de documentos."""
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
    """Persiste os documentos coletados como JSONL no diretorio do corpus."""
    path = settings.corpus_dir / "documents.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for doc in documents:
            fh.write(json.dumps(doc.to_dict(), ensure_ascii=False) + "\n")
    return path


def load_corpus(settings: Settings) -> list[dict]:
    """Le os documentos do corpus JSONL, retornando lista de dicionarios."""
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
