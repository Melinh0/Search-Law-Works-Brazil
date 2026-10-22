
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

CENTIMO = Decimal("0.01")


def _dec(valor) -> Decimal:
    return Decimal(str(valor))


def _round2(valor: Decimal) -> float:
    return float(valor.quantize(CENTIMO, rounding=ROUND_HALF_UP))


def br(valor: float, casas: int = 2) -> str:
    quantizado = _dec(valor).quantize(
        Decimal("1." + "0" * casas) if casas else Decimal("1"), rounding=ROUND_HALF_UP
    )
    texto = f"{quantizado:,.{casas}f}"
    return texto.replace(",", "@").replace(".", ",").replace("@", ".")


SALARIO_MINIMO_2026 = 1621.00
TETO_INSS_2026 = 8475.55

FAIXAS_INSS_2026: tuple[tuple[float, float, float], ...] = (
    (1621.00, 0.075, 0.00),
    (2902.84, 0.09, 23.66),
    (4354.27, 0.12, 110.75),
    (8475.55, 0.14, 197.83),
)

FAIXAS_IRRF_2026: tuple[tuple[float, float, float], ...] = (
    (2428.80, 0.0, 0.0),
    (2826.65, 0.075, 182.16),
    (3751.05, 0.15, 394.16),
    (4664.68, 0.225, 675.49),
    (float("inf"), 0.275, 908.73),
)

ALIQUOTA_FGTS = 0.08
MESES_DECIMO = 12.0
FERIAS_COM_TERCO = 4.0 / 3.0


def desconto_inss(base: float) -> float:
    if base <= 0:
        return 0.0
    salario = _dec(min(base, TETO_INSS_2026))
    aliquota, deduzir = _dec(FAIXAS_INSS_2026[-1][1]), _dec(FAIXAS_INSS_2026[-1][2])
    for teto, aliq, ded in FAIXAS_INSS_2026:
        if salario <= _dec(teto):
            aliquota, deduzir = _dec(aliq), _dec(ded)
            break
    return _round2(max(Decimal("0"), aliquota * salario - deduzir))


def desconto_irrf(rendimentos: float, base: float) -> float:
    if base <= 0:
        return 0.0
    aliquota, deduzir = _dec(FAIXAS_IRRF_2026[-1][1]), _dec(FAIXAS_IRRF_2026[-1][2])
    for teto, aliq, ded in FAIXAS_IRRF_2026:
        if _dec(base) <= _dec(teto):
            aliquota, deduzir = _dec(aliq), _dec(ded)
            break
    imposto = max(Decimal("0"), aliquota * _dec(base) - deduzir)
    renda = _dec(rendimentos)
    if renda <= Decimal("5000"):
        return 0.0
    if renda <= Decimal("7350"):
        imposto = max(
            Decimal("0"),
            imposto - (Decimal("978.62") - Decimal("0.133145") * renda),
        )
    return _round2(imposto)


def folha_formal(bruto: float) -> dict:
    inss = desconto_inss(bruto)
    base_irrf = max(0.0, bruto - inss)
    irrf = desconto_irrf(bruto, base_irrf)
    return {
        "bruto": bruto,
        "inss": inss,
        "irrf": irrf,
        "liquido": bruto - inss - irrf,
    }


def folha_informal(renda: float) -> dict:
    irrf = desconto_irrf(renda, renda)
    return {
        "bruto": renda,
        "inss": 0.0,
        "irrf": irrf,
        "liquido": renda - irrf,
    }


def pacote_anual(folha: dict, formal: bool) -> dict:
    liquido_12 = folha["liquido"] * MESES_DECIMO
    bruto = folha["bruto"]
    decimo = bruto if formal else 0.0
    ferias = (bruto * FERIAS_COM_TERCO / MESES_DECIMO) if formal else 0.0
    fgts = (bruto * ALIQUOTA_FGTS) if formal else 0.0
    return {
        "liquido_12": liquido_12,
        "decimo_terceiro": decimo,
        "ferias": ferias,
        "fgts": fgts,
        "total": liquido_12 + decimo + ferias + fgts,
    }
