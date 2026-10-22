
from __future__ import annotations

import json
import re
import unicodedata

from .calculos import (
    ALIQUOTA_FGTS,
    FAIXAS_INSS_2026,
    FAIXAS_IRRF_2026,
    FERIAS_COM_TERCO,
    MESES_DECIMO,
    SALARIO_MINIMO_2026,
    TETO_INSS_2026,
    br as _br,
    desconto_inss,
    desconto_irrf,
    folha_formal,
    folha_informal,
    pacote_anual,
)
from .collectors import (
    SIDRA_CATEGORIA_CLASS,
    annual_average,
    cesta_por_regiao,
    categoria_curta,
    fetch_ipea_series,
    fetch_ipea_series_regioes,
    fetch_sidra_table,
    ultimo_periodo_sidra,
)
from .config import Settings

FONTE_IBGE_CATEGORIAS = "IBGE, 2026"
FONTE_IPEA = "IPEA, 2026"
FONTE_DIEESE = "DIEESE, 2026"
FONTE_LEGAL = "BRASIL, 2026"

REGIOES = ["Norte", "Nordeste", "Sudeste", "Sul", "Centro-Oeste"]


def _pct(valor: float, casas: int = 1) -> str:
    return f"{_br(valor, casas)}%".replace(".", ",")


def _chave(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", str(texto))
    sem_acento = "".join(c for c in normalizado if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


def series_categorias(settings: Settings, log=print) -> dict:
    renda: dict[str, dict[str, float]] = {}
    composicao: dict[str, dict[str, float]] = {}
    try:
        tabela = fetch_sidra_table(
            settings, 4659, "2012-2025", 5932, SIDRA_CATEGORIA_CLASS
        )
    except (ConnectionError, ValueError, KeyError, IndexError):
        tabela = {}
    for categoria, por_ano in tabela.items():
        rotulo = categoria_curta(categoria)
        if rotulo == "Total":
            continue
        pontos: dict[str, float] = {}
        for ano, valor in por_ano.items():
            try:
                pontos[str(ano)] = float(valor)
            except (TypeError, ValueError):
                continue
        if pontos:
            renda[_chave(rotulo)] = pontos
    try:
        tabela = fetch_sidra_table(
            settings, 4097, "201201-202602", 4108, SIDRA_CATEGORIA_CLASS
        )
    except (ConnectionError, ValueError, KeyError, IndexError):
        tabela = {}
    for categoria, por_periodo in tabela.items():
        rotulo = categoria_curta(categoria)
        if rotulo == "Total":
            continue
        medias = annual_average(sorted(por_periodo.items()))
        pontos = {ano: valor for ano, valor in medias if ano >= "2012"}
        if pontos:
            composicao[_chave(rotulo)] = pontos
    return {"renda": renda, "composicao": composicao}


def series_cesta_regioes(settings: Settings, anos: list[int], log=print) -> dict:
    return cesta_por_regiao(settings, anos)


def series_rendimento_regioes(settings: Settings, log=print) -> dict:
    try:
        por_regiao = fetch_ipea_series_regioes(settings, "PNADCT_RRHPUF")
    except (ConnectionError, ValueError, KeyError):
        return {}
    saida: dict[str, dict[str, float]] = {}
    for tercod, serie in por_regiao.items():
        nome = {
            "1": "Norte",
            "2": "Nordeste",
            "3": "Sudeste",
            "4": "Sul",
            "5": "Centro-Oeste",
        }.get(tercod)
        if not nome:
            continue
        medias = annual_average(
            [(p, str(v)) for p, v in serie if p >= "2012-01"]
        )
        pontos = {ano: valor for ano, valor in medias}
        if pontos:
            saida[nome] = pontos
    return saida


def serie_ipca_anual(settings: Settings, log=print) -> dict[str, float]:
    try:
        serie = fetch_ipea_series(settings, "PRECOS12_IPCA12")
    except (ConnectionError, ValueError, KeyError):
        return {}
    medias = annual_average([(p, str(v)) for p, v in serie if p >= "2012-01"])
    return {ano: valor for ano, valor in medias}


def _anual(por_periodo: dict[str, float]) -> dict[str, float]:
    por_ano: dict[str, list[float]] = {}
    for periodo, valor in por_periodo.items():
        por_ano.setdefault(periodo[:4], []).append(valor)
    return {ano: sum(v) / len(v) for ano, v in sorted(por_ano.items()) if v}


def _comum(series: list[dict[str, float]]) -> list[str]:
    if not series:
        return []
    comuns = set(series[0])
    for serie in series[1:]:
        comuns &= set(serie)
    return sorted(anos for anos in comuns if anos >= "2012")


def _alinhado(anos: list[str], pontos: dict[str, float]) -> list[float]:
    return [pontos.get(ano, float("nan")) for ano in anos]


CATEGORIAS_RENDA = [
    ("privado com carteira", "Com carteira (setor privado)"),
    ("privado sem carteira", "Sem carteira (setor privado)"),
    ("conta propria", "Conta propria"),
    ("servidor publico", "Servidor publico"),
    ("empregador", "Empregador"),
]

CATEGORIAS_COMPOSICAO = [
    ("privado com carteira", "Com carteira (setor privado)"),
    ("privado sem carteira", "Sem carteira (setor privado)"),
    ("conta propria", "Conta propria"),
    ("servidor publico", "Servidor publico"),
    ("empregador", "Empregador"),
]


def specs_informal_formal(settings: Settings, log=print) -> list[dict]:
    dados = series_categorias(settings, log)
    specs: list[dict] = []

    series = []
    for chave, nome in CATEGORIAS_RENDA:
        pontos = dados["renda"].get(chave)
        if not pontos:
            continue
        series.append((nome, pontos))
    if series:
        anos = _comum([p for _, p in series])
        if len(anos) >= 8:
            specs.append(
                {
                    "tipo": "linha",
                    "titulo": (
                        "Rendimento medio mensal real por categoria de emprego "
                        f"({anos[0]}-{anos[-1]})"
                    ),
                    "rotulos": anos,
                    "valores": _alinhado(anos, series[0][1]),
                    "series": [
                        {"nome": nome, "valores": _alinhado(anos, pontos)}
                        for nome, pontos in series
                    ],
                    "unidade": "R$ (medias anuais, valores reais)",
                    "fonte": FONTE_IBGE_CATEGORIAS,
                    "nota": (
                        " Media anual do rendimento medio mensal real habitual do "
                        "trabalho principal por posicao na ocupacao e categoria do "
                        "emprego (tabela SIDRA 4659). O empregado com carteira "
                        "recebe mais que o empregado sem carteira em toda a serie."
                    ),
                }
            )

    series = []
    for chave, nome in CATEGORIAS_COMPOSICAO:
        pontos = dados["composicao"].get(chave)
        if not pontos:
            continue
        series.append((nome, pontos))
    if series:
        anos = _comum([p for _, p in series])
        if len(anos) >= 8:
            specs.append(
                {
                    "tipo": "linha",
                    "titulo": (
                        "Participacao de cada categoria de emprego no total de "
                        f"ocupados ({anos[0]}-{anos[-1]})"
                    ),
                    "rotulos": anos,
                    "valores": _alinhado(anos, series[0][1]),
                    "series": [
                        {"nome": nome, "valores": _alinhado(anos, pontos)}
                        for nome, pontos in series
                    ],
                    "unidade": "% dos ocupados",
                    "fonte": FONTE_IBGE_CATEGORIAS,
                    "nota": (
                        " Media anual da distribuicao percentual das pessoas ocupadas "
                        "por categoria do emprego (tabela SIDRA 4097). O trabalho "
                        "sem carteira e a conta propria perderam espaco para o "
                        "emprego com carteira ao longo da serie."
                    ),
                }
            )

    niveis = [1621.00, 3500.00, 7000.00, 12000.00]
    rotulos = []
    series_bruto, series_inss, series_irrf, series_liquido = [], [], [], []
    for nivel in niveis:
        folha = folha_formal(nivel)
        rotulos.append(f"R$ {_br(nivel, 2)}")
        series_bruto.append(folha["bruto"])
        series_inss.append(folha["inss"])
        series_irrf.append(folha["irrf"])
        series_liquido.append(folha["liquido"])
    specs.append(
        {
            "tipo": "barra_agrupada",
            "titulo": "Para onde vai o salario bruto do empregado com carteira (2026)",
            "rotulos": rotulos,
            "valores": series_bruto,
            "series": [
                {"nome": "Salario bruto", "valores": series_bruto},
                {"nome": "INSS (8% a 14% progressivo)", "valores": series_inss},
                {"nome": "IRRF", "valores": series_irrf},
                {"nome": "Salario liquido", "valores": series_liquido},
            ],
            "unidade": "R$ por mes",
            "fonte": FONTE_LEGAL,
            "nota": (
                " INSS pela tabela progressiva de 2026 (Portaria MPS/MF 13/2026) e "
                "IRRF pelas tabelas de 2026 da Receita Federal, ja com o redutor da "
                "Lei 15.270/2025 (imposto zero ate R$ 5.000,00). O desconto total "
                "cai com a renda: no salario minimo ele fica em R$ 121,58 (7,5%)."
            ),
        }
    )

    renda = dados["renda"]
    formal_pontos = renda.get("privado com carteira") or {}
    informal_pontos = renda.get("privado sem carteira") or {}
    if formal_pontos and informal_pontos:
        anos = sorted(set(formal_pontos) & set(informal_pontos))
        if anos:
            ano = anos[-1]
            bruto_formal = formal_pontos[ano]
            renda_informal = informal_pontos[ano]
            folha_f = folha_formal(bruto_formal)
            folha_i = folha_informal(renda_informal)
            pacote_f = pacote_anual(folha_f, formal=True)
            pacote_i = pacote_anual(folha_i, formal=False)
            specs.append(
                {
                    "tipo": "barra_agrupada",
                    "titulo": (
                        "Renda anual equivalente: empregado com carteira x "
                        f"trabalhador sem carteira ({ano})"
                    ),
                    "rotulos": ["Com carteira (CLT)", "Sem carteira (informal)"],
                    "valores": [pacote_f["total"], pacote_i["total"]],
                    "series": [
                        {
                            "nome": "12 meses de salario liquido",
                            "valores": [pacote_f["liquido_12"], pacote_i["liquido_12"]],
                        },
                        {
                            "nome": "13o salario",
                            "valores": [
                                pacote_f["decimo_terceiro"],
                                pacote_i["decimo_terceiro"],
                            ],
                        },
                        {
                            "nome": "Ferias + 1/3",
                            "valores": [pacote_f["ferias"], pacote_i["ferias"]],
                        },
                        {
                            "nome": "FGTS (8%, reserva do trabalhador)",
                            "valores": [pacote_f["fgts"], pacote_i["fgts"]],
                        },
                    ],
                    "unidade": "R$ por ano",
                    "fonte": FONTE_IBGE_CATEGORIAS,
                    "nota": (
                        f" Calculo com os rendimentos medios reais de {ano} "
                        "(tabela SIDRA 4659) e as tabelas de INSS e IRRF de 2026: "
                        "o empregado com carteira recebe mais mesmo depois dos "
                        "descontos, e ainda soma 13o, ferias com 1/3 e FGTS."
                    ),
                }
            )
    return specs


def specs_panorama_idade(settings: Settings, fonte: str, log=print) -> list[dict]:
    try:
        periodo = ultimo_periodo_sidra(settings, 4094) or "202602"
    except (ConnectionError, ValueError, KeyError, TypeError):
        periodo = "202602"
    try:
        tabela = fetch_sidra_table(settings, 4094, periodo, 4097, "58")
    except (ConnectionError, ValueError, KeyError, IndexError):
        return []
    rotulos: list[str] = []
    valores: list[float] = []
    for faixa, por_periodo in tabela.items():
        if faixa == "Total":
            continue
        try:
            valores.append(float(por_periodo[periodo]))
        except (KeyError, TypeError, ValueError):
            continue
        rotulos.append(faixa)
    if len(valores) < 3:
        return []
    trimestre = periodo[4:] or "01"
    rotulo_periodo = f"{int(trimestre)}o trimestre de {periodo[:4]}"
    specs = [
        {
            "tipo": "barra",
            "titulo": (
                "Nivel de ocupacao da populacao por grupo de idade "
                f"({rotulo_periodo})"
            ),
            "rotulos": rotulos,
            "valores": valores,
            "unidade": "%",
            "fonte": fonte,
            "nota": (
                " Percentual da populacao de cada grupo etario que esta ocupada, "
                "calculado pelo IBGE na tabela SIDRA 4094 (PNAD Continua). O nivel "
                "de ocupacao e minimo entre 14 e 17 anos por causa dos estudos, "
                "atinge o pico entre 25 e 39 anos e cai a partir dos 60 anos, "
                "quando a maioria se aposenta ou sai da forca de trabalho."
            ),
        }
    ]
    specs.extend(_specs_composicao_idade(settings, periodo, rotulo_periodo, fonte))
    return specs


CONDICOES_4094 = (
    (4090, "Ocupadas"),
    (4092, "Desocupadas"),
    (4094, "Fora da forca de trabalho"),
)


def _specs_composicao_idade(
    settings: Settings, periodo: str, rotulo_periodo: str, fonte: str
) -> list[dict]:
    try:
        populacao = fetch_sidra_table(settings, 4094, periodo, 1641, "58")
    except (ConnectionError, ValueError, KeyError, IndexError):
        return []
    faixas = list(populacao)
    if len(faixas) < 3:
        return []
    series: list[dict] = []
    for variavel, nome in CONDICOES_4094:
        try:
            tabela = fetch_sidra_table(settings, 4094, periodo, variavel, "58")
        except (ConnectionError, ValueError, KeyError, IndexError):
            return []
        valores: list[float] = []
        for faixa in faixas:
            try:
                valores.append(float(tabela[faixa][periodo]) / 1000)
            except (KeyError, TypeError, ValueError):
                valores.append(0.0)
        series.append({"nome": nome, "valores": valores})
    return [
        {
            "tipo": "barra_empilhada",
            "titulo": (
                "Composicao da populacao por condicao e grupo de idade "
                f"({rotulo_periodo})"
            ),
            "rotulos": list(faixas),
            "valores": series[0]["valores"],
            "series": series,
            "unidade": "milhoes de pessoas",
            "fonte": fonte,
            "nota": (
                " Populacao de 14 anos ou mais por condicao de ocupacao e grupo "
                "etario, em milhoes (tabela SIDRA 4094). A populacao idosa (60 anos "
                "ou mais) concentra a maior parte das pessoas fora da forca de "
                "trabalho, e os jovens de 14 a 17 anos somam uma forca de trabalho "
                "ainda em formacao."
            ),
        }
    ]



def tabela_informal_formal(settings: Settings, log=print) -> tuple[str, str, str]:
    dados = series_categorias(settings, log)
    renda = dados["renda"]
    formal = renda.get("privado com carteira") or {}
    informal = renda.get("privado sem carteira") or {}
    if not formal or not informal:
        return "", "", ""
    anos = sorted(set(formal) & set(informal))
    if not anos:
        return "", "", ""
    ano = anos[-1]
    folha_f = folha_formal(formal[ano])
    folha_i = folha_informal(informal[ano])
    pacote_f = pacote_anual(folha_f, formal=True)
    pacote_i = pacote_anual(folha_i, formal=False)
    diferenca = formal[ano] - informal[ano]
    vantagem_pct = diferenca / informal[ano] * 100 if informal[ano] else 0.0
    titulo = (
        "Teste do argumento informal x formal com os dados oficiais mais recentes"
    )
    fonte = f"{FONTE_IBGE_CATEGORIAS}; {FONTE_LEGAL}"
    linhas = [
        "| Indicador | Com carteira (CLT) | Sem carteira (informal) |",
        "|---|---|---|",
        f"| Rendimento medio mensal real ({ano}) | R$ {_br(formal[ano])} | "
        f"R$ {_br(informal[ano])} |",
        f"| Desconto de INSS | R$ {_br(folha_f['inss'])} | R$ 0,00 |",
        f"| Desconto de IRRF | R$ {_br(folha_f['irrf'])} | R$ {_br(folha_i['irrf'])} |",
        f"| Receita no mes | R$ {_br(folha_f['liquido'])} | R$ {_br(folha_i['liquido'])} |",
        f"| Equivalente anual (12 meses + 13o + ferias + 1/3 + FGTS) | "
        f"R$ {_br(pacote_f['total'])} | R$ {_br(pacote_i['total'])} |",
        f"| Vantagem do empregado com carteira | R$ {_br(diferenca)} por mes "
        f"({_pct(vantagem_pct)}) | - |",
    ]
    return titulo, fonte, "\n".join(linhas)


def tabela_categorias(settings: Settings, log=print) -> tuple[str, str, str]:
    dados = series_categorias(settings, log)
    renda = dados["renda"]
    if not renda:
        return "", "", ""
    anos_por_categoria = [sorted(p) for p in renda.values() if p]
    if not anos_por_categoria:
        return "", "", ""
    primeiro = min(anos[0] for anos in anos_por_categoria if anos)
    ultimo = max(anos[-1] for anos in anos_por_categoria if anos)
    ordem = [
        "Privado com carteira",
        "Privado sem carteira",
        "Trabalhador doméstico",
        "Trabalhador doméstico com carteira",
        "Trabalhador doméstico sem carteira",
        "Conta própria",
        "Conta própria com CNPJ",
        "Conta própria sem CNPJ",
        "Servidor público",
        "Militar e estatutário",
        "Empregador",
        "Empregador com CNPJ",
        "Empregador sem CNPJ",
    ]
    linhas = [
        f"| Categoria de emprego | {primeiro} (R$) | {ultimo} (R$) |",
        "|---|---|---|",
    ]
    for rotulo in ordem:
        pontos = renda.get(_chave(rotulo))
        if not pontos:
            continue
        inicio = pontos.get(primeiro)
        fim = pontos.get(ultimo)
        linhas.append(
            f"| {rotulo} | "
            f"{('R$ ' + _br(inicio)) if inicio is not None else 'sem serie'} | "
            f"{('R$ ' + _br(fim)) if fim is not None else 'sem serie'} |"
        )
    titulo = (
        f"Rendimento medio mensal real por categoria de emprego ({primeiro} e {ultimo})"
    )
    fonte = FONTE_IBGE_CATEGORIAS
    return titulo, fonte, "\n".join(linhas)


def specs_poder_compra_regioes(
    settings: Settings, anos_cesta: list[int] | None = None, log=print
) -> list[dict]:
    if anos_cesta is None:
        anos_cesta = list(range(2015, 2027))
    dados = series_cesta_regioes(settings, anos_cesta, log)
    if not dados.get("series"):
        return []
    specs: list[dict] = []
    base_texto = "; ".join(
        f"{regiao}: {', '.join(caps)}" for regiao, caps in dados.get("base", {}).items()
    )

    series = []
    anos = set()
    for regiao in REGIOES:
        serie = dados["series"].get(regiao)
        if not serie:
            continue
        anual = _anual(serie["cesta"])
        if not anual:
            continue
        series.append((regiao, anual))
        anos |= set(anual)
    if series:
        rotulos = sorted(anos)
        specs.append(
            {
                "tipo": "linha",
                "titulo": (
                    "Cesta basica media por Grande Regiao "
                    f"({rotulos[0]}-{rotulos[-1]})"
                ),
                "rotulos": rotulos,
                "valores": _alinhado(rotulos, series[0][1]),
                "series": [
                    {"nome": regiao, "valores": _alinhado(rotulos, pontos)}
                    for regiao, pontos in series
                ],
                "unidade": "R$ (media anual dos meses)",
                "fonte": FONTE_DIEESE,
                "nota": (
                    " Elaboracao propria: media do custo da cesta basica nas capitais "
                    f"de cada regiao (base fixa: {base_texto}). O DIEESE mudou a "
                    "composicao da cesta e a lista de capitais acompanhadas ao longo "
                    "do periodo, portanto a serie indica tendencia, nao valores "
                    "estritamente comparaveis ano a ano."
                ),
            }
        )

    series = []
    for regiao in REGIOES:
        serie = dados["series"].get(regiao)
        if not serie:
            continue
        anual = _anual(serie["sm_necessario"])
        if anual:
            series.append((regiao, anual))
    if series:
        rotulos = sorted({ano for _, pontos in series for ano in pontos})
        specs.append(
            {
                "tipo": "linha",
                "titulo": (
                    "Salario minimo necessario por Grande Regiao "
                    f"({rotulos[0]}-{rotulos[-1]})"
                ),
                "rotulos": rotulos,
                "valores": _alinhado(rotulos, series[0][1]),
                "series": [
                    {"nome": regiao, "valores": _alinhado(rotulos, pontos)}
                    for regiao, pontos in series
                ],
                "unidade": "R$ por mes",
                "fonte": FONTE_DIEESE,
                "nota": (
                    " Elaboracao propria: cesta media da regiao x 3 (familia de quatro "
                    "pessoas) / 35,71% (participacao da alimentacao na cesta, POF), "
                    "metodologia do DIEESE aplicada a media regional. Valor necessario "
                    "para cobrir a cesta constitucional, nao salario pago."
                ),
            }
        )

    rendimentos = series_rendimento_regioes(settings, log)
    ipca = serie_ipca_anual(settings, log)
    if rendimentos and ipca and dados["series"]:
        base_ipca = ipca[max(ipca)]
        rotulos: list[str] = []
        serie_renda: list[float] = []
        serie_necessario: list[float] = []
        for regiao in REGIOES:
            anual_renda = rendimentos.get(regiao) or {}
            anual_sm = _anual(dados["series"][regiao]["sm_necessario"]) if regiao in dados["series"] else {}
            if not anual_renda or not anual_sm:
                continue
            ano = max(set(anual_renda) & set(anual_sm))
            if ipca.get(ano) is None:
                continue
            fator = base_ipca / ipca[ano]
            rotulos.append(regiao)
            serie_renda.append(anual_renda[ano])
            serie_necessario.append(anual_sm[ano] * fator)
        if rotulos:
            specs.append(
                {
                    "tipo": "barra_agrupada",
                    "titulo": (
                        "Rendimento medio real da regiao x salario minimo "
                        "necessario da regiao (valores reais)"
                    ),
                    "rotulos": rotulos,
                    "valores": serie_renda,
                    "series": [
                        {
                            "nome": "Rendimento medio real do trabalho principal (R$)",
                            "valores": serie_renda,
                        },
                        {
                            "nome": "Salario minimo necessario da regiao (R$ real)",
                            "valores": serie_necessario,
                        },
                    ],
                    "unidade": "R$ (valores reais)",
                    "fonte": f"{FONTE_IPEA}; {FONTE_DIEESE}",
                    "nota": (
                        " Rendimento medio real por regiao (Ipea, PNAD Continua) "
                        "confrontado com o salario minimo necessario regional, "
                        "deflacionado pelo IPCA para o mesmo ano de referencia. "
                        "A barra menor indica renda que nao alcanca o custo de vida "
                        "regional."
                    ),
                }
            )

    ultimo = dados.get("ultimo") or {}
    if ultimo:
        rotulos = list(ultimo)
        valores = [ultimo[r]["pct_sm"] for r in rotulos]
        specs.append(
            {
                "tipo": "hbar",
                "titulo": "Percentual do salario minimo consumido pela cesta basica por regiao",
                "rotulos": rotulos,
                "valores": valores,
                "series": [{"nome": "% do salario minimo", "valores": valores}],
                "unidade": "%",
                "fonte": FONTE_DIEESE,
                "nota": (
                    f" Media regional do percentual do salario minimo liquido "
                    f"apurado pelo DIEESE em {dados.get('ultimo_periodo', '')}; "
                    "percentual maior significa cesta mais cara em relacao ao piso "
                    "salarial."
                ),
            }
        )
    return specs


def tabela_poder_compra_regioes(
    settings: Settings, anos_cesta: list[int] | None = None, log=print
) -> tuple[str, str, str]:
    if anos_cesta is None:
        anos_cesta = list(range(2015, 2027))
    dados = series_cesta_regioes(settings, anos_cesta, log)
    if not dados.get("ultimo"):
        return "", "", ""
    rendimentos = series_rendimento_regioes(settings, log)
    ipca = serie_ipca_anual(settings, log)
    ultimo = dados["ultimo"]
    titulo = "Cesta basica, salario necessario e renda por Grande Regiao"
    fonte = f"{FONTE_DIEESE}; {FONTE_IPEA}"
    linhas = [
        "| Grande Regiao | Cesta basica media (R$) | Salario minimo necessario (R$) | "
        "Rendimento medio real (R$) | Capitais |",
        "|---|---|---|---|---|",
    ]
    for regiao in REGIOES:
        item = ultimo.get(regiao)
        if not item:
            continue
        renda_regiao = ""
        if rendimentos:
            pontos = rendimentos.get(regiao) or {}
            if pontos:
                ano = max(pontos)
                renda_regiao = f"R$ {_br(pontos[ano])} ({ano})"
        linhas.append(
            f"| {regiao} | {_br(item['cesta_media'])} | {_br(item['sm_necessario'])} | "
            f"{renda_regiao or 'n/d'} | {item['capitais']} |"
        )
    return titulo, fonte, "\n".join(linhas)


SECOES_DETERMINISTICAS = {
    "panorama": "panorama_idade",
    "informal-vs-formal": "informal_formal",
    "poder-compra-regioes": "poder_compra_regioes",
}

CHARTS_DETERMINISTICOS = {
    "panorama": 2,
}

MIN_CHART_MODELO = {
    "panorama": 14,
}


def min_charts_modelo(section_id: str, total: int) -> int:
    return max(1, MIN_CHART_MODELO.get(section_id, total))

GRUPOS_GUARDA_PANORAMA = (
    (("nivel de ocupacao", "taxa de ocupacao"), ("idade", "etaria", "faixa")),
    (("condicao", "situacao"), ("idade", "etaria", "faixa")),
)


def charts_deterministicos(section_id: str) -> int:
    return CHARTS_DETERMINISTICOS.get(section_id, 0)


def _codigo_fonte(registry: dict | None, source_id: str, padrao: str) -> str:
    if registry:
        for entry in registry.values():
            if entry.get("source_id") == source_id:
                return str(entry.get("code") or padrao)
    return padrao


def _ja_existe_grafico(text: str, grupos: tuple[tuple[str, ...], ...]) -> bool:
    for bloco in re.findall(r"```grafico\n(.*?)\n```", text, flags=re.DOTALL):
        try:
            spec = json.loads(bloco)
        except ValueError:
            continue
        titulo = _chave(spec.get("titulo", ""))
        if all(any(palavra in titulo for palavra in grupo) for grupo in grupos):
            return True
    return False


def bloco_graficos(specs: list[dict]) -> str:
    partes = []
    for spec in specs:
        partes.append("```grafico\n" + json.dumps(spec, ensure_ascii=False) + "\n```")
    return "\n\n".join(partes)


def bloco_tabela(titulo: str, fonte: str, tabela: str) -> str:
    if not tabela:
        return ""
    return f"TABELA: {titulo} | fonte: {fonte}\n\n{tabela}\n"


def _e_separador(linha: str) -> bool:
    texto = linha.strip()
    if not texto.startswith("|"):
        return False
    return all(c in "|-: " for c in texto)


def _remover_tabela_categorias(text: str) -> str:
    linhas = text.splitlines()
    saida: list[str] = []
    i = 0
    while i < len(linhas):
        linha = linhas[i]
        if (
            linha.strip().startswith("|")
            and i + 1 < len(linhas)
            and _e_separador(linhas[i + 1])
        ):
            bloco = [linha, linhas[i + 1]]
            j = i + 2
            while j < len(linhas) and linhas[j].strip().startswith("|"):
                bloco.append(linhas[j])
                j += 1
            cabecalho = linha.lower()
            titulo = ""
            for anterior in reversed(saida):
                if not anterior.strip():
                    continue
                if anterior.strip().lower().startswith("tabela:"):
                    titulo = anterior.lower()
                break
            criterio = f"{cabecalho} {titulo}"
            eh_categorias = "rendimento" in criterio and (
                "2012" in criterio
                or "2025" in criterio
                or "categoria" in criterio
            )
            if eh_categorias:
                while saida and (
                    saida[-1].strip().lower().startswith("tabela:")
                    or not saida[-1].strip()
                ):
                    saida.pop()
                i = j
                continue
            saida.extend(bloco)
            i = j
            continue
        saida.append(linha)
        i += 1
    return "\n".join(saida)


def _inserir_blocos(text: str, blocos: list[str]) -> str:
    if not blocos:
        return text
    marcador = "**Em termos simples:**"
    indice = text.find(marcador)
    if indice < 0:
        return text.rstrip() + "\n\n" + "\n\n".join(blocos) + "\n"
    inicio = text.rfind("\n\n", 0, indice)
    if inicio < 0:
        return text.rstrip() + "\n\n" + "\n\n".join(blocos) + "\n"
    return (
        text[:inicio]
        + "\n\n"
        + "\n\n".join(blocos)
        + "\n\n"
        + text[inicio + 2 :]
    )


def append_secao(
    section_id: str,
    text: str,
    settings: Settings,
    log=print,
    registry: dict | None = None,
) -> str:
    tipo = SECOES_DETERMINISTICAS.get(section_id)
    if not tipo:
        return text
    try:
        if tipo == "panorama_idade":
            fonte = _codigo_fonte(registry, "ibge-pnad", FONTE_IBGE_CATEGORIAS)
            specs = specs_panorama_idade(settings, fonte, log)
            specs = [
                spec
                for spec, grupos in zip(specs, GRUPOS_GUARDA_PANORAMA)
                if not _ja_existe_grafico(text, grupos)
            ]
            titulo = fonte = tabela = ""
            titulo2 = fonte2 = tabela2 = ""
        elif tipo == "informal_formal":
            specs = specs_informal_formal(settings, log)
            titulo, fonte, tabela = tabela_informal_formal(settings, log)
            titulo2, fonte2, tabela2 = tabela_categorias(settings, log)
            text = _remover_tabela_categorias(text)
        else:
            specs = specs_poder_compra_regioes(settings, log=log)
            titulo, fonte, tabela = tabela_poder_compra_regioes(settings, log=log)
            titulo2 = fonte2 = tabela2 = ""
    except Exception as exc:
        log(f"[insights] falha ao montar a secao {section_id}: {exc}")
        return text
    if not specs:
        return text
    blocos: list[str] = []
    if tabela:
        blocos.append(bloco_tabela(titulo, fonte, tabela))
    if tabela2:
        blocos.append(bloco_tabela(titulo2, fonte2, tabela2))
    blocos.append(bloco_graficos(specs))
    return _inserir_blocos(text, blocos)
