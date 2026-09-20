# Plano de Ação — Segunda Entrega (AV1, 40%)

**Projeto:** DetectaRisco — estimativa de risco por trecho-período de rodovia federal
**Entrega:** 24/09 às 10:30 · Apresentação 24/09 ou 29/09
**Base:** documento de Entendimento de Negócio (primeira entrega)

---

## 1. O que a segunda entrega pede

> "O que muda é que a inferência sai do notebook e passa a ser chamável por outra pessoa."

Três entregáveis, os três avaliados:

1. Repositório público no GitHub — código, instruções, evidência de execução
2. Serviço **rodando na máquina** no dia da apresentação, respondendo
3. Apresentação de 15 min + 5 min de perguntas, todos os integrantes falam

A rubrica segue quatro perguntas, **nesta ordem**:

| # | Pergunta | Onde já está respondido |
|---|---|---|
| 1 | Qual o objetivo da aplicação? Que decisão, e quem toma? | Entrega 1, §1.2 (D1) — só apresentar |
| 2 | Que processo de negócio isso afeta, e como? Antes × depois | Entrega 1, §1.1 (baseline) e §1.3 (ON-1, ON-2) |
| 3 | Como se põe a API no ar? Do `git clone` ao serviço respondendo | README, mostrado na tela |
| 4 | Demonstre o uso básico | curl / Swagger ao vivo |

### O que NÃO vale ponto

Front-end, dashboard, app · deploy em nuvem · log de experimentos · uso complexo.
Modelo próprio, pré-treinado ou de terceiro valem **igual** — avalia-se o serviço em volta.

**Regra do projeto:** nada fora da lista acima entra antes de o serviço estar de pé.

---

## 2. Decisões travadas

| Decisão | Escolha | Por quê |
|---|---|---|
| Modelo | Treinar próprio, scikit-learn | Não existe pré-treinado para "risco de trecho-período da PRF". Treinar é mais rápido que adaptar terceiro. Pesa 0 na nota. |
| Serving | **BentoML** | Pilha recomendada pela disciplina. Swagger de graça → atende a pergunta 4. |
| Unidade | `uf \| br \| km_bin \| dia_semana \| fase_dia` | É a unidade de análise da entrega 1 (trecho-período) |
| Tamanho do trecho | **10 km** (PD-01 resolvido) | Ver §2.1 |
| Nível da abstenção | **trecho**, não trecho-período | Ver §2.1 |
| Janela | features 2020–2024 → rótulo **2025** | Evita vazamento. 2025 é ano cheio e bate com o Anuário. |
| 2026 | fora do treino | Parcial (até 31/07/2026). Usar nos casos da demo — dado que o modelo nunca viu. |
| Exposição / VMDA | **fora do escopo desta entrega** | R1 do doc. Sem denominador, o modelo mede volume, não taxa. Limitação declarada no README e no slide. |

### 2.1 PD-01 resolvido — trecho de 10 km, abstenção no nível do trecho

Rodado com `build_dataset.py trecho-report` sobre 332.714 acidentes (2020–2024).

A primeira tentativa exigia histórico por **trecho-período**. Resultado ruim: a chave fragmenta o histórico em 7 dias × 4 fases, e a abstenção engolia a malha.

| Nível da abstenção | km | limiar | % avaliáveis | **% acidentes graves cobertos** |
|---|---|---|---|---|
| trecho-período | 10 | 5 | 22,9% | 59,3% |
| trecho-período | 20 | 3 | 49,5% | 85,1% |
| **trecho** | **10** | **10** | **66,7%** | **95,1%** |
| trecho | 10 | 20 | 48,2% | 87,3% |
| trecho | 20 | 10 | 76,8% | 98,1% |

`tipo_pista` e `tracado_via` não mudam com a hora do dia — não faz sentido exigir histórico por combinação de dia e fase. Então:

- **abstenção (RE-6)** olha o histórico do **trecho**
- **`dia_semana` e `fase_dia`** entram como *features* do modelo
- a saída continua sendo por **trecho-período**, como manda a entrega 1

Com 10 km e limiar 10: abstém em 33% da malha, e o que fica de fora concentra apenas **4,9% dos acidentes graves**. 20 km cobriria mais, mas uma recomendação de 20 km é vaga demais para posicionar viatura.

### Rótulo

Por trecho-período, no ano-alvo (2025):

- `ALTO` — houve ao menos um morto ou ferido grave
- `MEDIO` — houve apenas ferido leve
- `BAIXO` — acidente sem vítima, **ou nenhum acidente no ano**

Universo de rótulos = todas as chaves que aparecem em 2020–2024. Chave sem acidente em 2025 recebe `BAIXO`. Sem isso o modelo só veria lugares onde houve acidente.

### Métrica — corrigida no Dia 2

A primeira versão usava **recall da classe ALTO**. Está errado, e o treino mostrou por quê: o baseline "ganhava" com recall 0,68 contra 0,49 do modelo — mas só porque marcava **10.348** trechos-período como ALTO para acertar 2.409. Precisão 0,23. Nenhuma delegacia tem viatura para um terço da malha.

**D1 não é classificação, é priorização sob orçamento.** O gestor tem N viaturas e pergunta *quais* trechos recebem reforço neste ciclo. A régua certa: os dois métodos ordenam a malha inteira e disputam o mesmo K.

**Métrica principal: % dos acidentes graves de 2025 capturados no top-K.**
Macro-F1 como apoio. Recall isolado por classe **não** entra na apresentação — não é comparável entre métodos que marcam quantidades diferentes.

**Baseline obrigatório** (§1.1 e §3.7 do doc): ordenar por acidentes graves acumulados em 2020–2024. É a prática de hoje. O modelo tem que bater isso, senão o projeto não se justifica.

---

## 3. Os requisitos da entrega 1 viram a API

Esse é o diferencial da apresentação: o serviço não é genérico, ele implementa o documento.

| Requisito | Implementação no serviço |
|---|---|
| Unidade = trecho-período | Payload: `uf, br, km, dia_semana, fase_dia` |
| Estimativa de risco | Resposta: `nivel` + `score` |
| **RE-6** abstenção | `nivel: "INSUFICIENTE"` quando `n_registros < limiar` — **é o caso de erro da demo** |
| **RE-7** explicabilidade | `fatores[]` em português claro |
| **RE-1** jurisdição | Resposta traz `uop / delegacia / regional` |
| **RE-8** versionamento | `model_version` na resposta + log da requisição |
| **RE-4** LGPD | `pesid`, `idade`, `sexo`, `tipo_envolvido` descartados no pipeline |

---

## 4. O que os dados são

Sete CSVs em `csvs/`, variante `todas_causas_tipos` (1 linha = pessoa × causa × tipo).

| Ano | Acidentes | Mortos | Período |
|---|---|---|---|
| 2024 | 73.156 | 6.160 | ano cheio |
| 2025 | **72.529** | **6.043** | ano cheio |
| 2026 | 42.322 | 3.516 | até 31/07 |

2025 bate **exatamente** com o Anuário citado na entrega 1. Vira slide de validação.

### Armadilha: 8,25 linhas por acidente

603.215 linhas para 73.156 acidentes em 2024.

```
mortos.sum() direto            ->  29.810   ERRADO (4,8x)
drop_duplicates('pesid') antes ->   6.160   certo
```

Regra do pipeline — três granularidades:

- contar **acidentes** → `nunique('id')`
- somar **vítimas** → `drop_duplicates('pesid')` antes
- atributos do **trecho** → `drop_duplicates('id')`

### Outros achados do perfil

- `tracado_via` é **multivalorado**: `"Interseção de Vias;Reta;Aclive"` → 659 combinações. Split por `;` e multi-hot, não one-hot.
- Encoding **latin-1**, separador `;`, decimal vírgula.
- 1.348 linhas sem `br`/`km` (sem trecho) e 1.663 sem `uop` (viola RE-1) → descartar.
- `classificacao_acidente` tem 3 NaN em 2024. O PD-06 do doc praticamente não existe nesta janela — pendência resolvida de graça.
- Cardinalidade: 27 UF · 112 BR · 394 UOP · 4 fases · 7 dias.

### Colunas do `datatran` que faltam

O arquivo por ocorrência da PRF (`datatran<ano>.csv`) não foi baixado. As colunas `(O)` do Anexo A — `pessoas`, `veiculos`, `feridos`, `ignorados` — são derivadas por `groupby('id')`. **Não precisa baixar nada.**

---

## 5. Cronograma

### Dia 1 — 20/09 — dados
- [x] Download dos CSVs
- [x] `build_dataset.py normalize` — 7 anos → parquet por ocorrência + tabelas longas
- [x] `build_dataset.py trecho-report` — PD-01 resolvido (§2.1)
- [x] `build_dataset.py build` — `data/treino.parquet`, 120.596 × 72
- [x] Sanity check: 2025 dá 6.043 mortos, **idêntico ao Anuário**
- [ ] Repo GitHub público + esqueleto

**Saída do dia:** 120.596 trechos-período (4.307 trechos × 7 dias × 4 fases), 72 colunas.
Rótulo: BAIXO 76,8% · ALTO 11,8% · MEDIO 11,4% → desbalanceado, tratar com `class_weight` no Dia 2.

### Dia 2 — 21/09 — modelo ✅
- [x] `train.py` — `HistGradientBoostingClassifier` multiclasse, `class_weight` balanceado
- [x] Split **por trecho** (`GroupShuffleSplit`), não por linha — ver §5.1
- [x] Avaliar contra o baseline histórico sob orçamento fixo
- [x] `models/model.pkl` (1,2 MB) + `metadata.json` + importâncias por permutação
- [x] `models/perfis.parquet` (1,4 MB) + `models/jurisdicao.parquet` — as consultas do serviço

#### 5.1 Resultado

Teste: 30.156 trechos-período de **1.077 trechos que o modelo nunca viu** (split por trecho — cada trecho tem 28 linhas que compartilham as features `tr_*`; dividir por linha deixaria o mesmo trecho dos dois lados e inflaria o número).

| Orçamento | Método | Precisão | **% dos graves capturados** | Lift |
|---|---|---|---|---|
| 5% (1.507) | baseline | 0,517 | 22,0% | 4,4× |
| 5% | **modelo** | **0,581** | **24,7%** | **4,9×** |
| 10% (3.015) | baseline | 0,404 | 34,3% | 3,4× |
| 10% | **modelo** | **0,465** | **39,5%** | **4,0×** |
| 20% (6.031) | baseline | 0,301 | 51,2% | 2,6× |
| 20% | **modelo** | **0,348** | **59,2%** | **3,0×** |

Modelo vence em todos os orçamentos. Macro-F1: 0,398 → 0,520.

**Frase para a pergunta 2 da rubrica:** com o mesmo número de viaturas, o modelo alcança cerca de **1 em cada 6 acidentes graves a mais** que a prática atual (+5,2 pp em 10% de orçamento, +8,0 pp em 20%).

Ganho é moderado, não espetacular. Apresentar assim — número honesto defende-se em 5 minutos de perguntas; número inflado não.

#### 5.2 O que o modelo usa (importância por permutação, RE-7)

```
0.0617  fase_dia                  <- o mais forte de todos
0.0366  tp_n_acidentes            historico do trecho-periodo
0.0233  tr_n_acidentes            historico do trecho
0.0168  tr_n_fer_graves
0.0051  tr_pista_dupla
0.0050  tr_n_fer_leves
0.0034  tr_clima_sol
0.0032  tr_urbano_nao
```

`fase_dia` dominar é o achado que justifica o produto: **o risco depende mais de *quando* do que de *onde*.** A prática atual olha só o "onde". É exatamente a lacuna descrita em §1.1 da entrega 1.

### Dia 3 — 22/09 — serviço
- [ ] `service.py` BentoML, endpoint `POST /estimar_risco`
- [ ] Camada de abstenção **antes** do modelo (RE-6)
- [ ] README: `git clone` → `pip install` → `bentoml serve` → `curl`
- [ ] Testar o README em diretório limpo

### Dia 4 — 23/09 — evidência e ensaio
- [ ] 3 casos de sucesso (ALTO / MEDIO / BAIXO) + 1 de erro (INSUFICIENTE) em `evidencias/`
- [ ] Screenshots do Swagger
- [ ] GitHub Actions com smoke test
- [ ] Ensaio cronometrado de 15 min, todos falando, perguntas 1–4 na ordem

### 24/09 10:30 — entrega do link

---

## 6. Estrutura do repositório

```
trabalhoParaEntregar/
├─ README.md                  <- mostrado na tela na pergunta 3
├─ PLANO.md
├─ requirements.txt
├─ .gitignore                 <- csvs/ (1,2 GB) fica fora
├─ csvs/                      <- dados brutos, nao versionados
├─ src/
│  ├─ build_dataset.py
│  ├─ train.py
│  └─ service.py              <- BentoML
├─ data/                      <- parquets processados
├─ models/
│  ├─ model.pkl
│  └─ metadata.json
├─ evidencias/                <- curl_*.txt, swagger_*.png
├─ notebooks/
└─ .github/workflows/ci.yml
```

---

## 7. Riscos

| Risco | Mitigação |
|---|---|
| Somar colunas sem dedup | Regra das 3 granularidades, com teste de sanidade contra o Anuário |
| BentoML 1.2+ mudou a API (`@bentoml.service` de classe) | Fixar versão no `requirements.txt` |
| Abstenção engolir a malha inteira | Testar tamanhos de trecho antes de treinar |
| Tentar fazer tudo e chegar em 24/09 sem nada de pé | Lista do §1 ("o que NÃO vale ponto") é vinculante |
| Memória ao carregar 7 arquivos | `usecols` + processar ano a ano, nunca concatenar cru |
