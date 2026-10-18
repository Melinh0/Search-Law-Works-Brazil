from __future__ import annotations

import json
import re

from .charts import ChartRenderer, parse_spec
from .collectors import annual_average, fetch_ipea_series
from .config import Settings

ANEXO_HEADING_RE = re.compile(r"^##\s+Anexo\s+B", re.MULTILINE)

FONTE_IPEA = "IPEA, 2026"


def _common_years(
    a: list[tuple[str, float]], b: list[tuple[str, float]]
) -> tuple[list[str], list[float], list[float]]:
    da, db = dict(a), dict(b)
    years = [y for y in sorted(set(da) & set(db)) if y >= "2012"]
    return years, [da[y] for y in years], [db[y] for y in years]


def _try_series(settings: Settings, code: str, log) -> list[tuple[str, float]]:
    try:
        return fetch_ipea_series(settings, code)
    except (ConnectionError, ValueError) as exc:
        log(f"[anexo] falha ao coletar serie {code}: {exc}")
        return []


def build_charts_annex(
    settings: Settings, md_text: str, log=print
) -> tuple[str, int]:
    if ANEXO_HEADING_RE.search(md_text):
        return md_text, 0
    existing = len(re.findall(r'<figure class="figura">', md_text))
    assets = settings.output_dir / "assets"
    file_nums = []
    for p in assets.glob("figura-*.png"):
        m = re.match(r"figura-(\d+)", p.stem)
        if m:
            file_nums.append(int(m.group(1)))
    file_base = max(file_nums, default=0)
    renderer = ChartRenderer(assets, wipe=False)
    renderer.fig_counter = file_base

    specs: list[dict] = []

    com = annual_average(_try_series(settings, "PNADC12_ESPCC12", log))
    sem = annual_average(_try_series(settings, "PNADC12_ESPSC12", log))
    anos, v_com, v_sem = _common_years(com, sem)
    if anos:
        specs.append(
            {
                "tipo": "linha",
                "titulo": (
                    "Empregados no Setor Privado: Com vs Sem Carteira Assinada "
                    f"({anos[0]}-{anos[-1]})"
                ),
                "rotulos": anos,
                "valores": v_com,
                "series": [
                    {"nome": "Com carteira assinada (mil)", "valores": v_com},
                    {"nome": "Sem carteira assinada (mil)", "valores": v_sem},
                ],
                "unidade": "mil pessoas",
                "fonte": FONTE_IPEA,
                "nota": " Medias anuais das series PNADC12_ESPCC12 e PNADC12_ESPSC12.",
            }
        )

    taxa = [
        (a, v)
        for a, v in annual_average(_try_series(settings, "PNADCT_TXINFORMUF", log))
        if a >= "2015"
    ]
    if taxa:
        anos = [a for a, _ in taxa]
        specs.append(
            {
                "tipo": "linha",
                "titulo": f"Taxa de Informalidade no Emprego Urbano ({anos[0]}-{anos[-1]})",
                "rotulos": anos,
                "valores": [v for _, v in taxa],
                "series": [
                    {"nome": "Informalidade (%)", "valores": [v for _, v in taxa]}
                ],
                "unidade": "%",
                "fonte": FONTE_IPEA,
                "nota": " Media anual da serie PNADCT_TXINFORMUF, territorio Brasil.",
            }
        )

    rend_com = annual_average(_try_series(settings, "PNADC12_RRPHESPCC12", log))
    rend_sem = annual_average(_try_series(settings, "PNADC12_RRPHESPSC12", log))
    anos, v_com, v_sem = _common_years(rend_com, rend_sem)
    if anos:
        specs.append(
            {
                "tipo": "linha",
                "titulo": (
                    "Rendimento Real Medio do Trabalho Principal: Com vs Sem "
                    f"Carteira Assinada ({anos[0]}-{anos[-1]})"
                ),
                "rotulos": anos,
                "valores": v_com,
                "series": [
                    {"nome": "Com carteira (R$)", "valores": v_com},
                    {"nome": "Sem carteira (R$)", "valores": v_sem},
                ],
                "unidade": "R$ (medias anuais)",
                "fonte": FONTE_IPEA,
                "nota": (
                    " Medias anuais das series PNADC12_RRPHESPCC12 e "
                    "PNADC12_RRPHESPSC12; valores reais."
                ),
            }
        )

    salario = [
        (a, v)
        for a, v in annual_average(_try_series(settings, "GAC12_SALMINRE12", log))
        if a >= "2012"
    ]
    if salario:
        anos = [a for a, _ in salario]
        specs.append(
            {
                "tipo": "linha",
                "titulo": f"Salario Minimo Real ({anos[0]}-{anos[-1]})",
                "rotulos": anos,
                "valores": [v for _, v in salario],
                "series": [
                    {"nome": "Salario minimo real (R$)", "valores": [v for _, v in salario]}
                ],
                "unidade": "R$ (medias anuais)",
                "fonte": FONTE_IPEA,
                "nota": " Media anual da serie GAC12_SALMINRE12; valores reais.",
            }
        )

    if not specs:
        log("[anexo] nenhuma serie disponivel; Anexo B nao gerado")
        return md_text, 0

    figures: list[str] = []
    for i, spec in enumerate(specs, start=1):
        try:
            fig = renderer.render(parse_spec(json.dumps(spec)))
        except Exception as exc:
            log(f"[anexo] falha ao renderizar {spec.get('titulo', '?')}: {exc}")
            continue
        n_file = file_base + i
        n_cap = existing + i
        if n_file != n_cap:
            fig = fig.replace(f"Figura {n_file}", f"Figura {n_cap}")
        figures.append(fig)
    if not figures:
        return md_text, 0

    intro = (
        "Series historicas complementares coletadas diretamente das APIs oficiais "
        "do IBGE (SIDRA) e do Ipea (Series Temporais/Ipeadata), usadas para "
        "contextualizar formalidade, informalidade e rendimentos no mercado de "
        "trabalho brasileiro. Os dados replicam a PNAD Continua."
    )
    annex = (
        "\n\n## Anexo B – Graficos complementares (IBGE/PNAD/IPEA)\n\n"
        + intro
        + "\n\n"
        + "\n\n".join(figures)
        + "\n"
    )
    log(f"[anexo] Anexo B gerado com {len(figures)} grafico(s)")
    return md_text.rstrip() + annex, len(figures)
