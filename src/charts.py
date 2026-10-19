from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

GRAFICO_RE = re.compile(r"```grafico\s*\n(.*?)\n```", re.DOTALL)

PALETTE = ["#0b3d62", "#c45d2b", "#3d8b57", "#7c5cbf", "#b8860b", "#b03a2e", "#2b7a9b"]
MATH_TYPES = {"barra", "barras", "pizza", "donut", "linha", "barra_empilhada", "barra_agrupada", "hbar"}
DIAGRAM_TYPES = {"comparativo"}
ALLOWED_TYPES = MATH_TYPES | DIAGRAM_TYPES


class ChartError(ValueError):
    pass


def _to_float(value) -> float:
    if isinstance(value, bool):
        raise ValueError(f"valor nao numerico: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    text = text.replace("\u00a0", "").replace("R$", "").replace("%", "")
    text = re.sub(r"\s+", "", text)
    if not re.fullmatch(r"-?\d+(?:[.,]\d+)*", text):
        raise ValueError(f"valor nao numerico: {value!r}")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", text):
        text = text.replace(".", "")
    return float(text)


def _diag_item(item) -> str:
    if isinstance(item, dict):
        label = (
            item.get("label")
            or item.get("titulo")
            or item.get("texto")
            or item.get("rotulo")
            or ""
        )
        valor = item.get("valor", item.get("value"))
        if label and valor is not None:
            return f"{label}: {_fmt_item(str(valor))}"
        if label:
            return str(label)
        return json.dumps(item, ensure_ascii=False)
    return str(item)


def parse_spec(raw: str) -> dict:
    try:
        spec = json.loads(raw.strip())
    except ValueError as exc:
        raise ChartError(f"JSON invalido: {exc}") from exc
    if not isinstance(spec, dict):
        raise ChartError("o bloco grafico deve ser um objeto JSON")
    tipo = str(spec.get("tipo", "")).strip().lower()
    if tipo not in ALLOWED_TYPES:
        raise ChartError(f"tipo desconhecido: {tipo!r} (use {sorted(ALLOWED_TYPES)})")
    titulo = str(spec.get("titulo", "")).strip()
    if not titulo:
        raise ChartError("grafico sem titulo")
    if tipo in MATH_TYPES:
        series = spec.get("series")
        if isinstance(series, list) and series:
            clean = []
            for i, s in enumerate(series):
                if isinstance(s, list):
                    s = {"valores": s}
                if not isinstance(s, dict):
                    raise ChartError("serie invalida")
                nome = str(
                    s.get("nome")
                    or s.get("titulo")
                    or s.get("label")
                    or s.get("rotulo")
                    or ""
                ).strip() or (titulo if len(series) == 1 else f"Serie {i + 1}")
                try:
                    vals = [_to_float(v) for v in s.get("valores", [])]
                except (TypeError, ValueError) as exc:
                    raise ChartError("valores devem ser numericos") from exc
                if not vals:
                    raise ChartError("serie sem valores")
                clean.append({"nome": nome, "valores": vals})
            spec["series"] = clean
        valores = spec.get("valores")
        if (
            not isinstance(valores, list)
            or not valores
        ) and isinstance(spec.get("series"), list) and spec["series"]:
            valores = spec["series"][0]["valores"]
        if not isinstance(valores, list) or not valores:
            raise ChartError("grafico quantitativo sem valores")
        try:
            spec["valores"] = [_to_float(v) for v in valores]
        except (TypeError, ValueError) as exc:
            raise ChartError("valores devem ser numericos") from exc
        rotulos = spec.get("rotulos")
        if tipo != "linha" or not isinstance(spec.get("series"), list):
            if not isinstance(rotulos, list) or not rotulos:
                raise ChartError("grafico sem rotulos")
            if len(rotulos) != len(spec["valores"]):
                raise ChartError("rotulos e valores com tamanhos diferentes")
            spec["rotulos"] = [str(r) for r in rotulos]
        if (
            tipo in ("barra", "barras")
            and isinstance(spec.get("series"), list)
            and len(spec["series"]) > 1
            and all(len(s["valores"]) == len(spec["rotulos"]) for s in spec["series"])
        ):
            tipo = "barra_agrupada"
    elif tipo == "comparativo":
        colunas = spec.get("colunas")
        if not isinstance(colunas, list) or len(colunas) < 2:
            raise ChartError("comparativo precisa de pelo menos 2 colunas")
        for col in colunas:
            if not isinstance(col, dict) or not col.get("titulo") or not col.get("itens"):
                raise ChartError("coluna do comparativo invalida")
            col["itens"] = [_diag_item(i) for i in col["itens"]]
    spec["tipo"] = tipo
    spec["titulo"] = titulo
    fonte = str(spec.get("fonte", "")).strip()
    m_fonte = re.fullmatch(r"\[\((.+?)\)\]\(https?://[^)]+\)", fonte)
    if m_fonte:
        fonte = m_fonte.group(1).strip()
    m_parens = re.fullmatch(r"\((.+)\)", fonte)
    if m_parens:
        fonte = m_parens.group(1).strip()
    spec["fonte"] = fonte
    spec["nota"] = str(spec.get("nota", "")).strip()
    spec["unidade"] = str(spec.get("unidade", "")).strip()
    return spec


def count_graficos(text: str) -> int:
    return len(GRAFICO_RE.findall(text))


def _wrap_labels(labels: list[str], width: int = 14) -> list[str]:
    out = []
    for label in labels:
        text = str(label)
        if len(text) <= width:
            out.append(text)
        else:
            out.append("\n".join(textwrap.wrap(text, width=width)))
    return out


def _ylabel(unidade: str) -> str:
    text = str(unidade or "").strip()
    if not text:
        return "valor"
    if "%" in text:
        return "(%)"
    if "R$" in text:
        return "(R$)"
    if text.startswith("(") and text.endswith(")"):
        return text
    return f"({text})"


def _pct_br(pct: float) -> str:
    return f"{pct:.1f}".replace(".", ",") + "%"


def _br_number(value: float, _pos=None) -> str:
    av = abs(value)
    if av >= 1_000_000_000:
        escala, div = " bi", 1_000_000_000
    elif av >= 1_000_000:
        escala, div = " mi", 1_000_000
    elif av >= 10_000:
        escala, div = " mil", 1_000
    else:
        escala, div = "", 1.0
    if not escala:
        if value == int(value):
            return f"{int(value):,}".replace(",", ".")
        inteiro, _, decimal = f"{value:,.1f}".partition(".")
        return f"{inteiro.replace(',', '.')},{decimal.rstrip('0').rstrip('.')}"
    scaled = value / div
    if abs(scaled - round(scaled)) < 0.05 and div > 1:
        scaled_txt = f"{abs(round(scaled))}"
    else:
        scaled_txt = f"{abs(scaled):.1f}".rstrip("0").rstrip(".").replace(".", ",")
    signal = "-" if value < 0 else ""
    return f"{signal}{scaled_txt}{escala}"


def _fmt_item(item: str) -> str:
    text = str(item).strip()
    if re.fullmatch(r"\d{4}", text) and 1900 <= int(text) <= 2099:
        return text
    if re.fullmatch(r"\d{4,}", text):
        return _br_number(float(text))
    return text


class ChartRenderer:

    def __init__(self, assets_dir: Path, wipe: bool = True):
        self.assets_dir = assets_dir
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        if wipe:
            for stale in self.assets_dir.glob("figura-*.png"):
                stale.unlink(missing_ok=True)
        self.fig_counter = 0
        self.table_counter = 0

    def render(self, spec: dict) -> str:
        self.fig_counter += 1
        number = self.fig_counter
        tipo = spec["tipo"]
        if tipo in DIAGRAM_TYPES:
            body = self._render_diagram(spec)
        else:
            png = self.assets_dir / f"figura-{number:02d}.png"
            self._render_math(spec, png)
            body = f'<img src="assets/{png.name}" alt="Figura {number} - {spec["titulo"]}">'
        fonte = f"Fonte: ({spec['fonte']})." if spec.get("fonte") else "Fonte: elaboracao propria."
        nota = f" {spec['nota']}" if spec.get("nota") else ""
        return (
            f'<figure class="figura">{body}'
            f"<figcaption><strong>Figura {number} \u2013 {spec['titulo']}.</strong> "
            f"<em>{fonte}</em>{nota}</figcaption></figure>"
        )

    def next_table_number(self) -> int:
        self.table_counter += 1
        return self.table_counter

    def _style(self):
        plt.rcParams.update(
            {
                "font.family": "Arial",
                "font.size": 11,
                "axes.titlesize": 11,
                "axes.labelsize": 11,
                "xtick.labelsize": 10,
                "ytick.labelsize": 10,
                "figure.dpi": 200,
                "savefig.dpi": 200,
                "axes.grid": True,
                "grid.color": "#cccccc",
                "grid.alpha": 0.5,
                "grid.linewidth": 0.6,
                "axes.spines.top": False,
                "axes.spines.right": False,
                "axes.edgecolor": "#666666",
            }
        )

    def _render_math(self, spec: dict, png: Path) -> None:
        self._style()
        tipo = spec["tipo"]
        valores = spec["valores"]
        rotulos = spec.get("rotulos", [])
        ylabel = _ylabel(spec.get("unidade", ""))
        if tipo in ("pizza", "donut"):
            fig, ax = plt.subplots(figsize=(7.0, 3.8))
            total = sum(valores) or 1.0

            def _autopct(pct: float) -> str:
                return _pct_br(pct) if pct >= 8 else ""

            wedges, _, auto = ax.pie(
                valores,
                labels=None,
                autopct=_autopct,
                startangle=90,
                colors=PALETTE[: len(valores)],
                wedgeprops={"width": 0.45} if tipo == "donut" else None,
                pctdistance=0.75 if tipo == "donut" else 0.6,
            )
            for t in auto:
                t.set_fontsize(9.5)
                t.set_color("#ffffff")
            legend_labels = []
            for rotulo, v in zip(rotulos, valores):
                pct = v / total * 100
                legend_labels.append(
                    str(rotulo) if pct >= 8 else f"{rotulo} ({_pct_br(pct)})"
                )
            ax.legend(
                wedges,
                legend_labels,
                loc="center left",
                bbox_to_anchor=(1.02, 0.5),
                fontsize=9.5,
                frameon=False,
            )
            ax.grid(False)
            ax.set_aspect("equal")
        elif tipo == "linha":
            fig, ax = plt.subplots(figsize=(7.0, 3.8))
            series = spec.get("series") or [{"nome": spec["titulo"], "valores": valores}]
            eixos = rotulos if rotulos else [str(i + 1) for i in range(len(series[0]["valores"]))]
            n = len(eixos)
            for i, s in enumerate(series):
                ax.plot(range(n), s["valores"], marker="o", linewidth=2,
                        color=PALETTE[i % len(PALETTE)], label=s["nome"])
                finite = [(xi, v) for xi, v in enumerate(s["valores"]) if v == v]
                if len(finite) <= 12:
                    pontos = finite
                else:
                    idxs = {finite[0][0], finite[-1][0]}
                    idxs.add(max(finite, key=lambda t: t[1])[0])
                    idxs.add(min(finite, key=lambda t: t[1])[0])
                    pontos = [(xi, v) for xi, v in finite if xi in idxs]
                for xi, v in pontos:
                    ax.annotate(_br_number(v), (xi, v), textcoords="offset points",
                                xytext=(0, 7), ha="center", fontsize=8.5)
            ax.set_xticks(range(n))
            kept = list(range(n))
            if n > 16:
                step = max(1, n // 8)
                keep = set(range(0, n, step)) | {n - 1}
                kept = [xi for xi in range(n) if xi in keep]
                ax.set_xticks(kept)
            ax.set_xticklabels([str(eixos[xi]) for xi in kept], fontsize=9)
            ax.set_ylabel(ylabel)
            ax.yaxis.set_major_formatter(FuncFormatter(_br_number))
            ax.margins(y=0.12)
            if len(series) > 1:
                ax.legend(fontsize=9, frameon=False)
        else:
            if tipo == "hbar":
                fig, ax = plt.subplots(figsize=(7.0, max(3.0, 0.42 * len(valores) + 1.0)))
                ypos = range(len(valores))
                ax.barh(list(ypos), valores, color=PALETTE[0], height=0.6)
                ax.set_yticks(list(ypos))
                ax.set_yticklabels(_wrap_labels(rotulos, width=18), fontsize=9)
                ax.invert_yaxis()
                ax.grid(axis="x", alpha=0.5)
                ax.grid(axis="y", visible=False)
                span = max(valores) if valores else 1
                for i, v in enumerate(valores):
                    ax.text(v + span * 0.015, i, _br_number(v), va="center", fontsize=9.5)
                ax.set_xlim(0, span * 1.14)
                ax.xaxis.set_major_formatter(FuncFormatter(_br_number))
            elif tipo == "barra_empilhada":
                fig, ax = plt.subplots(figsize=(7.0, 3.8))
                series = spec.get("series") or []
                bottom = [0.0] * len(valores)
                for i, s in enumerate(series):
                    ax.bar(rotulos, s["valores"], bottom=bottom,
                           color=PALETTE[i % len(PALETTE)], label=s["nome"], width=0.6)
                    bottom = [b + v for b, v in zip(bottom, s["valores"])]
                ax.set_ylabel(ylabel)
                ax.yaxis.set_major_formatter(FuncFormatter(_br_number))
                ax.legend(fontsize=9, frameon=False)
                if len(rotulos) > 4 or any(len(str(r)) > 10 for r in rotulos):
                    ax.set_xticks(range(len(rotulos)))
                    ax.set_xticklabels(_wrap_labels(rotulos), fontsize=8.5)
            elif tipo == "barra_agrupada":
                fig, ax = plt.subplots(figsize=(7.0, 3.8))
                series = spec.get("series") or []
                eixo_x = list(range(len(rotulos)))
                largura = 0.8 / max(len(series), 1)
                topo = max(
                    [max(s["valores"]) for s in series if s["valores"]] or valores or [1.0]
                )
                for i, s in enumerate(series):
                    centros = [x + (i - (len(series) - 1) / 2) * largura for x in eixo_x]
                    barras = ax.bar(centros, s["valores"], largura * 0.92,
                                    color=PALETTE[i % len(PALETTE)], label=s["nome"])
                    for rect, v in zip(barras, s["valores"]):
                        ax.text(rect.get_x() + rect.get_width() / 2, v + topo * 0.015,
                                _br_number(v), ha="center", fontsize=8.5)
                ax.set_xticks(eixo_x)
                ax.set_xticklabels(_wrap_labels(rotulos, width=12), fontsize=9)
                ax.set_ylabel(ylabel)
                ax.yaxis.set_major_formatter(FuncFormatter(_br_number))
                ax.set_ylim(0, topo * 1.18)
                if len(series) > 1:
                    ax.legend(fontsize=9, frameon=False)
            else:
                fig, ax = plt.subplots(figsize=(7.0, 3.8))
                ax.bar(range(len(valores)), valores, color=PALETTE[0], width=0.58)
                ax.set_ylabel(ylabel)
                ax.yaxis.set_major_formatter(FuncFormatter(_br_number))
                if rotulos:
                    ax.set_xticks(range(len(rotulos)))
                    ax.set_xticklabels(_wrap_labels(rotulos, width=16), fontsize=9)
                top = max(valores) if valores else 1
                ax.set_ylim(0, top * 1.15)
                for i, v in enumerate(valores):
                    ax.text(i, v + top * 0.02, _br_number(v), ha="center", fontsize=9.5)
        fig.tight_layout()
        fig.savefig(png, bbox_inches="tight", facecolor="white")
        plt.close(fig)

    def _render_diagram(self, spec: dict) -> str:
        cols = "".join(
            '<div class="diag-coluna"><h4>{}</h4><ul>{}</ul></div>'.format(
                col["titulo"],
                "".join(f"<li>{_fmt_item(item)}</li>" for item in col["itens"]),
            )
            for col in spec["colunas"]
        )
        return f'<div class="diagrama comparativo">{cols}</div>'
