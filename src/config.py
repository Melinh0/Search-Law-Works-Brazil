from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@dataclass
class Settings:

    user_agent: str
    timeout: int
    cache_dir: Path
    corpus_dir: Path
    output_dir: Path
    sources: list[dict] = field(default_factory=list)
    outline: dict = field(default_factory=dict)
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_cloud_url: str = "https://ollama.com"
    ollama_cloud_model: str = "gemma4:31b-cloud"
    ollama_local_model: str = "llama3.1:8b"
    ollama_api_keys: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, refresh: bool = False) -> "Settings":
        load_dotenv(ROOT / ".env")
        with open(CONFIG_DIR / "sources.yaml", encoding="utf-8") as fh:
            sources_raw = yaml.safe_load(fh)
        with open(CONFIG_DIR / "outline.yaml", encoding="utf-8") as fh:
            outline = yaml.safe_load(fh)

        cache_dir = ROOT / sources_raw.get("cache_dir", "data/cache")
        corpus_dir = ROOT / sources_raw.get("corpus_dir", "data/corpus")
        output_dir = ROOT / sources_raw.get("output_dir", "output")
        for path in (cache_dir, corpus_dir, output_dir):
            path.mkdir(parents=True, exist_ok=True)
        if refresh:
            for item in cache_dir.glob("**/*"):
                if item.is_file():
                    item.unlink()

        keys = [
            k.strip()
            for k in os.getenv("OLLAMA_API_KEYS", "").split(",")
            if k.strip()
        ]
        return cls(
            user_agent=sources_raw.get("user_agent", "SearchLawWorksBR/1.0"),
            timeout=int(sources_raw.get("timeout", 45)),
            cache_dir=cache_dir,
            corpus_dir=corpus_dir,
            output_dir=output_dir,
            sources=sources_raw.get("sources", []),
            outline=outline,
            ollama_host=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
            ollama_cloud_url=os.getenv("OLLAMA_CLOUD_URL", "https://ollama.com"),
            ollama_cloud_model=os.getenv("OLLAMA_CLOUD_MODEL", "gemma4:31b-cloud"),
            ollama_local_model=os.getenv("OLLAMA_LOCAL_MODEL", "llama3.1:8b"),
            ollama_api_keys=keys,
        )
