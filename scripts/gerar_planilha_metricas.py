"""
DetectaRisco - monta docs/metricas.xlsx a partir de dois scrapes do GET /metrics.

A planilha NAO guarda resultados prontos: guarda os contadores crus dos dois
scrapes e FORMULAS de Excel que calculam, a partir deles, o histograma e o
boxplot da latencia, a taxa de erros e a distribuicao da saida do modelo.
Trocar os valores das colunas t0/t1 (ou rodar este script com scrapes novos)
recalcula tudo. O passo a passo das contas esta em docs/METRICAS.md.

Uso:
    curl -s http://127.0.0.1:3000/metrics > scrape_t0.txt
    ... gera carga ...
    curl -s http://127.0.0.1:3000/metrics > scrape_t1.txt
    uv run --with openpyxl python scripts/gerar_planilha_metricas.py scrape_t0.txt scrape_t1.txt

openpyxl nao e dependencia do servico; `uv run --with` instala so para esta execucao.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from prometheus_client.parser import text_string_to_metric_families

RAIZ = Path(__file__).resolve().parents[1]

AZUL = PatternFill("solid", fgColor="DDEBF7")
AMARELO = PatternFill("solid", fgColor="FFF2CC")
CINZA = PatternFill("solid", fgColor="EDEDED")
NEGRITO = Font(bold=True)
TITULO = Font(bold=True, size=14)


# --------------------------------------------------------------------------
# leitura do formato de exposicao do Prometheus
# --------------------------------------------------------------------------

def ler_scrape(caminho: Path) -> list[tuple[str, dict, float]]:
    texto = caminho.read_text(encoding="utf-8")
    amostras = []
    for familia in text_string_to_metric_families(texto):
        for a in familia.samples:
            amostras.append((a.name, dict(a.labels), a.value))
    return amostras


def valor(amostras, nome, **filtro) -> float:
    total = 0.0
    for n, labels, v in amostras:
        if n == nome and all(labels.get(k) == str(val) for k, val in filtro.items()):
            total += v
    return total


def buckets(amostras, nome, **filtro) -> list[tuple[float, float]]:
    """[(le, contagem_acumulada)] somando por le (ex.: varios model_version)."""
    por_le: dict[float, float] = {}
    for n, labels, v in amostras:
        if n == nome + "_bucket" and all(labels.get(k) == str(val) for k, val in filtro.items()):
            le = float(labels["le"])
            por_le[le] = por_le.get(le, 0.0) + v
    return sorted(por_le.items())


def rotulos(amostras, nome, label) -> list[str]:
    return sorted({labels[label] for n, labels, _ in amostras if n == nome and label in labels})


# --------------------------------------------------------------------------
# blocos da planilha
# --------------------------------------------------------------------------

def largura(ws, larguras: dict[str, int]) -> None:
    for col, w in larguras.items():
        ws.column_dimensions[col].width = w


def bloco_histograma(ws, linha0: int, titulo: str, unidade: str, fator: float,
                     b0: list, b1: list, soma0: float, soma1: float, nome_metrica: str) -> dict:
    """Tabela bucket a bucket + quantis + boxplot, tudo em formula. Devolve enderecos uteis."""
    ws.cell(linha0, 1, titulo).font = TITULO
    ws.cell(linha0 + 1, 1, f"Metrica: {nome_metrica}_bucket  (buckets cumulativos: le = 'menor ou igual a')").font = Font(italic=True)

    cab = linha0 + 3
    cabecalhos = [f"le (bruto)", f"limite sup. ({unidade})", "acumulado t0", "acumulado t1",
                  "acumulado na janela (t1-t0)", "frequencia na faixa", f"limite inf. ({unidade})",
                  "faixa", "% da faixa", "% acumulado"]
    for j, texto in enumerate(cabecalhos, start=1):
        c = ws.cell(cab, j, texto)
        c.font, c.fill = NEGRITO, CINZA
        c.alignment = Alignment(wrap_text=True, vertical="top")

    primeira = cab + 1
    ultima = primeira + len(b1) - 1
    for i, ((le, acum0), (_, acum1)) in enumerate(zip(b0, b1)):
        r = primeira + i
        inf = math.isinf(le)
        ws.cell(r, 1, "+Inf" if inf else le)
        # limite superior em unidade legivel; +Inf fica vazio (nao ha limite)
        ws.cell(r, 2, None if inf else f"=A{r}*{fator}")
        ws.cell(r, 3, acum0).fill = AMARELO
        ws.cell(r, 4, acum1).fill = AMARELO
        ws.cell(r, 5, f"=D{r}-C{r}")
        ws.cell(r, 6, f"=E{r}" if i == 0 else f"=E{r}-E{r - 1}")
        ws.cell(r, 7, 0 if i == 0 else f"=B{r - 1}")
        ws.cell(r, 8, f'=TEXT(G{r},"0.###")&" - "&TEXT(B{r},"0.###")' if not inf
                else f'="> "&TEXT(G{r},"0.###")')
        ws.cell(r, 9, f"=IF($E${ultima}=0,0,F{r}/$E${ultima})").number_format = "0.0%"
        ws.cell(r, 10, f"=IF($E${ultima}=0,0,E{r}/$E${ultima})").number_format = "0.0%"

    rng = lambda col: f"${col}${primeira}:${col}${ultima}"  # noqa: E731
    E, F, G, B = rng("E"), rng("F"), rng("G"), rng("B")

    # ---- resumo: N, media, quantis por interpolacao linear (= histogram_quantile) ----
    r = ultima + 2
    ws.cell(r, 1, "Resumo da janela").font = NEGRITO
    ws.cell(r + 1, 1, "_sum t0");  ws.cell(r + 1, 2, soma0).fill = AMARELO
    ws.cell(r + 2, 1, "_sum t1");  ws.cell(r + 2, 2, soma1).fill = AMARELO
    ws.cell(r + 3, 1, "N (observacoes)"); ws.cell(r + 3, 2, f"=E{ultima}")
    ws.cell(r + 4, 1, f"media ({unidade})")
    ws.cell(r + 4, 2, f"=IF(B{r + 3}=0,\"\",(B{r + 2}-B{r + 1})/B{r + 3}*{fator})")
    enderecos = {"N": f"B{r + 3}", "media": f"B{r + 4}"}

    rq = r + 6
    for j, texto in enumerate(["quantil q", "posto = q*N", "indice do bucket", "limite inf.",
                               "limite sup.", "acum. antes do bucket", "freq. do bucket",
                               f"valor estimado ({unidade})"], start=1):
        c = ws.cell(rq, j, texto)
        c.font, c.fill = NEGRITO, CINZA
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for k, q in enumerate([0.25, 0.5, 0.75, 0.95, 0.99]):
        x = rq + 1 + k
        ws.cell(x, 1, q).fill = AMARELO
        ws.cell(x, 2, f"=A{x}*{enderecos['N']}")
        # primeiro bucket cujo acumulado alcanca o posto (acumulado e crescente)
        ws.cell(x, 3, f'=MIN(COUNTIF({E},"<"&B{x})+1,ROWS({E}))')
        ws.cell(x, 4, f"=INDEX({G},C{x})")
        ws.cell(x, 5, f"=INDEX({B},C{x})")
        ws.cell(x, 6, f"=INDEX({E},C{x})-INDEX({F},C{x})")
        ws.cell(x, 7, f"=INDEX({F},C{x})")
        # bucket +Inf nao tem limite superior: devolve o limite inferior (como o Prometheus)
        ws.cell(x, 8, f'=IF({enderecos["N"]}=0,"",IF(E{x}="",D{x},D{x}+(E{x}-D{x})*(B{x}-F{x})/G{x}))')
        ws.cell(x, 8).number_format = "0.000"
        enderecos[f"q{q}"] = f"H{x}"

    # ---- boxplot (5 numeros) ----
    rb = rq + 8
    ws.cell(rb, 1, "Boxplot (aproximado pelos buckets)").font = NEGRITO
    linhas_box = [
        ("minimo aprox. (limite inf. do 1o bucket nao vazio)", f'=INDEX({G},COUNTIF({E},0)+1)'),
        ("Q1", f"={enderecos['q0.25']}"),
        ("mediana", f"={enderecos['q0.5']}"),
        ("Q3", f"={enderecos['q0.75']}"),
        ("IQR = Q3 - Q1", f"=B{rb + 4}-B{rb + 2}"),
        ("maximo aprox. (limite sup. do bucket que fecha N)",
         f'=IF(INDEX({B},MIN(COUNTIF({E},"<"&{enderecos["N"]})+1,ROWS({E})))="",'
         f'INDEX({G},ROWS({E})),INDEX({B},MIN(COUNTIF({E},"<"&{enderecos["N"]})+1,ROWS({E}))))'),
        ("bigode inferior = max(minimo, Q1 - 1,5*IQR)", f"=MAX(B{rb + 1},B{rb + 2}-1.5*B{rb + 5})"),
        ("bigode superior = min(maximo, Q3 + 1,5*IQR)", f"=MIN(B{rb + 6},B{rb + 4}+1.5*B{rb + 5})"),
    ]
    for k, (rotulo, formula) in enumerate(linhas_box, start=1):
        ws.cell(rb + k, 1, rotulo)
        ws.cell(rb + k, 2, formula).number_format = "0.000"
    enderecos.update(box_linha=rb, primeira=primeira, ultima=ultima)

    # ---- grafico do histograma (faixas com frequencia, sem +Inf) ----
    grafico = BarChart()
    grafico.type, grafico.style = "col", 10
    grafico.title = f"Histograma - {titulo}"
    grafico.y_axis.title, grafico.x_axis.title = "pedidos na faixa", unidade
    grafico.add_data(Reference(ws, min_col=6, min_row=cab, max_row=ultima - 1), titles_from_data=True)
    grafico.set_categories(Reference(ws, min_col=8, min_row=primeira, max_row=ultima - 1))
    grafico.legend = None
    grafico.gapWidth = 10
    grafico.height, grafico.width = 8, 18
    ws.add_chart(grafico, f"L{linha0 + 2}")

    # ---- boxplot desenhado como barra empilhada (Excel nao tem boxplot via openpyxl) ----
    rbx = rb + 10
    ws.cell(rbx, 1, "Dados do desenho do boxplot (barra empilhada)").font = Font(italic=True)
    partes = [("base invisivel", f"=B{rb + 7}"), ("bigode inf -> Q1", f"=B{rb + 2}-B{rb + 7}"),
              ("Q1 -> mediana", f"=B{rb + 3}-B{rb + 2}"), ("mediana -> Q3", f"=B{rb + 4}-B{rb + 3}"),
              ("Q3 -> bigode sup", f"=B{rb + 8}-B{rb + 4}")]
    for k, (rotulo, formula) in enumerate(partes):
        ws.cell(rbx + 1, 2 + k, rotulo).font = NEGRITO
        ws.cell(rbx + 2, 2 + k, formula).number_format = "0.000"
    ws.cell(rbx + 2, 1, titulo)
    box = BarChart()
    box.type, box.grouping, box.overlap = "bar", "stacked", 100
    box.title = f"Boxplot - {titulo} ({unidade})"
    box.add_data(Reference(ws, min_col=2, max_col=6, min_row=rbx + 1, max_row=rbx + 2), titles_from_data=True)
    box.set_categories(Reference(ws, min_col=1, min_row=rbx + 2))
    cores = [None, "BFBFBF", "5B9BD5", "2E75B6", "BFBFBF"]
    for serie, cor in zip(box.series, cores):
        serie.graphicalProperties = GraphicalProperties(noFill=cor is None)
        if cor:
            serie.graphicalProperties.solidFill = cor
        else:
            serie.graphicalProperties.line.noFill = True
    box.legend.position = "b"
    box.height, box.width = 5, 18
    ws.add_chart(box, f"L{linha0 + 19}")
    return enderecos


def aba_latencia(wb, s0, s1, endpoint: str) -> None:
    ws = wb.create_sheet("Latencia")
    largura(ws, {"A": 46, "B": 16, "C": 13, "D": 13, "E": 15, "F": 13, "G": 13, "H": 16, "I": 11, "J": 11})
    nome = "bentoml_service_request_duration_seconds"
    filtro = {"endpoint": endpoint, "http_response_code": "200"}
    bloco_histograma(
        ws, 1, f"Latencia de {endpoint} (HTTP 200)", "ms", 1000,
        buckets(s0, nome, **filtro), buckets(s1, nome, **filtro),
        valor(s0, nome + "_sum", **filtro), valor(s1, nome + "_sum", **filtro), nome,
    )


def aba_inferencia(wb, s0, s1) -> None:
    ws = wb.create_sheet("Inferencia")
    largura(ws, {"A": 46, "B": 16, "C": 13, "D": 13, "E": 15, "F": 13, "G": 13, "H": 16, "I": 11, "J": 11})
    nome = "detectarisco_inferencia_segundos"
    bloco_histograma(
        ws, 1, "Tempo de inferencia (busca do perfil + predict_proba)", "ms", 1000,
        buckets(s0, nome), buckets(s1, nome),
        valor(s0, nome + "_sum"), valor(s1, nome + "_sum"), nome,
    )
    ws.cell(3, 1, "Comparar com a aba Latencia: a diferenca e o custo de HTTP, validacao e fila.").font = Font(italic=True)


def aba_erros(wb, s0, s1, t0: str, t1: str) -> None:
    ws = wb.create_sheet("Taxa de erros")
    largura(ws, {"A": 22, "B": 20, "C": 14, "D": 14, "E": 16, "F": 16, "G": 40})
    ws.cell(1, 1, "Taxa de erros por endpoint").font = TITULO
    ws.cell(2, 1, "Metrica: bentoml_service_request_total{endpoint, http_response_code} (contador)").font = Font(italic=True)

    ws.cell(4, 1, "instante t0"); ws.cell(4, 2, t0).fill = AMARELO
    ws.cell(5, 1, "instante t1"); ws.cell(5, 2, t1).fill = AMARELO
    ws.cell(6, 1, "janela (s)"); ws.cell(6, 2, "=(DATEVALUE(LEFT(B5,10))+TIMEVALUE(MID(B5,12,8))-DATEVALUE(LEFT(B4,10))-TIMEVALUE(MID(B4,12,8)))*86400")

    cab = 8
    for j, texto in enumerate(["endpoint", "http_response_code", "total t0", "total t1",
                               "na janela (t1-t0)", "por segundo", "classe"], start=1):
        c = ws.cell(cab, j, texto)
        c.font, c.fill = NEGRITO, CINZA
    nome = "bentoml_service_request_total"
    combos = sorted({(l["endpoint"], l["http_response_code"]) for n, l, _ in s1 if n == nome})
    combos = [c for c in combos if c[0] in ("/estimar_risco", "/treinar")]
    for i, (ep, codigo) in enumerate(combos):
        r = cab + 1 + i
        ws.cell(r, 1, ep); ws.cell(r, 2, int(codigo))
        ws.cell(r, 3, valor(s0, nome, endpoint=ep, http_response_code=codigo)).fill = AMARELO
        ws.cell(r, 4, valor(s1, nome, endpoint=ep, http_response_code=codigo)).fill = AMARELO
        ws.cell(r, 5, f"=D{r}-C{r}")
        ws.cell(r, 6, f"=IF($B$6=0,\"\",E{r}/$B$6)").number_format = "0.00"
        ws.cell(r, 7, f'=IF(B{r}>=500,"erro do servidor (5xx)",IF(B{r}>=400,"erro do cliente (4xx)","sucesso"))')
    ult = cab + len(combos)
    A, Bc, Ec = f"$A${cab + 1}:$A${ult}", f"$B${cab + 1}:$B${ult}", f"$E${cab + 1}:$E${ult}"

    r = ult + 2
    ws.cell(r, 1, "Resumo /estimar_risco na janela").font = NEGRITO
    resumo = [
        ("total de pedidos", f'=SUMIFS({Ec},{A},"/estimar_risco")', "0"),
        ("erros 4xx", f'=SUMIFS({Ec},{A},"/estimar_risco",{Bc},">=400",{Bc},"<500")', "0"),
        ("erros 5xx", f'=SUMIFS({Ec},{A},"/estimar_risco",{Bc},">=500")', "0"),
        ("taxa de erros (4xx+5xx)/total", f"=IF(B{r + 1}=0,\"\",(B{r + 2}+B{r + 3})/B{r + 1})", "0.00%"),
        ("taxa de erros do servidor 5xx/total", f"=IF(B{r + 1}=0,\"\",B{r + 3}/B{r + 1})", "0.00%"),
        ("vazao (pedidos/s)", f"=IF($B$6=0,\"\",B{r + 1}/$B$6)", "0.00"),
    ]
    for k, (rotulo, formula, fmt) in enumerate(resumo, start=1):
        ws.cell(r + k, 1, rotulo)
        ws.cell(r + k, 2, formula).number_format = fmt

    r2 = r + len(resumo) + 2
    ws.cell(r2, 1, "Conferencia com a metrica propria").font = NEGRITO
    ws.cell(r2 + 1, 1, "detectarisco_erros_total{tipo=erro_validacao} t0")
    ws.cell(r2 + 1, 2, valor(s0, "detectarisco_erros_total", tipo="erro_validacao")).fill = AMARELO
    ws.cell(r2 + 2, 1, "detectarisco_erros_total{tipo=erro_validacao} t1")
    ws.cell(r2 + 2, 2, valor(s1, "detectarisco_erros_total", tipo="erro_validacao")).fill = AMARELO
    ws.cell(r2 + 3, 1, "na janela (deve bater com 'erros 4xx')")
    ws.cell(r2 + 3, 2, f"=B{r2 + 2}-B{r2 + 1}")
    ws.cell(r2 + 5, 1, "INSUFICIENTE (abstencao) responde HTTP 200 - nao e erro de HTTP. "
                       "A taxa de abstencao esta na aba 'Saida do modelo'.").font = Font(italic=True)


def aba_saida(wb, s0, s1) -> None:
    ws = wb.create_sheet("Saida do modelo")
    largura(ws, {"A": 16, "B": 14, "C": 14, "D": 16, "E": 16, "F": 16, "G": 16})
    ws.cell(1, 1, "Distribuicao da saida: janela 1 (boot -> t0) x janela 2 (t0 -> t1)").font = TITULO
    ws.cell(2, 1, "Metrica: detectarisco_predicoes_total{nivel}. Se a proporcao muda entre janelas, "
                  "o padrao de uso (ou o mundo) mudou - investigar no log com KS/PSI.").font = Font(italic=True)
    cab = 4
    for j, texto in enumerate(["nivel", "acumulado t0", "acumulado t1", "janela 1 (=t0)",
                               "janela 2 (t1-t0)", "% janela 1", "% janela 2"], start=1):
        c = ws.cell(cab, j, texto)
        c.font, c.fill = NEGRITO, CINZA
    niveis = ["BAIXO", "MEDIO", "ALTO", "INSUFICIENTE"]
    for i, nivel in enumerate(niveis):
        r = cab + 1 + i
        ws.cell(r, 1, nivel)
        ws.cell(r, 2, valor(s0, "detectarisco_predicoes_total", nivel=nivel)).fill = AMARELO
        ws.cell(r, 3, valor(s1, "detectarisco_predicoes_total", nivel=nivel)).fill = AMARELO
        ws.cell(r, 4, f"=B{r}")
        ws.cell(r, 5, f"=C{r}-B{r}")
        ws.cell(r, 6, f"=IF(SUM($D${cab + 1}:$D${cab + 4})=0,0,D{r}/SUM($D${cab + 1}:$D${cab + 4}))").number_format = "0.0%"
        ws.cell(r, 7, f"=IF(SUM($E${cab + 1}:$E${cab + 4})=0,0,E{r}/SUM($E${cab + 1}:$E${cab + 4}))").number_format = "0.0%"
    ult = cab + len(niveis)
    ws.cell(ult + 1, 1, "PSI (nivel)").font = NEGRITO
    ws.cell(ult + 1, 2, f"=SUMPRODUCT((G{cab + 1}:G{ult - 1}-F{cab + 1}:F{ult - 1})*"
                        f"LN((G{cab + 1}:G{ult - 1}+0.0001)/(F{cab + 1}:F{ult - 1}+0.0001)))").number_format = "0.000"
    ws.cell(ult + 1, 3, "janela 2 contra janela 1, sem INSUFICIENTE. <0,10 estavel | 0,10-0,25 moderado | >0,25 drift")

    grafico = BarChart()
    grafico.type, grafico.title = "col", "Nivel previsto por janela"
    grafico.add_data(Reference(ws, min_col=6, max_col=7, min_row=cab, max_row=ult), titles_from_data=True)
    grafico.set_categories(Reference(ws, min_col=1, min_row=cab + 1, max_row=ult))
    grafico.y_axis.number_format = "0%"
    grafico.height, grafico.width = 8, 16
    ws.add_chart(grafico, "I4")

    # histograma de prob_alto na janela t0->t1
    nome = "detectarisco_prob_alto"
    largura(ws, {"H": 4})
    bloco_histograma(
        ws, 26, "prob_alto (janela t0 -> t1)", "prob", 1,
        buckets(s0, nome), buckets(s1, nome), valor(s0, nome + "_sum"), valor(s1, nome + "_sum"), nome,
    )


def aba_bruto(wb, caminho: Path, nome: str) -> None:
    ws = wb.create_sheet(nome)
    ws.column_dimensions["A"].width = 160
    ws.cell(1, 1, f"Saida crua do GET /metrics ({caminho.name}), uma linha por celula").font = NEGRITO
    for i, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), start=2):
        ws.cell(i, 1, linha)


def aba_leiame(wb, t0: str, t1: str) -> None:
    ws = wb.active
    ws.title = "LEIA-ME"
    ws.column_dimensions["A"].width = 120
    linhas = [
        ("Como ler o GET /metrics do DetectaRisco", TITULO),
        ("Texto completo, com as contas explicadas: docs/METRICAS.md", None),
        ("", None),
        (f"Dois scrapes reais do servico: t0 = {t0}  |  t1 = {t1}", NEGRITO),
        ("Entre o boot e t0: 300 pedidos no cenario 'referencia'. Entre t0 e t1: 300 pedidos no cenario 'drift'.", None),
        ("(scripts/gerar_carga.py, 20 pedidos/s, 4 em paralelo, ~5% fora da malha, ~3% invalidos)", None),
        ("", None),
        ("Celulas AMARELAS = valores copiados do /metrics (entrada). Todo o resto e formula.", NEGRITO),
        ("Para analisar outro periodo: cole os contadores de dois scrapes novos nas colunas t0/t1, ou rode", None),
        ("   uv run --with openpyxl python scripts/gerar_planilha_metricas.py scrape_t0.txt scrape_t1.txt", None),
        ("", None),
        ("Abas", NEGRITO),
        ("  Latencia             histograma, quantis e boxplot da latencia ponta a ponta de /estimar_risco", None),
        ("  Inferencia           o mesmo para o tempo so do modelo (metrica propria)", None),
        ("  Taxa de erros        4xx, 5xx, taxa de erros e vazao na janela", None),
        ("  Saida do modelo      nivel previsto por janela (+ PSI) e histograma de prob_alto", None),
        ("  Scrape t0 / t1       texto cru dos dois scrapes, para conferencia", None),
        ("", None),
        ("Por que sempre DOIS scrapes", NEGRITO),
        ("Contadores e histogramas do Prometheus sao acumulados desde que o processo subiu. Um scrape", None),
        ("sozinho mistura tudo o que aconteceu desde o boot; a diferenca t1 - t0 isola uma janela de tempo.", None),
        ("", None),
        ("Por quanto tempo o servidor guarda isto", NEGRITO),
        ("So na memoria do processo, desde o boot. Nao ha historico: cada scrape traz apenas o total atual.", None),
        ("Reiniciar o servico (ou o container) zera tudo. Serie temporal exige um Prometheus coletando", None),
        ("periodicamente (retencao padrao de 15 dias). O rastro permanente e logs/inferencia.jsonl.", None),
    ]
    for i, (texto, fonte) in enumerate(linhas, start=1):
        c = ws.cell(i, 1, texto)
        if fonte:
            c.font = fonte


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scrape_t0", type=Path)
    parser.add_argument("scrape_t1", type=Path)
    parser.add_argument("--t0", default=None, help="instante do scrape t0 (ISO 8601)")
    parser.add_argument("--t1", default=None, help="instante do scrape t1 (ISO 8601)")
    parser.add_argument("--endpoint", default="/estimar_risco")
    parser.add_argument("--saida", type=Path, default=RAIZ / "docs" / "metricas.xlsx")
    args = parser.parse_args(argv)

    s0, s1 = ler_scrape(args.scrape_t0), ler_scrape(args.scrape_t1)
    fmt = lambda p: datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")  # noqa: E731
    t0, t1 = args.t0 or fmt(args.scrape_t0), args.t1 or fmt(args.scrape_t1)

    wb = Workbook()
    aba_leiame(wb, t0, t1)
    aba_latencia(wb, s0, s1, args.endpoint)
    aba_inferencia(wb, s0, s1)
    aba_erros(wb, s0, s1, t0, t1)
    aba_saida(wb, s0, s1)
    aba_bruto(wb, args.scrape_t0, "Scrape t0")
    aba_bruto(wb, args.scrape_t1, "Scrape t1")
    args.saida.parent.mkdir(exist_ok=True)
    wb.save(args.saida)
    print(f"planilha salva em {args.saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
