from __future__ import annotations

import json
import re

from .charts import ChartRenderer, parse_spec
from .collectors import annual_average, fetch_ipea_series, fetch_sidra_table
from .config import Settings

FONTE_IPEA = "IPEA, 2026"
FONTE_PNS = "IBGE, 2019"

PNS_VARIAVEL = 4738
PNS_PERIODOS = "2013,2019"
PNS_ANOS = ("2013", "2019")

PNS_CASOS = (
    (
        4757,
        "653",
        ("Ocupada", "Desocupada", "Fora da força de trabalho"),
        "Autoavaliação de Saúde Boa ou Muito Boa por Situação Ocupacional (2013 e 2019)",
        " Quem estava fora da força de trabalho avaliava a própria saúde como boa ou "
        "muito boa 22,5 pontos percentuais menos do que os ocupados em 2019.",
    ),
    (
        4759,
        "652",
        (
            "Até 1/4 do salário mínimo",
            "1/4 a 1/2 do salário mínimo",
            "1/2 a 1 salário mínimo",
            "1 a 2 salários mínimos",
            "2 a 3 salários mínimos",
            "3 a 5 salários mínimos",
            "Mais de 5 salários mínimos",
        ),
        "Autoavaliação de Saúde Boa ou Muito Boa por Faixa de Renda Domiciliar "
        "per capita (2013 e 2019)",
        " A avaliação positiva da saúde sobe de 55,5% na menor renda para 86,4% "
        "na maior em 2019.",
    ),
    (
        4756,
        "86",
        ("Branca", "Preta", "Parda"),
        "Autoavaliação de Saúde Boa ou Muito Boa por Cor ou Raça (2013 e 2019)",
        " Em 2019, 71,0% das pessoas brancas avaliaram a saúde como boa ou muito "
        "boa, contra 62,9% das pretas e 62,1% das pardas.",
    ),
)


def _try_ipea_series(settings: Settings, code: str, log) -> list[tuple[str, float]]:
    try:
        return fetch_ipea_series(settings, code)
    except (ConnectionError, ValueError) as exc:
        log(f"[panorama] falha ao coletar serie {code}: {exc}")
        return []


def _medias_anuais(settings: Settings, log, code: str) -> dict[str, float]:
    return dict(annual_average(_try_ipea_series(settings, code, log)))


def _grade_anual(*series: dict[str, float]) -> tuple[list[str], list[list[float]]]:
    todos: set[str] = set()
    for serie in series:
        todos |= set(serie)
    if not todos:
        return [], [[] for _ in series]
    inicio, fim = (min(int(a) for a in todos), max(int(a) for a in todos))
    anos = [str(a) for a in range(inicio, fim + 1)]
    alinhadas = [[serie.get(ano, float("nan")) for ano in anos] for serie in series]
    return anos, alinhadas


def _pns_percentuais(
    settings: Settings, log, tabela: int, classificacao: str, curtos: tuple[str, ...]
) -> tuple[list[str], list[float], list[float]] | None:
    try:
        dados = fetch_sidra_table(settings, tabela, PNS_PERIODOS, PNS_VARIAVEL, classificacao)
    except (ConnectionError, ValueError) as exc:
        log(f"[panorama] falha na tabela PNS {tabela}: {exc}")
        return None
    categorias = [categoria for categoria in dados if categoria != "Total"]
    if not categorias:
        log(f"[panorama] tabela PNS {tabela} sem categorias utilizaveis")
        return None
    rotulos = list(curtos) if len(curtos) == len(categorias) else categorias
    series: list[list[float]] = []
    for ano in PNS_ANOS:
        valores: list[float] = []
        for categoria in categorias:
            bruto = (dados.get(categoria) or {}).get(ano)
            try:
                valores.append(float(bruto))
            except (TypeError, ValueError):
                log(f"[panorama] valor ausente na PNS {tabela} ({categoria}, {ano})")
                return None
        series.append(valores)
    return rotulos, series[0], series[1]


def demographic_specs(settings: Settings, log=print) -> list[dict]:
    specs: list[dict] = []

    pop_h = _medias_anuais(settings, log, "PNADCT_PCT14UF_HOM")
    pop_m = _medias_anuais(settings, log, "PNADCT_PCT14UF_MUL")
    ft_h = _medias_anuais(settings, log, "PNADCT_PCTDFTUF_HOM")
    ft_m = _medias_anuais(settings, log, "PNADCT_PCTDFTUF_MUL")
    comuns = set(pop_h) & set(pop_m) & set(ft_h) & set(ft_m)
    if comuns:
        ano = max(comuns)
        v_h = [pop_h[ano], ft_h[ano]]
        v_m = [pop_m[ano], ft_m[ano]]
        specs.append(
            {
                "tipo": "barra_empilhada",
                "titulo": (
                    "Composição da População de 14 Anos ou Mais e da Força de "
                    f"Trabalho por Sexo ({ano})"
                ),
                "rotulos": ["População de 14 anos ou mais", "Força de trabalho"],
                "valores": v_h,
                "series": [
                    {"nome": "Homens", "valores": v_h},
                    {"nome": "Mulheres", "valores": v_m},
                ],
                "unidade": "%",
                "fonte": FONTE_IPEA,
                "nota": (
                    " As mulheres são maioria na população de 14 anos ou mais, mas "
                    "minoria na força de trabalho, reflexo da menor participação feminina."
                ),
            }
        )

    sub_h = _medias_anuais(settings, log, "PNADCT_TXSUBSTUF_HOM")
    sub_m = _medias_anuais(settings, log, "PNADCT_TXSUBSTUF_MUL")
    anos, alinhadas = _grade_anual(sub_h, sub_m)
    if anos:
        v_sub_h, v_sub_m = alinhadas
        specs.append(
            {
                "tipo": "linha",
                "titulo": f"Taxa de Subutilização da Força de Trabalho por Sexo ({anos[0]}-{anos[-1]})",
                "rotulos": anos,
                "valores": v_sub_h,
                "series": [
                    {"nome": "Homens (%)", "valores": v_sub_h},
                    {"nome": "Mulheres (%)", "valores": v_sub_m},
                ],
                "unidade": "%",
                "fonte": FONTE_IPEA,
                "nota": (
                    " A taxa de subutilização das mulheres supera a dos homens em todos "
                    "os anos da série histórica."
                ),
            }
        )

    ren_h = _medias_anuais(settings, log, "PNADCT_RRHPUF_HOM")
    ren_m = _medias_anuais(settings, log, "PNADCT_RRHPUF_MUL")
    anos, alinhadas = _grade_anual(ren_h, ren_m)
    if anos:
        v_ren_h, v_ren_m = alinhadas
        specs.append(
            {
                "tipo": "linha",
                "titulo": (
                    "Rendimento Médio Real Mensal do Trabalho Principal por Sexo "
                    f"({anos[0]}-{anos[-1]})"
                ),
                "rotulos": anos,
                "valores": v_ren_h,
                "series": [
                    {"nome": "Homens (R$)", "valores": v_ren_h},
                    {"nome": "Mulheres (R$)", "valores": v_ren_m},
                ],
                "unidade": "R$ (médias anuais)",
                "fonte": FONTE_IPEA,
                "nota": (
                    " O rendimento médio real das mulheres permanece abaixo do dos "
                    "homens em toda a série, com ausência de dados em 2021."
                ),
            }
        )

    return specs


def health_specs(settings: Settings, log=print) -> list[dict]:
    specs: list[dict] = []
    for tabela, classificacao, curtos, titulo, nota in PNS_CASOS:
        dados = _pns_percentuais(settings, log, tabela, classificacao, curtos)
        if not dados:
            continue
        rotulos, v_2013, v_2019 = dados
        specs.append(
            {
                "tipo": "barra_agrupada",
                "titulo": titulo,
                "rotulos": rotulos,
                "valores": v_2013,
                "series": [
                    {"nome": "2013", "valores": v_2013},
                    {"nome": "2019", "valores": v_2019},
                ],
                "unidade": "%",
                "fonte": FONTE_PNS,
                "nota": nota,
            }
        )
    return specs


def render_section1_figures(
    settings: Settings, caption_base: int, file_base: int | None = None, log=print
) -> list[str]:
    specs = demographic_specs(settings, log) + health_specs(settings, log)
    assets = settings.output_dir / "assets"
    if file_base is None:
        file_nums = []
        for caminho in assets.glob("figura-*.png"):
            m = re.match(r"figura-(\d+)", caminho.stem)
            if m:
                file_nums.append(int(m.group(1)))
        file_base = max(file_nums, default=0)
    renderer = ChartRenderer(assets, wipe=False)
    renderer.fig_counter = file_base

    figures: list[str] = []
    for posicao, spec in enumerate(specs, start=1):
        try:
            figura = renderer.render(parse_spec(json.dumps(spec)))
        except Exception as exc:
            log(f"[panorama] falha ao renderizar {spec.get('titulo', '?')}: {exc}")
            continue
        arquivo = file_base + posicao
        legenda = caption_base + posicao
        if arquivo != legenda:
            figura = figura.replace(f"Figura {arquivo}", f"Figura {legenda}")
        figures.append(figura)
    log(f"[panorama] {len(figures)} figura(s) renderizada(s) para a Secao 1")
    return figures
