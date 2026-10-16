"""Renderizacao de graficos do relatorio: matplotlib (quantitativo) e HTML (diagramas)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

GRAFICO_RE = re.compile(r"```grafico\s*\n(.*?)\n```", re.DOTALL)

PALETTE = ["#0b3d62", "#c45d2b", "#3d8b57", "#7c5cbf", "#b8860b", "#b03a2e", "#2b7a9b"]
MATH_TYPES = {"barra", "barras", "pizza", "donut", "linha", "barra_empilhada", "hbar"}
DIAGRAM_TYPES = {"comparativo", "fluxo"}
ALLOWED_TYPES = MATH_TYPES | DIAGRAM_TYPES


class ChartError(ValueError):
    """Estrutura de grafico invalida ou dados nao numericos."""


def parse_spec(raw: str) -> dict:
    """Converte o conteudo de um bloco ```grafico em dicionario validado."""
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
        valores = spec.get("valores")
        if not isinstance(valores, list) or not valores:
            raise ChartError("grafico quantitativo sem valores")
        try:
            spec["valores"] = [float(v) for v in valores]
        except (TypeError, ValueError) as exc:
            raise ChartError("valores devem ser numericos") from exc
        rotulos = spec.get("rotulos")
        if tipo != "linha" or not isinstance(spec.get("series"), list):
            if not isinstance(rotulos, list) or not rotulos:
                raise ChartError("grafico sem rotulos")
            if len(rotulos) != len(spec["valores"]):
                raise ChartError("rotulos e valores com tamanhos diferentes")
            spec["rotulos"] = [str(r) for r in rotulos]
        series = spec.get("series")
        if isinstance(series, list):
            clean = []
            for s in series:
                if not isinstance(s, dict) or not s.get("nome"):
                    raise ChartError("serie invalida")
                vals = [float(v) for v in s.get("valores", [])]
                if not vals:
                    raise ChartError("serie sem valores")
                clean.append({"nome": str(s["nome"]), "valores": vals})
            spec["series"] = clean
    elif tipo == "comparativo":
        colunas = spec.get("colunas")
        if not isinstance(colunas, list) or len(colunas) < 2:
            raise ChartError("comparativo precisa de pelo menos 2 colunas")
        for col in colunas:
            if not isinstance(col, dict) or not col.get("titulo") or not col.get("itens"):
                raise ChartError("coluna do comparativo invalida")
            col["itens"] = [str(i) for i in col["itens"]]
    elif tipo == "fluxo":
        etapas = spec.get("etapas")
        if not isinstance(etapas, list) or len(etapas) < 2:
            raise ChartError("fluxo precisa de pelo menos 2 etapas")
        spec["etapas"] = [str(e) for e in etapas]
    spec["tipo"] = tipo
    spec["titulo"] = titulo
    spec["fonte"] = str(spec.get("fonte", "")).strip()
    spec["nota"] = str(spec.get("nota", "")).strip()
    spec["unidade"] = str(spec.get("unidade", "")).strip()
    return spec


def count_graficos(text: str) -> int:
    """Conta blocos ```grafico``` validos em um texto Markdown."""
    return len(GRAFICO_RE.findall(text))


class ChartRenderer:
    """Atribui numeracao ABNT a figuras e renderiza cada especificacao."""

    def __init__(self, assets_dir: Path):
        self.assets_dir = assets_dir
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.fig_counter = 0
        self.table_counter = 0

    def render(self, spec: dict) -> str:
        """Renderiza uma especificacao e devolve o bloco HTML com legenda ABNT."""
        self.fig_counter += 1
        number = self.fig_counter
        tipo = spec["tipo"]
        if tipo in DIAGRAM_TYPES:
            body = self._render_diagram(spec)
        else:
            png = self.assets_dir / f"figura-{number:02d}.png"
            self._render_math(spec, png)
            body = f'<img src="assets/{png.name}" alt="Figura {number} - {spec["titulo"]}">'
        fonte = f"Fonte: ({spec['fonte']})." if spec["fonte"] else "Fonte: elaboracao propria."
        nota = f" {spec['nota']}" if spec["nota"] else ""
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
        unidade = f" ({spec['unidade']})" if spec["unidade"] else ""
        if tipo in ("pizza", "donut"):
            fig, ax = plt.subplots(figsize=(7.0, 3.8))
            wedges, _, auto = ax.pie(
                valores,
                labels=None,
                autopct="%1.1f%%",
                startangle=90,
                colors=PALETTE[: len(valores)],
                wedgeprops={"width": 0.45} if tipo == "donut" else None,
                pctdistance=0.75 if tipo == "donut" else 0.6,
            )
            for t in auto:
                t.set_fontsize(9.5)
                t.set_color("#ffffff")
            ax.legend(
                wedges,
                [f"{r}" for r in rotulos],
                loc="center left",
                bbox_to_anchor=(1.0, 0.5),
                fontsize=9.5,
                frameon=False,
            )
            ax.grid(False)
            ax.set_aspect("equal")
        elif tipo == "linha":
            fig, ax = plt.subplots(figsize=(7.0, 3.8))
            series = spec.get("series") or [{"nome": spec["titulo"], "valores": valores}]
            eixos = rotulos if rotulos else [str(i + 1) for i in range(len(series[0]["valores"]))]
            for i, s in enumerate(series):
                ax.plot(eixos, s["valores"], marker="o", linewidth=2,
                        color=PALETTE[i % len(PALETTE)], label=s["nome"])
            ax.set_ylabel(unidade.strip() or "valor")
            ax.margins(y=0.12)
            if len(series) > 1:
                ax.legend(fontsize=9, frameon=False)
            if len(eixos) > 8:
                ax.tick_params(axis="x", rotation=30, labelsize=9)
        else:
            if tipo == "hbar":
                fig, ax = plt.subplots(figsize=(7.0, max(3.0, 0.42 * len(valores) + 1.0)))
                ypos = range(len(valores))
                ax.barh(list(ypos), valores, color=PALETTE[0], height=0.6)
                ax.set_yticks(list(ypos))
                ax.set_yticklabels(rotulos, fontsize=10)
                ax.invert_yaxis()
                ax.grid(axis="x", alpha=0.5)
                ax.grid(axis="y", visible=False)
                span = max(valores) if valores else 1
                for i, v in enumerate(valores):
                    ax.text(v + span * 0.015, i, f"{v:g}", va="center", fontsize=9.5)
                ax.set_xlim(0, span * 1.12)
            elif tipo == "barra_empilhada":
                fig, ax = plt.subplots(figsize=(7.0, 3.8))
                series = spec.get("series") or []
                bottom = [0.0] * len(valores)
                for i, s in enumerate(series):
                    ax.bar(rotulos, s["valores"], bottom=bottom,
                           color=PALETTE[i % len(PALETTE)], label=s["nome"], width=0.6)
                    bottom = [b + v for b, v in zip(bottom, s["valores"])]
                ax.set_ylabel(unidade.strip() or "valor")
                ax.legend(fontsize=9, frameon=False)
                if len(rotulos) > 5:
                    ax.tick_params(axis="x", rotation=20, labelsize=9)
            else:
                fig, ax = plt.subplots(figsize=(7.0, 3.8))
                ax.bar(rotulos, valores, color=PALETTE[0], width=0.58)
                ax.set_ylabel(unidade.strip() or "valor")
                if len(rotulos) > 5:
                    ax.tick_params(axis="x", rotation=20, labelsize=9)
                top = max(valores) if valores else 1
                ax.set_ylim(0, top * 1.15)
                for i, v in enumerate(valores):
                    ax.text(i, v + top * 0.02, f"{v:g}", ha="center", fontsize=9.5)
        fig.tight_layout()
        fig.savefig(png, bbox_inches="tight", facecolor="white")
        plt.close(fig)

    def _render_diagram(self, spec: dict) -> str:
        if spec["tipo"] == "comparativo":
            cols = "".join(
                '<div class="diag-coluna"><h4>{}</h4><ul>{}</ul></div>'.format(
                    col["titulo"],
                    "".join(f"<li>{item}</li>" for item in col["itens"]),
                )
                for col in spec["colunas"]
            )
            return f'<div class="diagrama comparativo">{cols}</div>'
        etapas = spec["etapas"]
        boxes = []
        for i, etapa in enumerate(etapas, 1):
            arrow = '<span class="diag-seta">&#9654;</span>' if i < len(etapas) else ""
            boxes.append(
                f'<div class="diag-etapa"><span class="diag-num">{i}</span>{etapa}</div>{arrow}'
            )
        return f'<div class="diagrama fluxo">{"".join(boxes)}</div>'
