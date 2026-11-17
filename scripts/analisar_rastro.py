"""
DetectaRisco - verificacao de suficiencia do rastro (logs/inferencia.jsonl).

Nao e o painel de monitoramento (fica para a proxima entrega). Serve para
responder duas perguntas antes de construi-lo:

  1. O formato do log da conta? -> le o arquivo (e os rotacionados .1, .2...)
     com pandas, sem regex, e monta todas as variaveis de entrada e saida.
  2. Os dados coletados bastam? -> compara cada variavel com a referencia via
     KS e PSI e mede o RUIDO do PSI para o mesmo N: sorteia N linhas da
     propria referencia muitas vezes. Se o PSI observado nao passa do p95
     desse ruido, ainda nao ha dado suficiente para afirmar drift.

Referencia (o conjunto de treino, models/perfis.parquet):
  - entrada:  uf, km_bin, dia_semana, fase_dia e as 10 features do rastro,
              com cada trecho-periodo do universo valendo 1;
  - saida:    prob_alto e nivel que o modelo em producao da para esse mesmo
              universo (predict_proba sobre perfis).

Uso:
    uv run python scripts/analisar_rastro.py                 # log inteiro
    uv run python scripts/analisar_rastro.py --ultimos 400   # so os 400 pedidos mais recentes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import ks_2samp

RAIZ = Path(__file__).resolve().parents[1]
DIR_MODELOS = RAIZ / "models"
# Mesma pasta que o servico usa (src/monitoramento.py), inclusive com DETECTARISCO_LOG_DIR.
DIR_LOGS = Path(os.environ.get("DETECTARISCO_LOG_DIR", RAIZ / "logs"))
CATEGORICAS = ["uf", "dia_semana", "fase_dia", "nivel"]


def ler_rastro(caminho: Path) -> pd.DataFrame:
    """Le o arquivo atual e os rotacionados, do mais antigo ao mais novo."""
    rotacionados = sorted(caminho.parent.glob(caminho.name + ".*"),
                          key=lambda p: int(p.suffix[1:]) if p.suffix[1:].isdigit() else 0,
                          reverse=True)
    if not caminho.exists() and not rotacionados:
        raise SystemExit(f"{caminho} nao existe. Suba o servico e gere carga antes (scripts/gerar_carga.py).")
    partes = [pd.read_json(p, lines=True) for p in [*rotacionados, caminho]
              if p.exists() and p.stat().st_size]
    if not partes:
        raise SystemExit(f"{caminho} esta vazio. Gere carga antes (scripts/gerar_carga.py).")
    bruto = pd.concat(partes, ignore_index=True)
    return bruto[bruto["endpoint"] == "/estimar_risco"].sort_values("ts_epoch")


def achatar(rastro: pd.DataFrame) -> pd.DataFrame:
    """Uma coluna por variavel: entrada_norm.*, features.*, saida.*."""
    blocos = [rastro[["ts", "status", "http_status", "latencia_ms"]].reset_index(drop=True)]
    for bloco in ("entrada_norm", "features", "saida"):
        if bloco in rastro:
            blocos.append(pd.json_normalize(rastro[bloco].where(rastro[bloco].notna(), {}).tolist()))
    df = pd.concat(blocos, axis=1)
    return df.loc[:, ~df.columns.duplicated()]


def referencia(features_rastro: list[str]) -> pd.DataFrame:
    perfis = pd.read_parquet(DIR_MODELOS / "perfis.parquet")
    metadata = json.loads((DIR_MODELOS / "metadata.json").read_text(encoding="utf-8"))
    modelo = joblib.load(DIR_MODELOS / "model.pkl")
    probas = modelo.predict_proba(perfis[metadata["features"]])
    classes = list(modelo.classes_)
    ref = perfis[["uf", "km_bin", "dia_semana", "fase_dia",
                  *[f for f in features_rastro if f not in ("fase_dia",)]]].copy()
    ref["prob_alto"] = probas[:, classes.index("ALTO")]
    ref["nivel"] = np.array(classes)[probas.argmax(axis=1)]
    return ref


def psi(ref: pd.Series, atual: pd.Series, categorica: bool, faixas: int = 10) -> float:
    eps = 1e-4
    if categorica:
        p = ref.value_counts(normalize=True)
        q = atual.value_counts(normalize=True)
        idx = p.index.union(q.index)
        p, q = p.reindex(idx, fill_value=0).to_numpy(), q.reindex(idx, fill_value=0).to_numpy()
    else:
        # Faixas pelos quantis da referencia. Variaveis com muitos valores
        # repetidos (contagens com muitos zeros) geram cortes iguais - unique
        # junta essas faixas em vez de criar faixa vazia.
        cortes = np.unique(np.quantile(ref, np.linspace(0, 1, faixas + 1)))
        cortes[0], cortes[-1] = -np.inf, np.inf
        p = np.histogram(ref, cortes)[0] / len(ref)
        q = np.histogram(atual, cortes)[0] / len(atual)
    p, q = np.clip(p, eps, None), np.clip(q, eps, None)
    return float(np.sum((q - p) * np.log(q / p)))


def ruido_psi(ref: pd.Series, n: int, categorica: bool, rng, repeticoes: int = 200) -> float:
    """p95 do PSI entre a referencia e amostras de tamanho n DELA MESMA (sem drift)."""
    valores = [psi(ref, ref.sample(n, replace=True, random_state=rng.integers(2**32)), categorica)
               for _ in range(repeticoes)]
    return float(np.quantile(valores, 0.95))


def leitura_psi(valor: float) -> str:
    return "estavel" if valor < 0.10 else ("moderado" if valor < 0.25 else "DRIFT")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", type=Path, default=DIR_LOGS / "inferencia.jsonl")
    parser.add_argument("--ultimos", type=int, default=None,
                        help="analisa so os N pedidos mais recentes")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    rastro = ler_rastro(args.log)
    if args.ultimos:
        rastro = rastro.tail(args.ultimos)
    df = achatar(rastro)
    ok = df[df["status"] == "ok"]

    print(f"{len(df)} pedidos em /estimar_risco  ({df['ts'].iloc[0]}  ->  {df['ts'].iloc[-1]})")
    print("status:", df["status"].value_counts().to_dict())
    lat = df["latencia_ms"]
    print(f"latencia ms: min {lat.min():.1f} | Q1 {lat.quantile(.25):.1f} | mediana {lat.median():.1f}"
          f" | Q3 {lat.quantile(.75):.1f} | p95 {lat.quantile(.95):.1f} | max {lat.max():.1f}")
    taxa_insuf = (df["status"] == "insuficiente").mean()
    print(f"taxa de abstencao (INSUFICIENTE): {taxa_insuf:.1%}  "
          f"(na referencia e 0% por construcao: so trechos qualificados)\n")

    features_rastro = [c for c in ok.columns if c.startswith(("tr_", "tp_"))]
    ref = referencia(features_rastro)
    rng = np.random.default_rng(args.seed)

    variaveis = (["uf", "dia_semana", "fase_dia", "km_bin"] + features_rastro + ["prob_alto", "nivel"])
    linhas = []
    for var in variaveis:
        if var not in ok or var not in ref:
            continue
        atual = ok[var].dropna()
        categorica = var in CATEGORICAS
        valor_psi = psi(ref[var], atual, categorica)
        ks = ks_2samp(ref[var], atual) if not categorica else None
        linhas.append({
            "variavel": var,
            "tipo": "entrada" if var not in ("prob_alto", "nivel") else "saida",
            "n": len(atual),
            "KS": None if ks is None else round(ks.statistic, 3),
            "KS_p": None if ks is None else float(f"{ks.pvalue:.2g}"),
            "PSI": round(valor_psi, 3),
            "ruido_PSI_p95": round(ruido_psi(ref[var], len(atual), categorica, rng), 3),
            "leitura": leitura_psi(valor_psi),
        })

    tabela = pd.DataFrame(linhas)
    tabela["acima_do_ruido"] = np.where(tabela.PSI > tabela.ruido_PSI_p95, "sim", "nao")
    pd.set_option("display.width", 200)
    print(tabela.to_string(index=False))
    print("\nLeitura:")
    print("  KS (so numericas): p < 0,05 -> distribuicao do log difere da referencia.")
    print("  PSI: < 0,10 estavel | 0,10-0,25 moderado | > 0,25 drift.")
    print("  ruido_PSI_p95: PSI que aparece SO por acaso com este N, sem drift nenhum.")
    print("  Se PSI <= ruido, ainda nao ha pedidos suficientes para afirmar nada sobre a variavel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
