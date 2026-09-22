"""
DetectaRisco - orquestracao do (re)treino.

Encadeia os estagios de src/build_dataset.py e src/train.py. Nao duplica
regra de negocio nenhuma - features, rotulo e avaliacao do modelo continuam
definidos nos dois scripts originais; este modulo so decide *quando* rodar
cada estagio e valida os pre-requisitos (CSVs presentes).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from src import build_dataset
from src import train as treino_modelo

RAIZ = Path(__file__).resolve().parents[1]
DIR_DADOS = RAIZ / "data"
DIR_CSV = RAIZ / "csvs"


class TreinoConfig(BaseModel):
    forcar_normalizacao: bool = Field(
        default=False,
        description=(
            "Reprocessa os CSVs brutos (csvs/) mesmo se os parquets de data/ ja "
            "existirem. Reprocessar e a etapa mais lenta do pipeline."
        ),
    )
    km_bin: int = Field(default=10, description="Tamanho do trecho, em km")
    limiar: int = Field(
        default=10, description="Minimo de acidentes 2020-2024 para o trecho ser avaliavel (RE-6)"
    )
    anos_historico: list[int] = Field(default=[2020, 2021, 2022, 2023, 2024])
    ano_alvo: int = Field(default=2025, description="Ano usado para rotular o risco (treino supervisionado)")
    test_size: float = Field(default=0.25)
    seed: int = Field(default=42)


def csvs_disponiveis() -> bool:
    return DIR_CSV.exists() and any(DIR_CSV.glob("acidentes*_todas_causas_tipos.csv"))


def executar(config: TreinoConfig) -> None:
    """Roda normalize (se preciso) -> build -> train. Escreve em models/."""
    anos_necessarios = set(config.anos_historico) | {config.ano_alvo}
    ja_normalizado = all(
        (DIR_DADOS / f"ocorrencias_{ano}.parquet").exists() for ano in anos_necessarios
    )
    if config.forcar_normalizacao or not ja_normalizado:
        build_dataset.main(["normalize"])

    build_dataset.main([
        "build",
        "--km-bin", str(config.km_bin),
        "--limiar", str(config.limiar),
        "--anos-historico", *[str(a) for a in config.anos_historico],
        "--ano-alvo", str(config.ano_alvo),
    ])

    treino_modelo.main(["--test-size", str(config.test_size), "--seed", str(config.seed)])
