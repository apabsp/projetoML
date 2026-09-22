# DetectaRisco

Estimativa de risco por trecho-período de rodovia federal, servida via BentoML.

## Rodar (único comando)

Requer apenas Docker instalado e em execução.

```bash
docker compose up --build
```

O serviço sobe em `http://localhost:3000`. Swagger em `http://localhost:3000/docs`.

## Testar

```bash
curl -s -X POST http://localhost:3000/estimar_risco \
  -H "Content-Type: application/json" \
  -d '{"trecho":{"uf":"PE","br":101,"km":42.5,"dia_semana":"sexta-feira","fase_dia":"plena noite"}}'
```

Trecho fora do universo com histórico suficiente (RE-6) responde `nivel: "INSUFICIENTE"`.

## Treinar via API

`POST /treinar` roda o pipeline completo (`build_dataset.py normalize` -> `build_dataset.py build` -> `train.py`) e recarrega o modelo em uso, sem reiniciar o serviço.

Requer os CSVs brutos da PRF (variante `todas_causas_tipos`) em `csvs/` no host — o `docker-compose.yml` monta `./csvs` (somente leitura), `./data` e `./models` como volumes, então os artefatos gerados aparecem direto no host.

```bash
curl -s -X POST http://localhost:3000/treinar -H "Content-Type: application/json" -d '{}'
```

Por padrão pula a normalização se `data/` já tiver os parquets do(s) ano(s) pedidos (retreino rápido). Para reprocessar os CSVs do zero, ou mudar hiperparâmetros do dataset:

```bash
curl -s -X POST http://localhost:3000/treinar -H "Content-Type: application/json" -d '{
  "forcar_normalizacao": true,
  "km_bin": 10,
  "limiar": 10,
  "anos_historico": [2020, 2021, 2022, 2023, 2024],
  "ano_alvo": 2025
}'
```

A chamada bloqueia até o treino terminar (alguns minutos se `forcar_normalizacao=true`, segundos se os parquets já existirem). Chamadas concorrentes retornam erro "já existe um treinamento em andamento".

## Desenvolvimento local (sem Docker)

Requer [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run bentoml serve src.service:DetectaRiscoService --reload
```

## Estrutura

```
src/
├─ build_dataset.py   # csvs/ -> data/treino.parquet
├─ train.py           # data/treino.parquet -> models/
└─ service.py         # BentoML: POST /estimar_risco
models/                # model.pkl, metadata.json, perfis.parquet, jurisdicao.parquet
```

`models/` já contém o modelo treinado e as tabelas de consulta — não é
necessário rodar `build_dataset.py` nem `train.py` para servir a API.
