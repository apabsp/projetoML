"""
DetectaRisco - construcao do dataset a partir dos dados abertos da PRF.

Os CSVs da variante `todas_causas_tipos` trazem uma linha por
pessoa x causa x tipo (~8,25 linhas por acidente). Todo o modulo respeita
tres granularidades distintas:

    acidentes         -> nunique('id')
    vitimas           -> drop_duplicates('pesid') antes de somar
    atributos do via  -> drop_duplicates('id')

Estagios:
    normalize      csvs/*.csv            -> data/ocorrencias_<ano>.parquet (+ tabelas longas)
    trecho-report  compara tamanhos de trecho (decide PD-01)
    build          features 2020-2024 + rotulo 2025 -> data/treino.parquet
"""

from __future__ import annotations
import argparse
import sys
import unicodedata
from pathlib import Path
import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
DIR_CSV = RAIZ / "csvs"
DIR_DADOS = RAIZ / "data"

PADRAO_CSV = "acidentes{ano}_todas_causas_tipos.csv"

# Removemos idade, sexo, tipo_envolvido, marca, ano_fabricacao_veiculo.
# `pesid` entra apenas para deduplicar e nunca e persistido.
COLUNAS_CSV = [
    "id", "pesid", "data_inversa", "dia_semana", "uf", "br", "km",
    "municipio", "causa_acidente", "tipo_acidente", "classificacao_acidente",
    "fase_dia", "sentido_via", "condicao_metereologica", "tipo_pista",
    "tracado_via", "uso_solo", "id_veiculo", "tipo_veiculo", "estado_fisico",
    "ilesos", "feridos_leves", "feridos_graves", "mortos",
    "regional", "delegacia", "uop",
]

# Atributos que descrevem o local e ambiente
ATRIBUTOS_OCORRENCIA = [
    "data_inversa", "dia_semana", "uf", "br", "km", "municipio",
    "classificacao_acidente", "fase_dia", "sentido_via",
    "condicao_metereologica", "tipo_pista", "tracado_via", "uso_solo",
    "regional", "delegacia", "uop",
]

CHAVE_TRECHO = ["uf", "br", "km_bin", "dia_semana", "fase_dia"]


def normalizar_texto(serie: pd.Series) -> pd.Series:
    """Minusculas, sem acento, sem espaco sobrando.

    A key do trecho-periodo é montada com texto normalizado para que o
    servico aceite "Plena Noite", "plena noite" ou "PLENA NOITE" sem
    """
    texto = serie.astype("string").str.strip()
    sem_acento = texto.map(
        lambda v: unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode()
        if pd.notna(v) else v
    )
    return sem_acento.str.lower()


def caminho_csv(ano: int) -> Path:
    return DIR_CSV / PADRAO_CSV.format(ano=ano)


def anos_disponiveis() -> list[int]:
    anos = []
    for caminho in sorted(DIR_CSV.glob("acidentes*_todas_causas_tipos.csv")):
        digitos = "".join(c for c in caminho.stem if c.isdigit())
        if digitos:
            anos.append(int(digitos[:4]))
    return sorted(anos)


def ler_ano(ano: int) -> pd.DataFrame:
    """Le um CSV bruto da PRF, ja com os descartes estruturais aplicados."""
    caminho = caminho_csv(ano)
    if not caminho.exists():
        raise FileNotFoundError(f"CSV nao encontrado: {caminho}")

    bruto = pd.read_csv(
        caminho,
        sep=";",
        encoding="latin-1",
        decimal=",",
        usecols=COLUNAS_CSV,
        low_memory=False,
    )
    antes = bruto["id"].nunique()

    # Sem br/km nao existe trecho; sem uop nao existe jurisdicao (RE-1).
    bruto = bruto.dropna(subset=["br", "km", "uop"])
    bruto["br"] = bruto["br"].astype(int)
    bruto["uf"] = bruto["uf"].astype("string").str.strip().str.upper()
    for coluna in ("dia_semana", "fase_dia"):
        bruto[coluna] = normalizar_texto(bruto[coluna])

    depois = bruto["id"].nunique()
    print(f"  {ano}: {len(bruto):>7} linhas | {depois:>6} acidentes "
          f"(descartados {antes - depois} sem br/km/uop)")
    return bruto


def normalizar_ano(ano: int) -> None:
    """Converte um CSV bruto nas tabelas por ocorrencia e nas tabelas longas."""
    bruto = ler_ano(ano)

    # --- nivel ocorrencia: atributos do trecho, um registro por acidente ---
    ocorrencias = (
        bruto.drop_duplicates("id")
        .set_index("id")[ATRIBUTOS_OCORRENCIA]
        .copy()
    )

    # --- nivel pessoa: vitimas contadas uma unica vez ---
    pessoas = bruto.drop_duplicates("pesid")
    estado = normalizar_texto(pessoas["estado_fisico"])
    pessoas = pessoas.assign(
        _ignorado=(estado.isna() | estado.str.startswith(("ignorado", "nao informado"))).astype(int)
    )
    vitimas = pessoas.groupby("id").agg(
        pessoas=("pesid", "nunique"),
        mortos=("mortos", "sum"),
        feridos_graves=("feridos_graves", "sum"),
        feridos_leves=("feridos_leves", "sum"),
        ilesos=("ilesos", "sum"),
        ignorados=("_ignorado", "sum"),
    )
    vitimas["feridos"] = vitimas["feridos_graves"] + vitimas["feridos_leves"]

    # --- nivel veiculo ---
    veiculos = bruto.groupby("id")["id_veiculo"].nunique().rename("veiculos")

    ocorrencias = ocorrencias.join(vitimas).join(veiculos).reset_index()
    ocorrencias["ano"] = ano

    DIR_DADOS.mkdir(exist_ok=True)
    ocorrencias.to_parquet(DIR_DADOS / f"ocorrencias_{ano}.parquet", index=False)

    # --- tabelas longas: um acidente tem varias causas, tipos e veiculos ---
    tabelas = {
        "causas": bruto[["id", "causa_acidente"]],
        "tipos": bruto[["id", "tipo_acidente"]],
        "veiculos": bruto[["id", "tipo_veiculo"]],
    }
    for nome, tabela in tabelas.items():
        (tabela.dropna().drop_duplicates()
         .to_parquet(DIR_DADOS / f"{nome}_{ano}.parquet", index=False))

    mortos = int(ocorrencias["mortos"].sum())
    print(f"       -> {len(ocorrencias)} ocorrencias | {mortos} mortos | "
          f"{int(ocorrencias['feridos_graves'].sum())} feridos graves")


def carregar_ocorrencias(anos: list[int]) -> pd.DataFrame:
    partes = []
    for ano in anos:
        caminho = DIR_DADOS / f"ocorrencias_{ano}.parquet"
        if not caminho.exists():
            raise FileNotFoundError(
                f"{caminho} nao existe. Rode: python src/build_dataset.py normalize"
            )
        partes.append(pd.read_parquet(caminho))
    return pd.concat(partes, ignore_index=True)


def aplicar_km_bin(df: pd.DataFrame, tamanho: int) -> pd.DataFrame:
    df = df.copy()
    df["km_bin"] = (np.floor(df["km"] / tamanho) * tamanho).astype(int)
    return df


# --------------------------------------------------------------------------
# estagios de linha de comando
# --------------------------------------------------------------------------

def estagio_normalize(args) -> None:
    anos = args.anos or anos_disponiveis()
    print(f"Normalizando {len(anos)} ano(s): {anos}")
    for ano in anos:
        normalizar_ano(ano)
    print(f"\nParquets em {DIR_DADOS}")


def estagio_trecho_report(args) -> None:
    """Compara tamanhos de trecho para decidir o PD-01.

    Trecho pequeno descreve melhor o local, mas espalha o historico e faz a
    regra de abstencao (RE-6) marcar quase tudo como INSUFICIENTE. Trecho
    grande tem historico denso e recomendacao vaga. O relatorio mostra o
    tradeoff em numeros.
    """
    anos = args.anos or [2020, 2021, 2022, 2023, 2024]
    print(f"Carregando ocorrencias {anos[0]}-{anos[-1]}...")
    ocorrencias = carregar_ocorrencias(anos)
    total_acidentes = len(ocorrencias)
    print(f"{total_acidentes} acidentes na janela\n")

    ocorrencias["graves"] = ocorrencias["feridos_graves"] + ocorrencias["mortos"]
    total_graves = ocorrencias["graves"].sum()

    niveis = {
        "trecho-periodo": CHAVE_TRECHO,
        "trecho": ["uf", "br", "km_bin"],
    }

    linhas = []
    for tamanho in args.tamanhos:
        df = aplicar_km_bin(ocorrencias, tamanho)
        for nome_nivel, chave in niveis.items():
            por_chave = df.groupby(chave, observed=True).agg(
                n_acidentes=("id", "size"),
                graves=("graves", "sum"),
            )
            for limiar in args.limiares:
                suficientes = por_chave["n_acidentes"] >= limiar
                linhas.append({
                    "nivel": nome_nivel,
                    "km": tamanho,
                    "limiar": limiar,
                    "unidades": len(por_chave),
                    "%_avaliaveis": 100 * suficientes.mean(),
                    "%_acid_cobertos": 100 * por_chave.loc[suficientes, "n_acidentes"].sum() / total_acidentes,
                    "%_graves_cobertos": 100 * por_chave.loc[suficientes, "graves"].sum() / total_graves,
                    "mediana_acid": por_chave["n_acidentes"].median(),
                })

    relatorio = pd.DataFrame(linhas).sort_values(["nivel", "km", "limiar"])
    pd.set_option("display.width", 160)
    print(relatorio.to_string(index=False, float_format=lambda v: f"{v:6.1f}"))

    print("\nLeitura:")
    print("  %_avaliaveis        fracao das unidades que o modelo consegue avaliar")
    print("  %_graves_cobertos   fracao dos acidentes com morto/ferido grave que caem numa")
    print("                      unidade avaliavel. E o numero que importa: o que ficar de")
    print("                      fora vira INSUFICIENTE (RE-6) e nao recebe viatura.")
    print("\n  O nivel 'trecho' cobre muito mais com o mesmo limiar porque nao fragmenta o")
    print("  historico em 7 dias x 4 fases. tipo_pista e tracado nao mudam por hora do dia,")
    print("  entao a abstencao olha o trecho e o periodo entra como feature do modelo.")


def carregar_longa(nome: str, anos: list[int]) -> pd.DataFrame:
    partes = [pd.read_parquet(DIR_DADOS / f"{nome}_{ano}.parquet") for ano in anos]
    return pd.concat(partes, ignore_index=True).drop_duplicates()


def _sanear(valor: str) -> str:
    texto = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode()
    return "".join(c if c.isalnum() else "_" for c in texto.lower()).strip("_")[:28]


def shares_por_trecho(longa: pd.DataFrame, coluna: str, mapa_trecho: pd.DataFrame,
                      n_acidentes: pd.Series, prefixo: str, top_n: int) -> pd.DataFrame:
    """Fracao dos acidentes do trecho que envolvem cada valor da coluna.

    A tabela longa ja vem deduplicada em (id, valor), entao contar linhas da
    o numero de acidentes distintos com aquele valor. O denominador e o total
    de acidentes do trecho - as fracoes somam mais de 1 porque um acidente
    tem varias causas e varios tipos.
    """
    juncao = longa.merge(mapa_trecho, on="id", how="inner")
    mais_comuns = juncao[coluna].value_counts().head(top_n).index
    juncao = juncao[juncao[coluna].isin(mais_comuns)]

    chave = list(mapa_trecho.columns.drop("id"))
    contagem = (juncao.groupby(chave + [coluna], observed=True).size()
                .unstack(fill_value=0))
    contagem.columns = [f"{prefixo}{_sanear(c)}" for c in contagem.columns]
    return contagem.div(n_acidentes.reindex(contagem.index), axis=0).fillna(0.0)


def perfil_do_trecho(hist: pd.DataFrame, anos: list[int], chave: list[str]) -> pd.DataFrame:
    """Tudo que se sabe sobre um trecho a partir da janela historica."""
    grupo = hist.groupby(chave, observed=True)
    perfil = grupo.agg(
        tr_n_acidentes=("id", "size"),
        tr_n_mortos=("mortos", "sum"),
        tr_n_fer_graves=("feridos_graves", "sum"),
        tr_n_fer_leves=("feridos_leves", "sum"),
        tr_media_pessoas=("pessoas", "mean"),
        tr_media_veiculos=("veiculos", "mean"),
        tr_n_anos=("ano", "nunique"),
    )
    perfil["tr_taxa_grave"] = (
        (perfil.tr_n_mortos + perfil.tr_n_fer_graves) / perfil.tr_n_acidentes
    )
    n_acidentes = perfil["tr_n_acidentes"]

    # tipo_pista e uso_solo: proporcao dos acidentes do trecho em cada condicao
    for coluna, prefixo in (("tipo_pista", "tr_pista_"), ("uso_solo", "tr_urbano_")):
        proporcao = (hist.groupby(chave + [coluna], observed=True).size()
                     .unstack(fill_value=0))
        proporcao.columns = [f"{prefixo}{_sanear(c)}" for c in proporcao.columns]
        perfil = perfil.join(proporcao.div(n_acidentes, axis=0).fillna(0.0))

    # tracado_via e multivalorado: "Intersecao de Vias;Reta;Aclive"
    tracado = (hist[["id", "tracado_via"]].dropna()
               .assign(tracado=lambda d: d.tracado_via.str.split(";"))
               .explode("tracado"))
    tracado["tracado"] = tracado["tracado"].str.strip()
    mapa = hist[["id"] + chave]
    perfil = perfil.join(shares_por_trecho(
        tracado[["id", "tracado"]].drop_duplicates(), "tracado",
        mapa, n_acidentes, "tr_trc_", top_n=12))

    clima = hist[["id", "condicao_metereologica"]].dropna().drop_duplicates()
    perfil = perfil.join(shares_por_trecho(
        clima, "condicao_metereologica", mapa, n_acidentes, "tr_clima_", top_n=6))

    for nome, prefixo, top_n in (("causas", "tr_causa_", 12),
                                 ("tipos", "tr_tipo_", 10),
                                 ("veiculos", "tr_veic_", 8)):
        longa = carregar_longa(nome, anos)
        coluna = longa.columns[1]
        perfil = perfil.join(shares_por_trecho(
            longa, coluna, mapa, n_acidentes, prefixo, top_n))

    return perfil.fillna(0.0)


def rotular(alvo: pd.DataFrame, chave: list[str]) -> pd.Series:
    """ALTO se houve morto ou ferido grave, MEDIO se so ferido leve, senao BAIXO."""
    resumo = alvo.groupby(chave, observed=True).agg(
        mortos=("mortos", "sum"),
        feridos_graves=("feridos_graves", "sum"),
        feridos_leves=("feridos_leves", "sum"),
    )
    nivel = np.where(
        (resumo.mortos + resumo.feridos_graves) > 0, "ALTO",
        np.where(resumo.feridos_leves > 0, "MEDIO", "BAIXO"),
    )
    return pd.Series(nivel, index=resumo.index, name="nivel_risco")


def estagio_build(args) -> None:
    chave_trecho = ["uf", "br", "km_bin"]
    anos_hist = args.anos_historico
    print(f"Historico {anos_hist[0]}-{anos_hist[-1]} | alvo {args.ano_alvo} | "
          f"trecho {args.km_bin} km | limiar {args.limiar}\n")

    hist = aplicar_km_bin(carregar_ocorrencias(anos_hist), args.km_bin)
    alvo = aplicar_km_bin(carregar_ocorrencias([args.ano_alvo]), args.km_bin)

    # RE-6: so entram trechos com historico suficiente. Os demais nunca chegam
    # ao modelo - o servico responde INSUFICIENTE antes de inferir.
    contagem = hist.groupby(chave_trecho, observed=True).size()
    qualificados = contagem[contagem >= args.limiar].index
    print(f"{len(contagem)} trechos | {len(qualificados)} com >= {args.limiar} acidentes "
          f"({100 * len(qualificados) / len(contagem):.1f}%)")

    hist = hist.set_index(chave_trecho).loc[qualificados].reset_index()
    print(f"{len(hist)} acidentes nos trechos qualificados\n")

    print("Montando perfil do trecho...")
    perfil = perfil_do_trecho(hist, anos_hist, chave_trecho)

    # RE-1: jurisdicao e o valor mais frequente no trecho. Recomendar fora da
    # jurisdicao nao serve para nada, entao a tabela vai junto com o modelo.
    jurisdicao = (hist.groupby(chave_trecho, observed=True)
                  [["uop", "delegacia", "regional", "municipio"]]
                  .agg(lambda s: s.mode().iat[0] if not s.mode().empty else None))

    # Universo: todo trecho qualificado x 7 dias x 4 fases. Um trecho-periodo
    # sem nenhum acidente no historico continua sendo avaliavel, porque o que
    # sustenta a estimativa e o perfil do trecho.
    dias = sorted(hist["dia_semana"].dropna().unique())
    fases = sorted(hist["fase_dia"].dropna().unique())
    universo = (perfil.reset_index()[chave_trecho]
                .merge(pd.Series(dias, name="dia_semana"), how="cross")
                .merge(pd.Series(fases, name="fase_dia"), how="cross"))
    print(f"Universo: {len(universo)} trechos-periodo "
          f"({len(perfil)} trechos x {len(dias)} dias x {len(fases)} fases)")

    # Historico do proprio trecho-periodo: mais fraco, mas e o que separa
    # "sexta a noite" de "terca de manha" no mesmo pedaco de rodovia.
    por_periodo = hist.groupby(CHAVE_TRECHO, observed=True).agg(
        tp_n_acidentes=("id", "size"),
        tp_n_mortos=("mortos", "sum"),
        tp_n_fer_graves=("feridos_graves", "sum"),
    )
    por_periodo["tp_taxa_grave"] = (
        (por_periodo.tp_n_mortos + por_periodo.tp_n_fer_graves)
        / por_periodo.tp_n_acidentes
    )

    dados = (universo
             .merge(perfil.reset_index(), on=chave_trecho, how="left")
             .merge(por_periodo.reset_index(), on=CHAVE_TRECHO, how="left")
             .merge(rotular(alvo, CHAVE_TRECHO).reset_index(), on=CHAVE_TRECHO, how="left"))

    colunas_tp = ["tp_n_acidentes", "tp_n_mortos", "tp_n_fer_graves", "tp_taxa_grave"]
    dados[colunas_tp] = dados[colunas_tp].fillna(0.0)
    dados["tp_share_do_trecho"] = dados.tp_n_acidentes / dados.tr_n_acidentes

    # Trecho-periodo sem acidente nenhum no ano-alvo e BAIXO, nao e ausente.
    # Sem isso o modelo so veria lugares onde houve acidente.
    dados["nivel_risco"] = dados["nivel_risco"].fillna("BAIXO")

    DIR_DADOS.mkdir(exist_ok=True)
    dados.to_parquet(DIR_DADOS / "treino.parquet", index=False)
    (jurisdicao.join(contagem.rename("tr_n_acidentes_total"))
     .reset_index().to_parquet(DIR_DADOS / "jurisdicao.parquet", index=False))

    print(f"\ndata/treino.parquet: {dados.shape[0]} linhas x {dados.shape[1]} colunas")
    distribuicao = dados["nivel_risco"].value_counts(normalize=True).mul(100).round(1)
    print("\nDistribuicao do rotulo (%):")
    print(distribuicao.to_string())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="estagio", required=True)

    p_norm = sub.add_parser("normalize", help="CSV bruto -> parquet por ocorrencia")
    p_norm.add_argument("--anos", type=int, nargs="*", default=None)
    p_norm.set_defaults(func=estagio_normalize)

    p_rep = sub.add_parser("trecho-report", help="compara tamanhos de trecho (PD-01)")
    p_rep.add_argument("--anos", type=int, nargs="*", default=None)
    p_rep.add_argument("--tamanhos", type=int, nargs="*", default=[1, 5, 10, 20, 50])
    p_rep.add_argument("--limiares", type=int, nargs="*", default=[3, 5, 10])
    p_rep.set_defaults(func=estagio_trecho_report)

    p_build = sub.add_parser("build", help="features + rotulo -> data/treino.parquet")
    p_build.add_argument("--km-bin", type=int, default=10, dest="km_bin")
    p_build.add_argument("--limiar", type=int, default=10)
    p_build.add_argument("--anos-historico", type=int, nargs="*",
                         default=[2020, 2021, 2022, 2023, 2024], dest="anos_historico")
    p_build.add_argument("--ano-alvo", type=int, default=2025, dest="ano_alvo")
    p_build.set_defaults(func=estagio_build)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
