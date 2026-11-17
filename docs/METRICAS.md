# Como ler o `GET /metrics` do DetectaRisco

Este guia explica, com números reais deste serviço:

- como montar o histograma e o boxplot da latência a partir do `/metrics`;
- como calcular a taxa de erros;
- por quanto tempo o servidor guarda essas informações.

A planilha [`metricas.xlsx`](metricas.xlsx) faz as mesmas contas com fórmulas de Excel. As células amarelas são valores copiados do `/metrics`, e todo o resto é calculado.

> **`/metrics` ou o log?** O `/metrics` é um **painel**: contadores somados em memória, baratos de consultar, bons para alarmes ("a latência subiu?", "os erros dispararam?"). O log [`logs/inferencia.jsonl`](RASTRO.md) é o **rastro**: guarda cada pedido em detalhe e permanece depois de um reinício. É com ele que se faz a análise de drift (KS e PSI). Os dois se complementam.

---

## 1. O que o `/metrics` expõe

```bash
curl -s http://127.0.0.1:3000/metrics
```

O texto segue o [formato de exposição do Prometheus](https://prometheus.io/docs/instrumenting/exposition_formats/): uma linha `# HELP` (descrição), uma linha `# TYPE` (tipo) e uma linha por série, no formato `nome{rótulos} valor`.

### Métricas do próprio BentoML

| Métrica | Tipo | Para que serve |
|---|---|---|
| `bentoml_service_request_duration_seconds` | histogram | **latência** ponta a ponta de cada rota, por `endpoint` e `http_response_code` |
| `bentoml_service_request_total` | counter | **número de pedidos** por `endpoint` e `http_response_code`, base da **taxa de erros** |
| `bentoml_service_request_in_progress` | gauge | pedidos sendo atendidos agora (indica fila) |
| `bentoml_service_last_request_timestamp_seconds` | gauge | instante (epoch) do último pedido |

> Obs.: o texto `# HELP` dessas métricas diz "runner RPC". É só o texto genérico do BentoML: os valores são os das rotas HTTP. O rótulo `service_version="not available"` aparece porque o serviço roda com `bentoml serve` direto, e não a partir de um *bento* empacotado.

### Métricas próprias do DetectaRisco (`src/monitoramento.py`)

| Métrica | Tipo | Para que serve |
|---|---|---|
| `detectarisco_predicoes_total{nivel, model_version}` | counter | quantas respostas BAIXO / MEDIO / ALTO / INSUFICIENTE: distribuição da **saída** |
| `detectarisco_prob_alto{model_version}` | histogram | distribuição da probabilidade de ALTO (saída contínua, faixas de 0,1) |
| `detectarisco_inferencia_segundos` | histogram | tempo **só do modelo** (busca do perfil + `predict_proba`), sem HTTP |
| `detectarisco_erros_total{endpoint, tipo}` | counter | erros por tipo (`erro_validacao`, `erro_interno`) |

A abstenção (`INSUFICIENTE`) responde **HTTP 200**: para o HTTP, não é erro. Por isso ela é contada em `detectarisco_predicoes_total{nivel="INSUFICIENTE"}`, e não na taxa de erros.

---

## 2. Ideia central: contadores acumulados e diferença entre dois scrapes

Todo `counter` e todo `histogram` do Prometheus é um **total acumulado desde que o processo subiu**. Só aumenta, nunca diminui. Um scrape isolado responde "quanto aconteceu desde o boot", o que raramente é a pergunta certa.

Para analisar um período, **faça dois scrapes (t0 e t1) e subtraia**:

```
valor na janela = valor(t1) − valor(t0)
```

Todas as contas abaixo usam essa diferença. Exemplo real usado neste guia (dois scrapes com 17 s de intervalo, 300 pedidos entre eles, gerados com `scripts/gerar_carga.py --modo drift`):

```bash
curl -s http://127.0.0.1:3000/metrics > scrape_t0.txt
uv run python scripts/gerar_carga.py --n 300 --modo drift
curl -s http://127.0.0.1:3000/metrics > scrape_t1.txt
```

---

## 3. Histograma da latência

### 3.1 Como o Prometheus guarda um histograma

Para cada endpoint e código HTTP, o histograma gera três tipos de linha:

```
bentoml_service_request_duration_seconds_bucket{endpoint="/estimar_risco",http_response_code="200",le="0.01"}  61.0
bentoml_service_request_duration_seconds_bucket{endpoint="/estimar_risco",http_response_code="200",le="0.015"} 547.0
...
bentoml_service_request_duration_seconds_bucket{...,le="+Inf"} 579.0
bentoml_service_request_duration_seconds_sum{...}   8.86281580001014
bentoml_service_request_duration_seconds_count{...} 579.0
```

- **`_bucket{le="X"}`**: quantos pedidos levaram **X segundos ou menos**. Os buckets são **cumulativos**: o `le="0.015"` já inclui os 61 pedidos do `le="0.01"`.
- **`_sum`**: soma de todas as latências, em segundos.
- **`_count`**: total de observações. É sempre igual ao bucket `+Inf`.

### 3.2 Do acumulado para a frequência de cada faixa

O que falta para o histograma é a contagem **dentro** de cada faixa, e não "até X". São dois passos:

1. **Janela:** `acum_janela(le) = bucket_t1(le) − bucket_t0(le)`
2. **Faixa:** `freq(faixa i) = acum_janela(le_i) − acum_janela(le_{i−1})`

Com os dados reais (`/estimar_risco`, HTTP 200):

| faixa (ms) | `le` (s) | acum. t0 | acum. t1 | acum. janela | **frequência na faixa** |
|---|---|---|---|---|---|
| 0 – 1 | 0.001 | 0 | 0 | 0 | 0 |
| 1 – 2 | 0.002 | 0 | 0 | 0 | 0 |
| 2 – 3 | 0.003 | 18 | 25 | 7 | **7** |
| 3 – 5 | 0.005 | 20 | 33 | 13 | **6** |
| 5 – 7,5 | 0.0075 | 20 | 33 | 13 | 0 |
| 7,5 – 10 | 0.01 | 43 | 61 | 18 | **5** |
| 10 – 15 | 0.015 | 274 | 547 | 273 | **255** |
| 15 – 20 | 0.02 | 280 | 553 | 273 | 0 |
| 20 – 30 | 0.03 | 282 | 558 | 276 | **3** |
| 30 – 50 | 0.05 | 285 | 564 | 279 | **3** |
| 50 – 75 | 0.075 | 289 | 571 | 282 | **3** |
| 75 – 100 | 0.1 | 289 | 571 | 282 | 0 |
| 100 – 250 | 0.25 | 289 | 575 | 286 | **4** |
| 250 – 500 | 0.5 | 289 | 579 | 290 | **4** |
| > 500 | +Inf | 289 | 579 | 290 | 0 |

A coluna "frequência na faixa" é o histograma: 255 dos 290 pedidos ficaram entre 10 e 15 ms, e há uma cauda de 8 pedidos acima de 100 ms. Na planilha, ele está desenhado na aba **Latencia**.

As faixas têm larguras diferentes (1 ms perto do zero, 250 ms na cauda). Por isso o histograma mostra **contagem por faixa**, e não densidade. Para comparar alturas como num histograma clássico, divida pela largura da faixa.

### 3.3 Média

```
média = Δ_sum / Δ_count = (8,8628 − 3,3722) s / (579 − 289) = 5,4906 / 290 = 18,93 ms
```

A média (18,9 ms) fica bem acima da mediana (12,5 ms, calculada abaixo). Os 8 pedidos lentos da cauda puxam a média para cima. É por isso que latência se descreve por **quantis**, e não pela média.

---

## 4. Boxplot da latência

O boxplot precisa de cinco números: mínimo, Q1, mediana, Q3 e máximo (mais os bigodes). O `/metrics` não guarda as latências individuais, só as contagens por faixa, então **os quantis são estimados** por interpolação linear dentro do bucket. É a mesma conta que o `histogram_quantile()` do Prometheus faz.

### 4.1 Quantil por interpolação

Para o quantil `q` (0,25, 0,5 ou 0,75):

1. **posto** `= q × N`
2. **bucket do quantil:** o primeiro cujo acumulado da janela é ≥ posto
3. **valor** `= lim_inf + (lim_sup − lim_inf) × (posto − acum_antes) / freq_bucket`

Isso supõe que os pedidos estão espalhados de forma uniforme dentro da faixa.

**Mediana, passo a passo** (N = 290):

- posto = 0,5 × 290 = **145**
- o acumulado passa de 18 (até 10 ms) para 273 (até 15 ms), então o bucket é **10 – 15 ms**, com 255 pedidos
- mediana = 10 + (15 − 10) × (145 − 18) / 255 = 10 + 5 × 0,498 = **12,49 ms**

Do mesmo jeito: **Q1 = 11,07 ms** (posto 72,5) e **Q3 = 13,91 ms** (posto 217,5).

### 4.2 Os cinco números

| Estatística | Valor (ms) | Como sai do `/metrics` |
|---|---|---|
| mínimo (aprox.) | 2 | limite **inferior** do primeiro bucket não vazio |
| Q1 | 11,07 | interpolação, q = 0,25 |
| mediana | 12,49 | interpolação, q = 0,50 |
| Q3 | 13,91 | interpolação, q = 0,75 |
| IQR | 2,84 | Q3 − Q1 |
| bigode inferior | 6,80 | max(mínimo, Q1 − 1,5 × IQR) |
| bigode superior | 18,18 | min(máximo, Q3 + 1,5 × IQR) |
| máximo (aprox.) | 500 | limite **superior** do bucket onde o acumulado chega a N |

Tudo acima do bigode superior (18,2 ms) é **outlier**: os 17 pedidos das faixas de 20 a 500 ms. A aba **Latencia** da planilha faz essas contas e desenha o boxplot como barra empilhada.

### 4.3 Limitação: o boxplot do `/metrics` é aproximado

| | Do `/metrics` (estimado) | Do log (exato, mesmos 290 pedidos) |
|---|---|---|
| Q1 | 11,07 ms | 10,67 ms |
| mediana | 12,49 ms | 11,14 ms |
| Q3 | 13,91 ms | 11,66 ms |
| mínimo / máximo | 2 / 500 ms (limites de faixa) | 2,34 / 422,96 ms |

O erro vem da largura do bucket: 255 pedidos caíram na faixa 10–15 ms, e a interpolação supõe que estão espalhados de 10 a 15, quando na verdade estão concentrados perto de 11. **Quanto mais estreitas as faixas onde os dados se concentram, menor o erro.** Por isso o serviço configura faixas próprias (`BUCKETS_LATENCIA` em `src/service.py`) em vez do padrão do Prometheus, que começa em 5 ms e pula direto para 10 e 25 ms (com o padrão, quase tudo caía em 2 faixas).

A métrica própria `detectarisco_inferencia_segundos` tem faixas de 1 ms entre 2 e 10 ms, e lá o erro quase some: mediana estimada de 9,41 ms contra 9,35 ms exata.

**Mínimo e máximo exatos não existem no `/metrics`.** Para o boxplot exato (e para histogramas de qualquer variável de entrada ou saída), use o log.

---

## 5. Taxa de erros

Use o contador `bentoml_service_request_total{endpoint, http_response_code}`, sempre pela diferença entre dois scrapes:

```
taxa de erros = Δ(pedidos com código 4xx ou 5xx) / Δ(todos os pedidos)     [mesmo endpoint]
```

Dados reais, `/estimar_risco`, janela t0 → t1 (17 s):

| `http_response_code` | t0 | t1 | Δ | classe |
|---|---|---|---|---|
| 200 | 289 | 579 | 290 | sucesso |
| 400 | 11 | 21 | 10 | erro do cliente |
| 500 | 0 | 0 | 0 | erro do servidor |

- **taxa de erros** = (10 + 0) / 300 = **3,33 %** (o gerador manda ~3 % de payloads inválidos de propósito)
- **taxa de erros do servidor** = 0 / 300 = **0 %**
- **vazão** = 300 / 17 s = **17,6 pedidos/s**

Separe as duas classes de erro:
- **4xx** indica problema do **cliente** (payload malformado). Uma taxa alta pede contato com quem integra a API.
- **5xx** indica problema do **serviço**. Deve ser zero, e é o candidato natural a alarme.

**Conferência:** `detectarisco_erros_total{tipo="erro_validacao"}` também variou 10 na janela (11 → 21), e o log tem 10 linhas com `"status": "erro_validacao"` no mesmo período. As três fontes concordam.

A taxa de erros também sai do **histograma**, sem precisar do counter: `_count{http_response_code="400"}` dividido pela soma dos `_count` de todos os códigos.

---

## 6. Saída do modelo (métricas próprias)

A aba **Saida do modelo** compara duas janelas: boot → t0 (300 pedidos no cenário *referência*) e t0 → t1 (300 no cenário *drift*):

| nível | janela 1 (referência) | janela 2 (drift) |
|---|---|---|
| BAIXO | 63,7 % | 25,2 % |
| MEDIO | 18,3 % | 26,2 % |
| ALTO | 11,1 % | 43,4 % |
| INSUFICIENTE | 6,9 % | 5,2 % |

O PSI do nível previsto entre as janelas é **0,83** (> 0,25 indica drift). Só pelo `/metrics` já dá para perceber que o perfil das respostas mudou. Saber **por quê** (quais entradas mudaram) exige o log: `scripts/analisar_rastro.py` mostra que a causa é a concentração em sexta, sábado e domingo à noite e em trechos com histórico pesado.

---

## 7. Por quanto tempo o servidor guarda essas informações

**Só enquanto o processo está vivo, e só como total acumulado.**

- **Onde ficam:** na memória dos processos do BentoML. Como ele roda em modo multiprocesso do `prometheus_client`, cada worker também espelha os valores em arquivos de `$BENTOML_HOME/prometheus_multiproc_dir` (por padrão `~/bentoml/prometheus_multiproc_dir`), que o `/metrics` soma na hora do scrape. **O BentoML apaga esse diretório toda vez que sobe** (`ensure_prometheus_dir` em `bentoml/serving.py`), então os arquivos não servem de histórico.
- **Desde quando:** desde o boot do processo. Cada scrape traz o total *até agora*.
- **Histórico:** **nenhum.** O servidor não guarda "quanto era às 14h". Só existe o valor atual de cada contador.
- **Reinício:** parar e subir o serviço (`Ctrl+C`, `docker compose restart`, um deploy, uma queda) **zera todas as métricas**. Verificado: depois de um restart, `bentoml_service_request_total{endpoint="/estimar_risco"}` voltou a 0, enquanto `logs/inferencia.jsonl` continuou com todas as linhas anteriores.
- **Consequência para análise:** um contador que *diminui* entre dois scrapes indica que o processo reiniciou no meio. A diferença t1 − t0 perde o sentido, e a janela deve ser descartada. (O `rate()` do Prometheus trata isso sozinho.)

Para ter **série temporal** (latência hora a hora, taxa de erros por dia), é preciso um **servidor Prometheus** coletando o `/metrics` periodicamente (por exemplo a cada 15 s) e guardando cada coleta. A retenção padrão do Prometheus é de **15 dias** (`--storage.tsdb.retention.time`). Esta entrega não sobe um Prometheus, porque não é exigido. O registro permanente é o **log em arquivo** ([RASTRO.md](RASTRO.md)), guardado em disco com rotação.

| | `/metrics` | `logs/inferencia.jsonl` |
|---|---|---|
| granularidade | agregados (contagens por faixa) | um registro por pedido |
| sobrevive a reinício | **não** | sim |
| histórico | não (só o total atual) | sim, até a rotação descartar o arquivo mais antigo (6 arquivos × 10 MB; ~950 bytes por linha, cerca de 60 mil pedidos) |
| quantis | estimados (limitados pelas faixas) | exatos |
| uso típico | alarme, painel em tempo real | análise de drift, auditoria, histogramas de entrada e saída |

---

## 8. Refazer a análise com dados novos

```bash
curl -s http://127.0.0.1:3000/metrics > scrape_t0.txt
uv run python scripts/gerar_carga.py --n 300            # ou espere tráfego real
curl -s http://127.0.0.1:3000/metrics > scrape_t1.txt
uv run --with openpyxl python scripts/gerar_planilha_metricas.py scrape_t0.txt scrape_t1.txt
```

O último comando reescreve `docs/metricas.xlsx` com os novos contadores. As fórmulas são as mesmas. Também dá para editar a planilha à mão: cole os valores dos dois scrapes nas células amarelas.
