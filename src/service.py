"""
DetectaRisco - servico BentoML.

So expoe rotas HTTP e delega para src/risco.py (inferencia),
src/pipeline.py (orquestra o retreino) e src/monitoramento.py (rastro em
arquivo + metricas). Nenhuma regra de negocio mora aqui.

Rotas:
    POST /estimar_risco   inferencia
    POST /treinar         retreino
    GET  /metrics         Prometheus (do proprio BentoML + metricas detectarisco_*)

Uso:
    bentoml serve src.service:DetectaRiscoService
"""

from __future__ import annotations

import threading
import time
import uuid

import bentoml

from src import monitoramento
from src.monitoramento import MiddlewareRastro, detalhar
from src.pipeline import DIR_CSV, TreinoConfig, csvs_disponiveis, executar as executar_treino
from src.risco import RiscoEngine, Trecho


# Faixas do histograma de latencia do BentoML (bentoml_service_request_duration_seconds).
# O padrao do Prometheus comeca em 5 ms e pula para 10 e 25 ms, mas uma
# inferencia aqui leva ~1 ms e o pedido inteiro uns 2-20 ms: quase tudo caia em
# 2 faixas e o boxplot reconstruido do /metrics ficava sem resolucao. Estas
# faixas sao densas onde a latencia mora e ainda cobrem um retreino (minutos).
BUCKETS_LATENCIA = [0.001, 0.002, 0.003, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03,
                    0.05, 0.075, 0.1, 0.25, 0.5, 1.0, 2.5, 10.0, 60.0, 300.0]


@bentoml.service(name="detectarisco", metrics={"duration": {"buckets": BUCKETS_LATENCIA}})
class DetectaRiscoService:
    def __init__(self) -> None:
        self._lock_treino = threading.Lock()
        self.engine = RiscoEngine()
        self.metricas = monitoramento.iniciar_metricas()

    @bentoml.api(route="/estimar_risco")
    def estimar_risco(self, trecho: Trecho) -> dict:
        request_id = uuid.uuid4().hex
        inicio = time.perf_counter()
        resposta, rastro = self.engine.estimar_com_rastro(trecho)
        segundos = time.perf_counter() - inicio

        self.metricas.observar_predicao(rastro["saida"], rastro["model_version"], segundos)
        detalhar(request_id=request_id, inferencia_ms=round(segundos * 1000, 3), **rastro)
        return resposta

    @bentoml.api(route="/treinar")
    def treinar(self, config: TreinoConfig = TreinoConfig()) -> dict:
        """Roda o pipeline completo (normalize -> build -> train) e recarrega o modelo em uso.

        Requer csvs/ montado no container (docker-compose.yml ja monta ./csvs
        como volume). Bloqueia a requisicao ate terminar - com os 7 anos da
        PRF, a normalizacao leva alguns minutos; retreinos sem
        forcar_normalizacao pulam essa etapa se data/ ja estiver populado.
        """
        detalhar(entrada=config.model_dump())
        if not csvs_disponiveis():
            detalhar(status="sem_csvs")
            return {
                "status": "erro",
                "mensagem": (
                    f"nenhum CSV encontrado em {DIR_CSV}. Baixe os dados da PRF "
                    "(variante todas_causas_tipos) e monte a pasta csvs/ no container."
                ),
            }

        if not self._lock_treino.acquire(blocking=False):
            detalhar(status="treino_em_andamento")
            return {"status": "erro", "mensagem": "ja existe um treinamento em andamento"}

        try:
            executar_treino(config)
            self.engine.carregar()
        finally:
            self._lock_treino.release()

        detalhar(status="ok", model_version=self.engine.metadata["model_version"])
        return {
            "status": "ok",
            "model_version": self.engine.metadata["model_version"],
            "n_treino": self.engine.metadata["n_treino"],
            "n_teste": self.engine.metadata["n_teste"],
            "metricas": self.engine.metadata["metricas"],
        }


# Uma linha de rastro por requisicao em /estimar_risco e /treinar, inclusive
# as que o BentoML recusa na validacao (400) antes de chegar ao handler.
DetectaRiscoService.add_asgi_middleware(MiddlewareRastro)
