from __future__ import annotations

import asyncio
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from .chunking import build_chunks
from .collectors import collect_all, load_corpus, save_corpus
from .config import Settings
from .llm import LLMError, OllamaClient
from .pdf import export
from .rag import build_index
from .report import SYSTEM_PROMPT, audit_report, generate_report

TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"

app = FastAPI(title="Search-Law-Works-Brazil")

_settings: Settings | None = None
_client: OllamaClient | None = None
_report_state: dict = {"status": "ocioso", "log": []}


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


def get_client() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient(get_settings())
    return _client


def _append_log(line: str) -> None:
    _report_state["log"].append(line)


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@app.post("/api/coletar")
def api_coletar() -> dict:
    settings = get_settings()

    def run() -> None:
        _report_state["status"] = "coletando"
        try:
            docs = collect_all(settings)
            save_corpus(settings, docs)
            chunks = build_chunks(settings)
            _append_log(f"coleta concluida: {len(docs)} docs, {len(chunks)} fragmentos")
        except Exception as exc:
            _append_log(f"erro na coleta: {exc}")
        finally:
            _report_state["status"] = "ocioso"

    threading.Thread(target=run, daemon=True).start()
    return {"status": "coletando"}


@app.get("/api/status")
def api_status() -> dict:
    settings = get_settings()
    docs = load_corpus(settings)
    return {
        "status": _report_state["status"],
        "documentos": len(docs),
        "chaves": len(settings.ollama_api_keys),
        "log": _report_state["log"][-30:],
    }


@app.post("/api/relatorio")
def api_relatorio() -> dict:
    if _report_state["status"] in ("coletando", "gerando relatorio"):
        return {"status": _report_state["status"]}

    def run() -> None:
        settings = get_settings()
        _report_state["status"] = "gerando relatorio"
        _report_state["log"].append("inicio da geracao do relatorio")
        try:
            if not load_corpus(settings):
                docs = collect_all(settings)
                save_corpus(settings, docs)
                build_chunks(settings)
            md = generate_report(settings, get_client(), log=_append_log)
            name = f"relatorio-trabalho-brasil-{date.today().isoformat()}"
            md_path, extra = export(settings, md, name)
            _report_state["log"].append(f"concluido: {md_path} | {extra}")
        except Exception as exc:
            _report_state["log"].append(f"erro: {type(exc).__name__}: {exc}")
        finally:
            _report_state["status"] = "ocioso"

    threading.Thread(target=run, daemon=True).start()
    return {"status": "gerando relatorio"}


@app.get("/api/download")
def api_download() -> FileResponse:
    settings = get_settings()
    files = sorted(settings.output_dir.glob("relatorio-trabalho-brasil-*.md"))
    if not files:
        return HTMLResponse("Nenhum relatorio gerado ainda.", status_code=404)
    return FileResponse(files[-1], media_type="text/markdown", filename=files[-1].name)


@app.get("/api/auditar")
def api_auditar() -> dict:
    settings = get_settings()
    files = sorted(settings.output_dir.glob("relatorio-trabalho-brasil-*.md"))
    if not files:
        return {"erro": "nenhum relatorio gerado ainda"}
    path = files[-1]
    stats = audit_report(path.read_text(encoding="utf-8"), settings.outline)
    stats["arquivo"] = path.name
    return stats


@app.post("/api/chat")
async def api_chat(payload: dict) -> StreamingResponse:
    question = (payload.get("message") or "").strip()
    settings = get_settings()

    async def stream():
        if not question:
            yield "data: \n\n"
            return
        try:
            index = build_index(settings)
        except FileNotFoundError:
            yield f"data: { {'text': 'Corpus vazio. Clique em Coletar fontes.'} }\n\n"
            return
        chunks = index.search(question, top_k=8)
        context = "\n\n".join(
            f"[{c['title']}] {c['url']}\n{c['text']}" for c in chunks
        ) or "Corpus vazio. Use /api/coletar."

        def ask() -> str:
            try:
                return get_client().chat_with_retry(
                    f"Contexto:\n{context}\n\nPergunta: {question}",
                    system=SYSTEM_PROMPT,
                    max_tokens=1500,
                )
            except LLMError as exc:
                return f"Erro do LLM: {exc}"

        answer = await asyncio.to_thread(ask)
        yield f"data: { {'text': answer} }\n\n"
        sources = [
            {"title": c["title"], "url": c["url"], "date": c["collected_at"]}
            for c in chunks[:5]
        ]
        yield f"data: { {'sources': sources} }\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
