"""
Arquivo para treinar o modelo.

Aprende sobre o historico 2020-2024 e e avaliado contra o que de fato
aconteceu em 2025. Nenhuma coluna do ano-alvo entra como preditor.

O modelo so e aceito se bater a pratica atual, representada pelo
baseline historico (secao 1.1 do documento): mandar viatura para onde
os acidentes graves ja se acumularam.

Uso:
    python src/train.py
    python src/train.py --test-size 0.3 --seed 7
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import classification_report, confusion_matrix, f1_score, recall_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder
from sklearn.utils.class_weight import compute_sample_weight

RAIZ = Path(__file__).resolve().parents[1]
DIR_DADOS = RAIZ / "data"
DIR_MODELOS = RAIZ / "models"

ALVO = "nivel_risco"
CLASSES = ["BAIXO", "MEDIO", "ALTO"]
CATEGORICAS = ["uf", "dia_semana", "fase_dia"]

# br e km_bin identificam o trecho. Deixa-los entrar faria o modelo decorar
# corredores especificos em vez de aprender o que torna um trecho perigoso.
IDENTIFICADORAS = ["br", "km_bin"]


def carregar() -> pd.DataFrame:
    caminho = DIR_DADOS / "treino.parquet"
    if not caminho.exists():
        raise FileNotFoundError(
            f"{caminho} nao existe. Rode antes:\n"
            "  python src/build_dataset.py normalize\n"
            "  python src/build_dataset.py build"
        )
    return pd.read_parquet(caminho)


def separar_colunas(df: pd.DataFrame) -> list[str]:
    descartar = set(IDENTIFICADORAS) | {ALVO}
    return [c for c in df.columns if c not in descartar]


def dividir(df: pd.DataFrame, test_size: float, seed: int):
    """Separa por trecho, nunca por linha.

    Cada trecho aparece em 28 linhas (7 dias x 4 fases) que compartilham as
    features tr_*. Dividir por linha deixaria o mesmo trecho nos dois lados e
    inflaria o resultado. O agrupamento mede o que interessa: generalizar
    para pedacos de rodovia que o modelo nao viu.
    """
    grupos = df["uf"].astype(str) + "|" + df["br"].astype(str) + "|" + df["km_bin"].astype(str)
    divisor = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
    idx_treino, idx_teste = next(divisor.split(df, groups=grupos))
    return df.iloc[idx_treino].copy(), df.iloc[idx_teste].copy(), grupos


def baseline_historico(df: pd.DataFrame) -> np.ndarray:
    """A pratica de hoje: olhar onde ja houve acidente grave.

    E a regua obrigatoria das secoes 1.1 e 3.7 do documento de negocio. Se o
    modelo nao bater isso, o projeto nao se justifica.
    """
    houve_grave = (df["tp_n_mortos"] + df["tp_n_fer_graves"]) > 0
    houve_acidente = df["tp_n_acidentes"] > 0
    return np.where(houve_grave, "ALTO", np.where(houve_acidente, "MEDIO", "BAIXO"))


def montar_modelo(colunas: list[str], seed: int) -> Pipeline:
    numericas = [c for c in colunas if c not in CATEGORICAS]
    preparo = ColumnTransformer(
        [("cat", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1),
          CATEGORICAS)],
        remainder="passthrough",
        verbose_feature_names_out=False,
    )
    # O ColumnTransformer poe as categoricas primeiro; o HGB precisa saber
    # quais indices tratar como categoria em vez de numero ordenado.
    indices_categoricos = list(range(len(CATEGORICAS)))
    modelo = HistGradientBoostingClassifier(
        categorical_features=indices_categoricos,
        max_iter=400,
        learning_rate=0.06,
        max_leaf_nodes=31,
        l2_regularization=1.0,
        early_stopping=True,
        validation_fraction=0.15,
        random_state=seed,
    )
    return Pipeline([("preparo", preparo), ("modelo", modelo)]), numericas


def avaliar(nome: str, y_true, y_pred) -> dict:
    recall_alto = recall_score(y_true, y_pred, labels=["ALTO"], average="macro", zero_division=0)
    f1_macro = f1_score(y_true, y_pred, average="macro", zero_division=0)
    print(f"\n{'=' * 62}\n{nome}\n{'=' * 62}")
    print(classification_report(y_true, y_pred, labels=CLASSES, zero_division=0, digits=3))
    matriz = pd.DataFrame(
        confusion_matrix(y_true, y_pred, labels=CLASSES),
        index=[f"real {c}" for c in CLASSES],
        columns=[f"prev {c}" for c in CLASSES],
    )
    print(matriz.to_string())
    return {"recall_alto": float(recall_alto), "f1_macro": float(f1_macro)}


def avaliar_prioridade(y_true, escores: dict[str, np.ndarray], seed: int,
                       fracoes=(0.05, 0.10, 0.20)) -> dict:
    """Compara baseline e modelo sob orcamento fixo.

    D1 nao e uma classificacao, e uma priorizacao: o gestor tem um numero
    fixo de viaturas e precisa saber quais trechos-periodo recebem reforco
    neste ciclo. Comparar recall em pontos de corte diferentes nao diz nada
    - o baseline alcanca recall alto marcando um terco da malha como ALTO,
    o que nao cabe em nenhuma escala real.

    Aqui os dois metodos ordenam a malha inteira e disputam o mesmo
    orcamento: dos K trechos-periodo priorizados, quantos de fato tiveram
    morto ou ferido grave em 2025?
    """
    real_alto = (np.asarray(y_true) == "ALTO")
    n, total_alto = len(real_alto), int(real_alto.sum())
    taxa_base = total_alto / n
    gerador = np.random.default_rng(seed)

    linhas = []
    for frac in fracoes:
        k = int(n * frac)
        for nome, escore in escores.items():
            # Ruido minimo desempata: o baseline empilha milhares de zeros e
            # a ordem entre eles nao carrega informacao nenhuma.
            desempate = escore.astype(float) + gerador.normal(0, 1e-9, n)
            top = np.argsort(-desempate, kind="stable")[:k]
            capturados = int(real_alto[top].sum())
            linhas.append({
                "orcamento": f"{frac:.0%} ({k})",
                "metodo": nome,
                "acertos": capturados,
                "precisao": capturados / k,
                "%_dos_ALTO_capturados": 100 * capturados / total_alto,
                "lift": (capturados / k) / taxa_base,
            })

    tabela = pd.DataFrame(linhas)
    print(f"\n{'=' * 62}\nPRIORIZACAO SOB ORCAMENTO FIXO\n{'=' * 62}")
    print(f"{n} trechos-periodo no teste | {total_alto} com morto ou ferido grave "
          f"em 2025 ({taxa_base:.1%})\n")
    print(tabela.to_string(index=False, float_format=lambda v: f"{v:7.3f}"))
    return {"tabela": linhas}


def importancias(pipeline: Pipeline, X, y, colunas, seed, n=2500) -> list[dict]:
    """Importancia por permutacao, usada para explicar a recomendacao (RE-7)."""
    amostra = min(n, len(X))
    idx = np.random.default_rng(seed).choice(len(X), amostra, replace=False)
    resultado = permutation_importance(
        pipeline, X.iloc[idx], y.iloc[idx],
        n_repeats=3, random_state=seed, scoring="f1_macro", n_jobs=-1,
    )
    ordem = np.argsort(resultado.importances_mean)[::-1]
    return [{"feature": colunas[i], "importancia": float(resultado.importances_mean[i])}
            for i in ordem if resultado.importances_mean[i] > 0]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    df = carregar()
    colunas = separar_colunas(df)
    print(f"{len(df)} trechos-periodo | {len(colunas)} features")
    print(f"Distribuicao do rotulo: "
          f"{df[ALVO].value_counts(normalize=True).mul(100).round(1).to_dict()}")

    treino, teste, _ = dividir(df, args.test_size, args.seed)
    n_trechos_teste = teste.groupby(["uf", "br", "km_bin"]).ngroups
    print(f"\ntreino {len(treino)} linhas | teste {len(teste)} linhas "
          f"({n_trechos_teste} trechos nunca vistos)")

    X_treino, y_treino = treino[colunas], treino[ALVO]
    X_teste, y_teste = teste[colunas], teste[ALVO]

    # ALTO e 11,8% da base. Sem reponderar, o modelo acerta 77% chutando
    # BAIXO em tudo e nao serve para nada.
    pesos = compute_sample_weight("balanced", y_treino)

    pipeline, _ = montar_modelo(colunas, args.seed)
    print("\nTreinando...")
    pipeline.fit(X_treino, y_treino, modelo__sample_weight=pesos)

    metricas_base = avaliar("BASELINE - onde ja houve acidente grave",
                            y_teste, baseline_historico(teste))
    metricas_modelo = avaliar("MODELO - HistGradientBoosting",
                              y_teste, pipeline.predict(X_teste))

    print(f"\n{'=' * 62}")
    print(f"f1 macro      baseline {metricas_base['f1_macro']:.3f}  ->  "
          f"modelo {metricas_modelo['f1_macro']:.3f}")
    print("Os recalls acima nao sao comparaveis: cada metodo marca uma")
    print("quantidade diferente de trechos como ALTO. A comparacao que vale")
    print("para D1 esta na tabela de orcamento fixo, abaixo.")
    print("=" * 62)

    indice_alto = list(pipeline.classes_).index("ALTO")
    prioridade = avaliar_prioridade(
        y_teste,
        {
            "baseline": (teste["tp_n_mortos"] + teste["tp_n_fer_graves"]).to_numpy(),
            "modelo": pipeline.predict_proba(X_teste)[:, indice_alto],
        },
        args.seed,
    )

    print("\nCalculando importancias (RE-7)...")
    top = importancias(pipeline, X_teste, y_teste, colunas, args.seed)
    for item in top[:12]:
        print(f"  {item['importancia']:.4f}  {item['feature']}")

    DIR_MODELOS.mkdir(exist_ok=True)
    joblib.dump(pipeline, DIR_MODELOS / "model.pkl")

    # O servico precisa reconstruir o vetor de features a partir de
    # (uf, br, km, dia_semana, fase_dia). Estes dois parquets sao a consulta.
    df.drop(columns=[ALVO]).to_parquet(DIR_MODELOS / "perfis.parquet", index=False)
    pd.read_parquet(DIR_DADOS / "jurisdicao.parquet").to_parquet(
        DIR_MODELOS / "jurisdicao.parquet", index=False)

    versao = f"{date.today():%Y.%m.%d}"
    metadados = {
        "model_version": versao,
        "treinado_em": date.today().isoformat(),
        "algoritmo": "HistGradientBoostingClassifier",
        "janela_historico": "2020-2024",
        "ano_alvo": 2025,
        "km_bin": 10,
        "limiar_abstencao": 10,
        "classes": CLASSES,
        "features": colunas,
        "categoricas": CATEGORICAS,
        "n_treino": int(len(treino)),
        "n_teste": int(len(teste)),
        "split": f"GroupShuffleSplit por trecho, test_size={args.test_size}, seed={args.seed}",
        "metricas": {"modelo": metricas_modelo, "baseline": metricas_base},
        "priorizacao_orcamento_fixo": prioridade["tabela"],
        "importancias": top[:25],
        "limitacoes": [
            "Sem exposicao (VMDA do DNIT): o modelo mede volume de acidentes, "
            "nao taxa por veiculo-km. Risco R1 do documento de negocio.",
            "Trechos com menos de 10 acidentes em 2020-2024 nao sao avaliados "
            "(RE-6): 33% da malha, concentrando 4,9% dos acidentes graves.",
        ],
        "versoes": {
            "python": sys.version.split()[0],
            "scikit-learn": sklearn.__version__,
            "pandas": pd.__version__,
        },
    }
    (DIR_MODELOS / "metadata.json").write_text(
        json.dumps(metadados, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nmodels/model.pkl        {(DIR_MODELOS / 'model.pkl').stat().st_size / 1e6:.1f} MB")
    print(f"models/perfis.parquet   {(DIR_MODELOS / 'perfis.parquet').stat().st_size / 1e6:.1f} MB")
    print(f"models/metadata.json    versao {versao}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
