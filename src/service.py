"""
DetectaRisco - servico BentoML.

So expoe rotas HTTP e delega para src/risco.py (inferencia) e
src/treinamento.py (pipeline de treino). Nenhuma regra de negocio mora aqui.

Uso:
    bentoml serve src.service:DetectaRiscoService
"""

from __future__ import annotations

import threading

import bentoml

from src.risco import RiscoEngine, Trecho
from src.treinamento import DIR_CSV, TreinoConfig, csvs_disponiveis, executar as executar_treino


@bentoml.service(name="detectarisco")
class DetectaRiscoService:
    def __init__(self) -> None:
        self._lock_treino = threading.Lock()
        self.engine = RiscoEngine()

    @bentoml.api(route="/estimar_risco")
    def estimar_risco(self, trecho: Trecho) -> dict:
        return self.engine.estimar(trecho)

    @bentoml.api(route="/treinar")
    def treinar(self, config: TreinoConfig = TreinoConfig()) -> dict:
        """Roda o pipeline completo (normalize -> build -> train) e recarrega o modelo em uso.

        Requer csvs/ montado no container (docker-compose.yml ja monta ./csvs
        como volume). Bloqueia a requisicao ate terminar - com os 7 anos da
        PRF, a normalizacao leva alguns minutos; retreinos sem
        forcar_normalizacao pulam essa etapa se data/ ja estiver populado.
        """
        if not csvs_disponiveis():
            return {
                "status": "erro",
                "mensagem": (
                    f"nenhum CSV encontrado em {DIR_CSV}. Baixe os dados da PRF "
                    "(variante todas_causas_tipos) e monte a pasta csvs/ no container."
                ),
            }

        if not self._lock_treino.acquire(blocking=False):
            return {"status": "erro", "mensagem": "ja existe um treinamento em andamento"}

        try:
            executar_treino(config)
            self.engine.carregar()
        finally:
            self._lock_treino.release()

        return {
            "status": "ok",
            "model_version": self.engine.metadata["model_version"],
            "n_treino": self.engine.metadata["n_treino"],
            "n_teste": self.engine.metadata["n_teste"],
            "metricas": self.engine.metadata["metricas"],
        }
