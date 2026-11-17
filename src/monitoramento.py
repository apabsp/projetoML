"""
DetectaRisco - monitoramento do servico (rastro em arquivo + metricas proprias).

Duas saidas, com papeis diferentes:

1. Log em arquivo (logs/inferencia.jsonl) - o RASTRO. Uma linha JSON por
   requisicao, append-only, com o que entrou, o que o modelo viu e o que saiu.
   E a base para histogramas, boxplots e drift (KS / PSI) contra o conjunto
   de treino (models/perfis.parquet). Sobrevive a reinicios do servico.

2. Metricas Prometheus (GET /metrics) - o PAINEL. Contadores e histogramas
   acumulados em memoria desde o boot; somem quando o processo reinicia.

Por que JSON Lines: cada linha e um evento autocontido, o arquivo so cresce
no fim (append-only) e `pd.read_json(caminho, lines=True)` ja devolve um
DataFrame pronto para analise, sem regex.

Rotacao: RotatingFileHandler. Quando o arquivo passa de LOG_MAX_BYTES ele e
renomeado para inferencia.jsonl.1 (o .1 vira .2, e assim por diante) e um
arquivo novo e aberto. Nenhuma linha ja escrita e reescrita.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import time
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DIR_LOGS = Path(os.environ.get("DETECTARISCO_LOG_DIR", RAIZ / "logs"))
ARQUIVO_LOG = DIR_LOGS / "inferencia.jsonl"
LOG_MAX_BYTES = int(os.environ.get("DETECTARISCO_LOG_MAX_BYTES", 10 * 1024 * 1024))
LOG_BACKUPS = int(os.environ.get("DETECTARISCO_LOG_BACKUPS", 5))

# Versao do formato da linha. Mudou um campo? Incrementa - quem analisa o
# log sabe separar linhas de formatos diferentes.
SCHEMA_VERSION = 1

# Rotas cujo trafego vira rastro. Probes, /metrics e Swagger ficam de fora:
# nao sao uso do modelo e afogariam o arquivo.
ROTAS_RASTREADAS = ("/estimar_risco", "/treinar")

# Corpo de requisicao que falhou na validacao entra no log ate este tamanho.
MAX_CORPO_ERRO = 2000


# --------------------------------------------------------------------------
# log em arquivo
# --------------------------------------------------------------------------

class FormatadorJsonl(logging.Formatter):
    """Uma linha JSON por evento, com o carimbo de tempo mais detalhado possivel.

    O `asctime` padrao do logging so vai ate milissegundos e nao traz fuso.
    Aqui `formatTime` e sobrescrito para ISO 8601 com MICROssegundos e
    deslocamento de fuso (2026-11-16T14:03:27.481392-03:00), que ordena como
    texto, e lido direto por pandas e nao e ambiguo entre servidores em fusos
    diferentes. `ts_epoch` (float, segundos) vai junto para contas de intervalo.
    """

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="microseconds")

    def format(self, record: logging.LogRecord) -> str:
        linha = {
            "ts": self.formatTime(record),
            "ts_epoch": round(record.created, 6),
            "schema": SCHEMA_VERSION,
            **getattr(record, "evento", {"mensagem": record.getMessage()}),
        }
        return json.dumps(linha, ensure_ascii=False, default=str)


def _configurar_logger() -> logging.Logger:
    logger = logging.getLogger("detectarisco.rastro")
    if logger.handlers:  # modulo reimportado: nao duplica o handler
        return logger
    DIR_LOGS.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        ARQUIVO_LOG, mode="a", maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUPS, encoding="utf-8",
    )
    handler.setFormatter(FormatadorJsonl())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    # Nao repassa para o logger raiz: o rastro e um arquivo de dados, nao
    # deve aparecer misturado com o log de texto do BentoML no terminal.
    logger.propagate = False
    return logger


RASTRO = _configurar_logger()


def registrar(evento: dict) -> None:
    RASTRO.info("rastro", extra={"evento": evento})


# --------------------------------------------------------------------------
# metricas proprias (expostas no GET /metrics junto com as do BentoML)
# --------------------------------------------------------------------------

# Faixas de prob_alto: 0,1 em 0,1 da a mesma resolucao do PSI com 10 faixas.
BUCKETS_PROB = tuple(round(0.1 * i, 1) for i in range(1, 11))
# Tempo so do predict_proba + busca do perfil (sem rede/serializacao).
BUCKETS_INFERENCIA = (0.0005, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007, 0.008, 0.009,
                      0.01, 0.0125, 0.015, 0.02, 0.03, 0.05, 0.1, 0.25, 1.0)


class Metricas:
    """Contadores/histogramas proprios do DetectaRisco.

    Sao criados no __init__ do servico, nunca na importacao deste modulo: o
    BentoML define PROMETHEUS_MULTIPROC_DIR quando o worker sobe, e metrica
    criada antes disso fica invisivel no /metrics (ver bentoml/metrics.py).
    """

    def __init__(self) -> None:
        import prometheus_client as prom  # importado tarde de proposito

        self.predicoes = prom.Counter(
            "detectarisco_predicoes_total",
            "Respostas de /estimar_risco por nivel (inclui INSUFICIENTE)",
            ["nivel", "model_version"],
        )
        self.prob_alto = prom.Histogram(
            "detectarisco_prob_alto",
            "Probabilidade da classe ALTO devolvida pelo modelo (saida continua)",
            ["model_version"],
            buckets=BUCKETS_PROB,
        )
        self.inferencia = prom.Histogram(
            "detectarisco_inferencia_segundos",
            "Tempo da busca do perfil + predict_proba, sem rede",
            buckets=BUCKETS_INFERENCIA,
        )
        self.erros = prom.Counter(
            "detectarisco_erros_total",
            "Requisicoes rastreadas que terminaram em erro, por tipo",
            ["endpoint", "tipo"],
        )

    def observar_predicao(self, saida: dict, model_version: str, segundos: float) -> None:
        self.predicoes.labels(nivel=saida["nivel"], model_version=model_version).inc()
        self.inferencia.observe(segundos)
        if saida.get("prob_alto") is not None:
            self.prob_alto.labels(model_version=model_version).observe(saida["prob_alto"])


# Instancia unica por worker, criada pelo servico no boot (ver Metricas).
METRICAS: Metricas | None = None


def iniciar_metricas() -> Metricas:
    global METRICAS
    if METRICAS is None:
        METRICAS = Metricas()
    return METRICAS


# --------------------------------------------------------------------------
# middleware: uma linha de rastro por requisicao, inclusive as que falham
# --------------------------------------------------------------------------

# O middleware poe um dict vazio aqui; o handler da API preenche com os
# detalhes da inferencia. Como o dict e o mesmo objeto, o que o handler
# escreve aparece para o middleware mesmo quando a API roda em outra thread
# (a thread recebe uma copia do contexto, que aponta para o mesmo dict).
CONTEXTO_REQUISICAO: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "contexto_requisicao", default=None
)


def detalhar(**campos) -> None:
    """Chamado pelos handlers para anexar dados ao rastro da requisicao atual."""
    contexto = CONTEXTO_REQUISICAO.get()
    if contexto is not None:
        contexto.update(campos)


class MiddlewareRastro:
    """Middleware ASGI: mede latencia ponta a ponta, captura status HTTP e grava a linha.

    Fica no nivel ASGI (e nao dentro do handler) porque erros de validacao
    (HTTP 400, campo faltando, tipo errado) sao devolvidos pelo BentoML antes
    de o handler rodar. Sem o middleware, justamente os pedidos ruins - que
    sao sinal de problema no cliente - nao deixariam rastro.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"] not in ROTAS_RASTREADAS:
            await self.app(scope, receive, send)
            return

        contexto: dict = {}
        token = CONTEXTO_REQUISICAO.set(contexto)
        inicio = time.perf_counter()
        corpo = bytearray()
        status = {"codigo": 500}

        async def receive_capturando():
            mensagem = await receive()
            if mensagem["type"] == "http.request" and len(corpo) < MAX_CORPO_ERRO:
                corpo.extend(mensagem.get("body", b"")[: MAX_CORPO_ERRO - len(corpo)])
            return mensagem

        async def send_capturando(mensagem):
            if mensagem["type"] == "http.response.start":
                status["codigo"] = mensagem["status"]
            await send(mensagem)

        excecao = None
        try:
            await self.app(scope, receive_capturando, send_capturando)
        except Exception as erro:  # registra e repassa
            excecao = erro
            raise
        finally:
            CONTEXTO_REQUISICAO.reset(token)
            self._gravar(scope, contexto, status["codigo"], inicio, corpo, excecao)

    def _gravar(self, scope, contexto, codigo, inicio, corpo, excecao) -> None:
        latencia_ms = (time.perf_counter() - inicio) * 1000
        endpoint = scope["path"]

        if codigo >= 500 or excecao is not None:
            status = "erro_interno"
        elif codigo >= 400:
            status = "erro_validacao"
        else:
            status = contexto.pop("status", "ok")

        evento = {
            "request_id": contexto.pop("request_id", None) or uuid.uuid4().hex,
            "endpoint": endpoint,
            "metodo": scope.get("method"),
            "http_status": codigo,
            "status": status,
            "latencia_ms": round(latencia_ms, 3),
            **contexto,
        }
        if status.startswith("erro"):
            # Sem o handler ter rodado, o unico registro do que chegou e o corpo cru.
            evento["corpo_bruto"] = corpo.decode("utf-8", errors="replace")
            if excecao is not None:
                evento["excecao"] = f"{type(excecao).__name__}: {excecao}"
            if METRICAS is not None:
                METRICAS.erros.labels(endpoint=endpoint, tipo=status).inc()

        registrar(evento)
