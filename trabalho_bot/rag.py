"""Busca semantica simples por relevancia (BM25) sobre o corpus fragmentado."""

from __future__ import annotations

import re
import unicodedata

from rank_bm25 import BM25Okapi

from .chunking import load_chunks
from .config import Settings

_TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = {
    "de", "da", "do", "das", "dos", "e", "ou", "para", "por", "com", "sem",
    "que", "uma", "um", "os", "as", "ao", "a", "o", "em", "no", "na", "nos",
    "nas", "mais", "mas", "se", "como", "quando", "onde", "qual", "quais",
    "the", "of", "and", "for", "to", "in", "on",
}


def normalize(text: str) -> str:
    """Remove acentos e converte para minusculas para indexacao uniforme."""
    stripped = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in stripped if not unicodedata.combining(ch))


def tokenize(text: str) -> list[str]:
    """Tokeniza texto normalizado removendo stopwords e tokens curtos."""
    tokens = _TOKEN_RE.findall(normalize(text))
    return [t for t in tokens if len(t) > 2 and t not in STOPWORDS]


class SearchIndex:
    """Indice BM25 construido uma unica vez e reutilizado nas consultas."""

    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.bm25 = BM25Okapi(
            [tokenize(c["text"] + " " + " ".join(str(t) for t in c["tags"])) for c in chunks]
        )

    def search(self, query: str, top_k: int = 8) -> list[dict]:
        """Retorna os fragmentos mais relevantes para a consulta informada."""
        scores = self.bm25.get_scores(tokenize(query))
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [self.chunks[i] for i, s in ranked[:top_k] if s > 0]


def build_index(settings: Settings) -> SearchIndex:
    """Carrega os fragmentos do disco e constroi o indice BM25."""
    return SearchIndex(load_chunks(settings))
