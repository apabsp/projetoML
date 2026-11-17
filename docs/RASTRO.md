# Rastro de inferência: `logs/inferencia.jsonl`

Todo pedido a `POST /estimar_risco` (e a `POST /treinar`) deixa **uma linha** neste arquivo: o que chegou, o que o modelo efetivamente usou e o que foi devolvido. Isso vale também para os pedidos que falham. É a matéria-prima para histogramas, boxplots e comparação com a referência por KS e PSI.

Código: [`src/monitoramento.py`](../src/monitoramento.py) (logger, formato, rotação e middleware) e [`src/risco.py`](../src/risco.py) (`estimar_com_rastro`).

---

## 1. Decisões de formato

| Decisão | Escolha | Por quê |
|---|---|---|
| Biblioteca | `logging` da biblioteca padrão, logger próprio `detectarisco.rastro` | segue o [howto de logging do Python](https://docs.python.org/pt-br/3/howto/logging.html); `propagate=False` impede que o rastro se misture ao log de texto do BentoML no terminal |
| Formato da linha | **JSON Lines**: um objeto JSON por linha | cada linha é autocontida, o arquivo é texto puro e `pd.read_json(caminho, lines=True)` já devolve um DataFrame, sem regex |
| Data e hora | `ts` em **ISO 8601 com microssegundos e fuso**, por exemplo `2026-11-16T23:09:24.805619-03:00`, mais `ts_epoch` em float | o `asctime` padrão do `logging` vai só até milissegundos e não traz fuso. O `Formatter.formatTime` foi sobrescrito para usar o `record.created` com resolução total (ver [exibindo data/hora nas mensagens](https://docs.python.org/pt-br/3/howto/logging.html#displaying-the-date-time-in-messages)). ISO ordena como texto e não é ambíguo entre servidores em fusos diferentes; o `ts_epoch` facilita contas de intervalo |
| Escrita | `RotatingFileHandler(mode="a")` | **append-only**: cada evento só acrescenta uma linha no fim |
| Rotação | ao passar de **10 MB**, o arquivo é **renomeado** para `inferencia.jsonl.1` (o `.1` vira `.2`, e assim por diante, até `.5`) e um arquivo novo é aberto | nenhuma linha já escrita é reescrita. Capacidade: 6 arquivos × 10 MB, ~950 bytes por linha, **cerca de 60 mil pedidos**. Configurável por `DETECTARISCO_LOG_MAX_BYTES` e `DETECTARISCO_LOG_BACKUPS` |
| Versão do formato | campo `schema` (hoje `1`) | se um campo mudar no futuro, a análise sabe separar linhas de formatos diferentes |
| Onde é gravado | `middleware` ASGI, uma linha ao fim de cada pedido | erros de validação (HTTP 400) são devolvidos pelo BentoML **antes** do handler rodar. Sem o middleware, justamente os pedidos ruins não deixariam rastro |
| O que fica de fora | `/metrics`, `/livez`, `/readyz`, Swagger | não são uso do modelo e afogariam o arquivo |

Local: `logs/` na raiz do projeto (ou `DETECTARISCO_LOG_DIR`). No Docker, `./logs` é montado como volume, então o arquivo aparece no computador host e sobrevive ao container.

---

## 2. Três tipos de linha

**Pedido atendido** (`status: "ok"`):

```json
{"ts": "2026-11-16T23:09:24.805619-03:00", "ts_epoch": 1794881364.805619, "schema": 1,
 "request_id": "9e2cbc95fe19483e884d3b481ff24bc3", "endpoint": "/estimar_risco", "metodo": "POST",
 "http_status": 200, "status": "ok", "latencia_ms": 11.306, "inferencia_ms": 9.691,
 "model_version": "2026.09.22",
 "entrada": {"uf": "GO", "br": 60, "km": 136.2, "dia_semana": "Domingo", "fase_dia": "Plena noite"},
 "entrada_norm": {"uf": "GO", "br": 60, "km_bin": 130, "dia_semana": "domingo", "fase_dia": "plena noite"},
 "features": {"fase_dia": "plena noite", "tp_n_acidentes": 7.0, "tr_n_acidentes": 120,
              "tr_n_fer_graves": 23.0, "tr_pista_dupla": 0.758, "tr_n_fer_leves": 103.0,
              "tr_clima_sol": 0.067, "tr_urbano_nao": 0.542,
              "tr_causa_demais_falhas_mecanicas_ou_e": 0.1, "tp_taxa_grave": 0.0},
 "saida": {"nivel": "ALTO", "score": 0.5131, "prob_baixo": 0.07802, "prob_medio": 0.408894,
           "prob_alto": 0.513087, "jurisdicao_uop": "UOP01-DEL01-GO"}}
```

(Quebras de linha e arredondamento das features apenas para leitura. No arquivo, é uma linha só e os valores são completos.)

**Abstenção** (`status: "insuficiente"`): trecho sem histórico suficiente (RE-6). Tem `entrada`, mas `features: null` e probabilidades `null`, porque o modelo não foi consultado.

**Erro** (`status: "erro_validacao"` ou `"erro_interno"`): o handler não chegou a rodar, então a única evidência do que chegou é o `corpo_bruto`, truncado em 2.000 caracteres:

```json
{"ts": "2026-11-16T23:09:22.606649-03:00", ..., "http_status": 400, "status": "erro_validacao",
 "latencia_ms": 1.678, "corpo_bruto": "{\"uf\": \"SP\", \"br\": 116}"}
```

---

## 3. Quais variáveis, e por quê (ligação com o CRISP-DM)

O ponto de partida é como o modelo funciona (ver [ARQUITETURA.md](ARQUITETURA.md)). O payload tem 5 campos, mas o modelo **não recebe esses campos**: eles são a chave de busca do perfil histórico do trecho-período em `models/perfis.parquet`, e o `predict_proba` recebe as 68 features dessa linha. Por isso existem **duas camadas de entrada**, e as duas são registradas.

### 3.1 Entrada: o que o cliente pediu (`entrada`, `entrada_norm`)

| Variável | Tipo | Por que importa | Análise |
|---|---|---|---|
| `uf` | categórica | onde a PRF está consultando. Mudança no mix de UFs significa outro público usando a API | PSI por categoria, barras |
| `br`, `km` → `km_bin` | discreta / numérica | qual trecho foi consultado. `km_bin` é a mesma unidade do treino (10 km, PD-01) | KS e PSI, histograma |
| `dia_semana` | categórica | **quando**. A entrega 1 (§1.1) mostrou que a prática atual ignora o "quando" | PSI por categoria |
| `fase_dia` | categórica | é a **feature mais importante do modelo** (importância por permutação 0,062, o dobro da segunda) | PSI por categoria |

`entrada` guarda o texto **como o cliente mandou** ("Terça-feira", "Plena noite"), e `entrada_norm` guarda o valor **depois** da normalização usada para buscar no treino. Comparar as duas mostra problemas de integração (grafias que a normalização não cobre) sem precisar reproduzir o pedido.

### 3.2 Entrada: o que o modelo viu (`features`, top-10 por importância)

As 10 features de maior importância por permutação (`models/metadata.json → importancias`), com os valores que o modelo recebeu:

| # | Feature | Importância | Significado |
|---|---|---|---|
| 1 | `fase_dia` | 0,0617 | fase do dia (categórica, repetida aqui para o bloco ficar completo) |
| 2 | `tp_n_acidentes` | 0,0366 | acidentes 2020–24 **neste trecho-período** |
| 3 | `tr_n_acidentes` | 0,0233 | acidentes 2020–24 **no trecho** (todos os dias e fases) |
| 4 | `tr_n_fer_graves` | 0,0168 | feridos graves acumulados no trecho |
| 5 | `tr_pista_dupla` | 0,0051 | fração dos acidentes do trecho em pista dupla |
| 6 | `tr_n_fer_leves` | 0,0050 | feridos leves acumulados no trecho |
| 7 | `tr_clima_sol` | 0,0034 | fração dos acidentes com sol |
| 8 | `tr_urbano_nao` | 0,0032 | fração dos acidentes fora de área urbana |
| 9 | `tr_causa_demais_falhas_mecanicas_ou_e` | 0,0027 | fração com falha mecânica como causa |
| 10 | `tp_taxa_grave` | 0,0025 | taxa histórica de acidente grave no trecho-período |

**Por que só 10 das 68:** as 10 primeiras concentram **89 %** da importância positiva medida, e o restante tem importância próxima de zero (a 25ª vale 0,0004). Um drift numa feature que o modelo quase não usa não muda a resposta. Registrar as 68 deixaria cada linha com ~3 KB e esconderia o sinal no ruído. Com as 10, cada coluna do log é uma variável que **move a decisão**, e a análise cabe numa tela.

A lista vem do `metadata.json` do modelo carregado. Se um retreino mudar a ordem de importância, o rastro passa a registrar as novas top-10, e a `model_version` de cada linha diz qual lista vale para ela.

### 3.3 Saída (`saida`)

| Variável | Tipo | Por que importa | Análise |
|---|---|---|---|
| `nivel` | categórica | a resposta que o gestor vê (BAIXO / MEDIO / ALTO / INSUFICIENTE) | PSI por categoria, barras |
| **`prob_alto`** | contínua [0,1] | **a variável de saída mais importante.** A decisão D1 da entrega 1 é *priorização sob orçamento*: os trechos-período são ordenados por `prob_alto` (é a métrica de top-K da avaliação). Um deslocamento nela muda quem recebe viatura | KS e PSI, histograma, boxplot |
| `prob_baixo`, `prob_medio` | contínuas | completam a distribuição. Com `prob_alto`, mostram se o modelo ficou mais ou menos "indeciso" | histograma, boxplot |
| `score` | contínua | a probabilidade da classe prevista, que é o que a API devolve | só descritiva |
| `jurisdicao_uop` | categórica | RE-1: qual unidade da PRF recebe a recomendação | contagem por UOP |

**Por que guardar as três probabilidades, e não só o `score`:** o `score` mistura classes. 0,53 de ALTO e 0,53 de BAIXO viram o mesmo número. Um histograma de `score` não diz nada sobre risco; um de `prob_alto` diz.

### 3.4 Operação

| Variável | Para quê |
|---|---|
| `ts`, `ts_epoch` | janelas de tempo, ordenação, taxa de pedidos |
| `request_id` | identificar um pedido específico (auditoria) |
| `http_status`, `status` | taxa de erros e de abstenção. **A taxa de `insuficiente` é, por si só, um sinal de drift**: na referência ela é 0 % por construção, então se subir, a API está sendo consultada fora da malha que o modelo conhece |
| `latencia_ms` | ponta a ponta, medida no middleware: histograma e boxplot **exatos** de latência, complementando o `/metrics`, que só estima (ver [METRICAS.md §4.3](METRICAS.md)) |
| `inferencia_ms` | só busca + `predict_proba`. A diferença para `latencia_ms` é o custo de HTTP, validação e fila |
| `model_version` | separar as linhas por modelo (RE-8). Drift só se compara dentro da mesma versão |

### 3.5 Privacidade

O payload não contém dado pessoal (UF, BR, km, dia, fase) e não é registrado IP de cliente. O log mantém a garantia do RE-4 da entrega 1.

---

## 4. Conjunto de referência

O enunciado prevê um conjunto de referência manual quando não se tem o de treino. **Aqui temos o de treino:** `models/perfis.parquet` contém os 120.596 trechos-período (4.307 trechos × 7 dias × 4 fases) com as mesmas features que o modelo recebe.

| Variável do log | Referência |
|---|---|
| `uf`, `km_bin`, `dia_semana`, `fase_dia`, as 10 features | colunas de mesmo nome em `perfis.parquet` |
| `prob_alto`, `nivel` | `predict_proba` do modelo em produção sobre todo o `perfis.parquet` |

Cuidado de interpretação: a referência pesa **cada trecho-período igualmente**. É a distribuição "a malha inteira, sem preferência". Um uso real da API vai se afastar dela (gestores consultam mais os trechos que já preocupam), e isso é **drift de uso**, não drift do mundo. Drift do mundo (2026 diferente de 2020–24) só aparece com dados novos rotulados, ou seja, com retreino. O rastro distingue os dois: se as features de entrada mudaram mas a relação delas com `prob_alto` não, a mudança é de uso.

---

## 5. Verificação: os dados coletados bastam?

[`scripts/analisar_rastro.py`](../scripts/analisar_rastro.py) lê o log (e os arquivos rotacionados), monta todas as variáveis acima e compara cada uma com a referência por **KS** (numéricas) e **PSI** (todas). Para saber se o N já basta, ele também calcula o **ruído do PSI**: sorteia N linhas *da própria referência* 200 vezes e pega o p95 do PSI resultante. Esse é o PSI que aparece só por acaso, sem drift. Um PSI observado abaixo desse valor ainda não permite concluir nada.

```bash
uv run python scripts/analisar_rastro.py                 # o log inteiro
uv run python scripts/analisar_rastro.py --ultimos 300   # só os 300 pedidos mais recentes
```

Resultado real com ~300 pedidos de cada cenário do gerador de carga:

| Variável | Cenário referência: PSI (ruído p95) | Cenário drift: PSI (ruído p95) | KS p (drift) |
|---|---|---|---|
| `dia_semana` | 0,028 (0,033) | **4,635** (0,043) | n/a |
| `fase_dia` | 0,011 (0,021) | **4,287** (0,027) | n/a |
| `tr_n_acidentes` | 0,024 (0,045) | **1,269** (0,063) | 1e-59 |
| `tp_n_acidentes` | 0,005 (0,025) | **0,816** (0,037) | 3e-38 |
| `km_bin` | 0,027 (0,045) | 0,095 (0,065) | 3e-03 |
| `prob_alto` | 0,032 (0,046) | **0,772** (0,068) | 1e-30 |
| `nivel` | 0,019 (0,016) | **0,642** (0,022) | n/a |

Leitura:
- **No cenário referência**, nenhum PSI passa de 0,10 e quase todos ficam abaixo do próprio ruído. O formato e o volume são suficientes para dizer "sem drift".
- **No cenário drift** (fim de semana à noite, trechos com mais acidentes), a mudança aparece com folga nas variáveis certas: dia e fase, o histórico do trecho e, como consequência, `prob_alto`.
- `km_bin` mal se move, como esperado, porque o cenário de drift não escolhe a quilometragem. É bom sinal: a análise não acusa drift onde ele não existe.
- O ruído do PSI cai com N. Medido: com 300 pedidos, o p95 do ruído fica entre 0,02 e 0,06 nas variáveis numéricas e chega a 0,15 em `uf` (27 categorias, muitas raras). Com 1.000 pedidos, fica ≤ 0,02 nas numéricas e 0,04 em `uf`. Então **algumas centenas de pedidos** bastam para um drift forte, mas para detectar PSI na faixa moderada (0,10–0,25) com segurança, sobretudo em `uf`, é melhor janelas de **~1.000 pedidos**.

---

## 6. Como ler o arquivo para a análise

```python
import pandas as pd
log = pd.read_json("logs/inferencia.jsonl", lines=True)
ok = log[log.status == "ok"]
saida    = pd.json_normalize(ok.saida.tolist())      # nivel, prob_alto, ...
features = pd.json_normalize(ok.features.tolist())   # as 10 features
entrada  = pd.json_normalize(ok.entrada_norm.tolist())

saida.prob_alto.plot.hist(bins=10)                   # histograma
features[["tr_n_acidentes", "tp_n_acidentes"]].plot.box(logy=True)   # boxplot
```

`scripts/analisar_rastro.py` tem as funções `ler_rastro` (que junta os arquivos rotacionados na ordem certa) e `achatar` (que produz uma coluna por variável), prontas para a próxima entrega.
