"""Execucao como modulo: python -m trabalho_bot [chat|coletar|relatorio|status|web]."""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        from .web import main as web_main

        web_main()
        return
    from .cli import main as cli_main

    cli_main(sys.argv[1:])


if __name__ == "__main__":
    main()
