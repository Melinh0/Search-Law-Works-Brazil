"""Divisao dos documentos coletados em fragmentos indexaveis para busca."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Settings

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Quebra um texto longo em fragmentos sobrepostos respeitando paragrafos."""
    paragraphs = [p for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if len(para) > size:
            if current:
                chunks.append(current.strip())
                current = ""
            start = 0
            while start < len(para):
                chunks.append(para[start : start + size])
                start += size - overlap
            continue
        if len(current) + len(para) + 1 <= size:
            current = f"{current}\n{para}".strip()
        else:
            chunks.append(current.strip())
            current = para
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if len(c) > 80]


def build_chunks(settings: Settings) -> list[dict]:
    """Transforma os documentos do corpus em fragmentos com metadados de origem."""
    path = settings.corpus_dir / "documents.jsonl"
    if not path.exists():
        raise FileNotFoundError(
            "Corpus nao encontrado. Execute primeiro a coleta de fontes."
        )
    chunks: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            doc = json.loads(line)
            for idx, piece in enumerate(chunk_text(doc["text"])):
                chunks.append(
                    {
                        "id": f"{doc['source_id']}-{len(chunks)}",
                        "source_id": doc["source_id"],
                        "title": doc["title"],
                        "url": doc["url"],
                        "tags": doc.get("tags", []),
                        "priority": int(doc.get("priority", 1)),
                        "collected_at": doc.get("collected_at", ""),
                        "chunk_index": idx,
                        "text": piece,
                    }
                )
    out = settings.corpus_dir / "chunks.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        for chunk in chunks:
            fh.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    return chunks


def load_chunks(settings: Settings) -> list[dict]:
    """Le os fragmentos indexados do corpus, ou os reconstru se inexistentes."""
    path = settings.corpus_dir / "chunks.jsonl"
    if not path.exists():
        return build_chunks(settings)
    chunks = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                chunks.append(json.loads(line))
    return chunks
