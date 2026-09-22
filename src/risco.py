"""
DetectaRisco - motor de inferencia.

Carrega os artefatos de models/ e estima o risco de um trecho-periodo:
normaliza o payload, busca o perfil historico do trecho (RE-6), consulta
a jurisdicao (RE-1) e monta a resposta com os fatores mais relevantes
(RE-7) e a versao do modelo (RE-8).
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

RAIZ = Path(__file__).resolve().parents[1]
DIR_MODELOS = RAIZ / "models"

# Traducao das features mais importantes (RE-7), na ordem de
# models/metadata.json -> importancias. Nomes fora do mapa aparecem crus.
FATORES_PT = {
    "fase_dia": "horário do dia (fase_dia)",
    "tp_n_acidentes": "histórico de acidentes neste trecho-período",
    "tr_n_acidentes": "histórico de acidentes neste trecho",
    "tr_n_fer_graves": "feridos graves acumulados no trecho",
    "tr_pista_dupla": "pista dupla",
    "tr_n_fer_leves": "feridos leves acumulados no trecho",
    "tr_clima_sol": "clima ensolarado",
    "tr_urbano_nao": "trecho fora de área urbana",
    "tr_causa_demais_falhas_mecanicas_ou_e": "falhas mecânicas frequentes no trecho",
    "tp_taxa_grave": "taxa histórica de acidentes graves no trecho-período",
    "tr_trc_ponte": "presença de ponte no trecho",
    "tr_causa_falta_de_atencao_a_conducao": "falta de atenção na condução",
    "tr_veic_caminhao": "presença de caminhões",
    "tr_veic_camioneta": "presença de camionetas",
    "tr_media_pessoas": "média de pessoas envolvidas por acidente",
    "tr_tipo_colisao_com_objeto": "colisão com objeto frequente no trecho",
}


def normalizar_texto(valor: str) -> str:
    """Minusculas, sem acento, sem espaco nas pontas. Mesma regra usada no treino."""
    sem_acento = unicodedata.normalize("NFKD", valor.strip()).encode("ascii", "ignore").decode()
    return sem_acento.lower()


class Trecho(BaseModel):
    uf: str = Field(..., description="Sigla da UF", examples=["PE"])
    br: int = Field(..., description="Numero da rodovia federal", examples=[101])
    km: float = Field(..., description="Quilometro do trecho", examples=[42.5])
    dia_semana: str = Field(..., description="Dia da semana", examples=["sexta-feira"])
    fase_dia: str = Field(..., description="Fase do dia", examples=["plena noite"])


class RiscoEngine:
    """Mantem os artefatos do modelo em memoria e responde estimativas.

    `carregar()` e chamado no boot e de novo apos cada retreino
    (POST /treinar), para trocar o modelo em uso sem reiniciar o servico.
    """

    def __init__(self) -> None:
        self.carregar()

    def carregar(self) -> None:
        self.pipeline = joblib.load(DIR_MODELOS / "model.pkl")
        self.metadata = json.loads((DIR_MODELOS / "metadata.json").read_text(encoding="utf-8"))
        self.perfis = pd.read_parquet(DIR_MODELOS / "perfis.parquet")
        self.jurisdicao = pd.read_parquet(DIR_MODELOS / "jurisdicao.parquet")
        self.features = self.metadata["features"]
        self.tamanho_km_bin = self.metadata["km_bin"]
        self.limiar_abstencao = self.metadata["limiar_abstencao"]

    def estimar(self, trecho: Trecho) -> dict:
        uf = trecho.uf.strip().upper()
        br = int(trecho.br)
        km_bin = int(np.floor(trecho.km / self.tamanho_km_bin) * self.tamanho_km_bin)
        dia_semana = normalizar_texto(trecho.dia_semana)
        fase_dia = normalizar_texto(trecho.fase_dia)

        linha = self.perfis[
            (self.perfis["uf"] == uf)
            & (self.perfis["br"] == br)
            & (self.perfis["km_bin"] == km_bin)
            & (self.perfis["dia_semana"] == dia_semana)
            & (self.perfis["fase_dia"] == fase_dia)
        ]

        jurisdicao_info = self._jurisdicao_do_trecho(uf, br, km_bin)

        if linha.empty:
            return {
                "nivel": "INSUFICIENTE",
                "score": None,
                "motivo": (
                    f"menos de {self.limiar_abstencao} acidentes registrados neste "
                    "trecho no historico 2020-2024"
                ),
                "fatores": [],
                "jurisdicao": jurisdicao_info,
                "model_version": self.metadata["model_version"],
            }

        X = linha[self.features]
        probabilidades = self.pipeline.predict_proba(X)[0]
        classes = list(self.pipeline.classes_)
        indice = int(np.argmax(probabilidades))

        return {
            "nivel": classes[indice],
            "score": round(float(probabilidades[indice]), 4),
            "fatores": [
                FATORES_PT.get(item["feature"], item["feature"])
                for item in self.metadata["importancias"][:5]
            ],
            "jurisdicao": jurisdicao_info,
            "model_version": self.metadata["model_version"],
        }

    def _jurisdicao_do_trecho(self, uf: str, br: int, km_bin: int) -> dict | None:
        linha = self.jurisdicao[
            (self.jurisdicao["uf"] == uf)
            & (self.jurisdicao["br"] == br)
            & (self.jurisdicao["km_bin"] == km_bin)
        ]
        if linha.empty:
            return None
        registro = linha.iloc[0]
        return {
            "uop": registro["uop"],
            "delegacia": registro["delegacia"],
            "regional": registro["regional"],
            "municipio": registro["municipio"],
        }
