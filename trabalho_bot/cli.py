"""Interface de terminal interativa para coleta, chat e geracao de relatorio."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from .chunking import build_chunks
from .collectors import collect_all, load_corpus, save_corpus
from .config import Settings
from .llm import LLMError, OllamaClient
from .pdf import export
from .rag import SearchIndex, build_index
from .report import SYSTEM_PROMPT, generate_report

console = Console()

HELP = """Comandos disponiveis:
  /coletar          coleta as fontes configuradas e reconstroi o corpus
  /relatorio        gera o relatorio completo em Markdown e PDF
  /relatorio <s>    gera apenas as secoes indicadas (ex.: /relatorio clt,mei)
  /buscar <texto>   busca fragmentos no corpus sem usar o LLM
  /status           mostra estado do corpus e configuracao atual
  /ajuda            mostra esta ajuda
  /sair             encerra o programa
"""


def cmd_collect(settings: Settings, refresh: bool = False) -> Settings:
    """Executa a coleta de todas as fontes e reindexa o corpus."""
    console.print("[bold]Coletando fontes...[/bold]")
    docs = collect_all(settings)
    if not docs:
        console.print("[red]Nenhum documento coletado.[/red]")
        return settings
    path = save_corpus(settings, docs)
    chunks = build_chunks(settings)
    console.print(
        f"[green]OK:[/green] {len(docs)} documentos, {len(chunks)} fragmentos -> {path}"
    )
    return settings


def cmd_report(settings: Settings, client: OllamaClient, only: list[str] | None) -> None:
    """Gera o relatorio completo (ou secoes especificas) e exporta em MD/PDF."""
    if not load_corpus(settings):
        console.print("[yellow]Corpus vazio; executando coleta automatica...[/yellow]")
        cmd_collect(settings)
    with console.status("Gerando relatorio (pode levar varios minutos)..."):
        md = generate_report(settings, client, log=lambda m: console.print(m), sections_filter=only)
    name = f"relatorio-trabalho-brasil-{date.today().isoformat()}"
    md_path, extra_path = export(settings, md, name)
    console.print(Panel(f"[green]Relatorio gerado:[/green]\n{md_path}\n{extra_path}"))


def cmd_search(settings: Settings, query: str) -> None:
    """Lista os fragmentos mais relevantes para uma consulta livre."""
    index = build_index(settings)
    results = index.search(query, top_k=6)
    if not results:
        console.print("[yellow]Nenhum fragmento encontrado.[/yellow]")
        return
    for c in results:
        console.print(
            Panel(
                c["text"][:600],
                title=f"{c['title']} | {c['collected_at']}",
                subtitle=c["url"],
            )
        )


def cmd_status(settings: Settings) -> None:
    """Exibe contagem de documentos, fragmentos e chaves configuradas."""
    docs = load_corpus(settings)
    chunks_path = settings.corpus_dir / "chunks.jsonl"
    chunk_count = (
        sum(1 for _ in open(chunks_path, encoding="utf-8"))
        if chunks_path.exists()
        else 0
    )
    console.print(
        Panel(
            f"Documentos: {len(docs)}\nFragmentos: {chunk_count}\n"
            f"Chaves cloud: {len(settings.ollama_api_keys)}\n"
            f"Modelo cloud: {settings.ollama_cloud_model}\n"
            f"Modelo local: {settings.ollama_local_model}\n"
            f"Corpus: {settings.corpus_dir}",
            title="Status",
        )
    )


def chat_loop(settings: Settings) -> None:
    """Inicia o atendimento interativo com busca RAG e resposta do LLM."""
    console.print(
        Panel(
            "[bold]Bot de Pesquisa - Trabalho no Brasil 2026[/bold]\n"
            "Digite /ajuda para ver os comandos. Perguntas em linguagem natural "
            "sao respondidas com base no corpus coletado.",
            border_style="cyan",
        )
    )
    client = OllamaClient(settings)
    index: SearchIndex | None = None
    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]voce[/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nAte logo!")
            break
        if not user_input:
            continue
        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""
            if cmd in ("/sair", "/exit", "/quit"):
                console.print("Ate logo!")
                break
            elif cmd == "/ajuda":
                console.print(HELP)
            elif cmd == "/coletar":
                cmd_collect(settings)
                index = None
            elif cmd == "/status":
                cmd_status(settings)
            elif cmd == "/buscar":
                if arg:
                    cmd_search(settings, arg)
                else:
                    console.print("[yellow]Uso: /buscar <texto>[/yellow]")
            elif cmd == "/relatorio":
                only = [s.strip() for s in arg.split(",") if s.strip()] or None
                try:
                    cmd_report(settings, client, only)
                except LLMError as exc:
                    console.print(f"[red]Erro LLM: {exc}[/red]")
            else:
                console.print(f"[yellow]Comando desconhecido: {cmd}[/yellow]")
            continue

        try:
            if index is None:
                index = build_index(settings)
            chunks = index.search(user_input, top_k=8)
            context = "\n\n".join(
                f"[{c['title']}] {c['url']}\n{c['text']}" for c in chunks
            ) or "Corpus vazio. Use /coletar primeiro."
            with console.status("Pensando..."):
                answer = client.chat_with_retry(
                    f"Contexto:\n{context}\n\nPergunta: {user_input}",
                    system=SYSTEM_PROMPT,
                    max_tokens=1500,
                )
            console.print(Panel(Markdown(answer), title="Resposta", border_style="green"))
        except LLMError as exc:
            console.print(f"[red]Erro do LLM: {exc}[/red]")
        except FileNotFoundError as exc:
            console.print(f"[red]{exc}[/red]")


def main(argv: list[str] | None = None) -> None:
    """Ponto de entrada da CLI com subcomandos para execucao nao interativa."""
    parser = argparse.ArgumentParser(
        prog="trabalho_bot", description="Bot de pesquisa sobre trabalho no Brasil"
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["chat", "coletar", "relatorio", "status"],
        default="chat",
        help="execucao imediata (padrao: chat interativo)",
    )
    parser.add_argument("--refresh", action="store_true", help="ignora cache na coleta")
    parser.add_argument(
        "--so-secoes", help="lista de ids de secoes para gerar no relatorio"
    )
    args = parser.parse_args(argv)

    settings = Settings.load(refresh=args.refresh)
    if args.command == "coletar":
        cmd_collect(settings, refresh=args.refresh)
    elif args.command == "status":
        cmd_status(settings)
    elif args.command == "relatorio":
        only = [s.strip() for s in args.so_secoes.split(",")] if args.so_secoes else None
        cmd_report(settings, OllamaClient(settings), only)
    else:
        chat_loop(settings)


if __name__ == "__main__":
    sys.exit(main())
