# DetectaRisco

API que estima o **nível de risco (BAIXO / MEDIO / ALTO)** de um trecho de rodovia federal (10 km) num dado dia da semana e fase do dia, a partir do histórico de acidentes da PRF (2020–2024). Servida com [BentoML](https://www.bentoml.com/).

- **Decisão apoiada:** onde e quando priorizar o policiamento/fiscalização (quem decide: gestão da PRF por regional/delegacia).
- **Abstenção:** trechos com menos de 10 acidentes no histórico respondem `INSUFICIENTE` em vez de chutar.

## Requisitos

| Item | Versão |
|---|---|
| Python | **3.12** ou superior (testado com 3.12) |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | qualquer versão recente |
| Internet | só na 1ª vez, para baixar as dependências (~1–2 min). Depois tudo roda offline |

O modelo (`models/model.pkl`) **já está no repositório** — não é preciso baixar dados nem treinar nada para servir a API.

## Do `git clone` à primeira predição

```bash
git clone https://github.com/apabsp/projetoML.git
cd projetoML

uv sync --locked                       # instala dependências exatas do uv.lock (~1–2 min na 1ª vez)
uv run bentoml serve src.service:DetectaRiscoService --port 3000   # sobe em ~10 s
```

Quando aparecer `Service detectarisco initialized`, abra **outro terminal** (na pasta do projeto) e rode:

```bash
curl -s -X POST http://localhost:3000/estimar_risco \
  -H "Content-Type: application/json" \
  -d '{"trecho":{"uf":"AC","br":364,"km":125,"dia_semana":"Domingo","fase_dia":"Pleno dia"}}'
```

Resposta esperada:

```json
{"nivel": "ALTO", "score": 0.5266, "fatores": ["horário do dia (fase_dia)", "histórico de acidentes neste trecho-período", "histórico de acidentes neste trecho", "feridos graves acumulados no trecho", "pista dupla"], "jurisdicao": {"uop": "UOP01-DEL01-AC", "delegacia": "DEL01-AC", "regional": "SPRF-AC", "municipio": "RIO BRANCO"}, "model_version": "2026.09.22"}
```

Swagger (documentação interativa, gerada automaticamente): <http://localhost:3000>. Se não subir, os exemplos escritos abaixo bastam.

### Alternativa com Docker (sem instalar Python/uv)

```bash
docker compose up --build
```

Sobe no mesmo endereço (`http://localhost:3000`).

## Contrato da API

### `POST /estimar_risco`

**Entrada** (JSON):

| Campo | Tipo | Exemplo | Observação |
|---|---|---|---|
| `trecho.uf` | string | `"AC"` | sigla da UF |
| `trecho.br` | int | `364` | número da BR |
| `trecho.km` | float | `125` | km da rodovia (agrupado em trechos de 10 km) |
| `trecho.dia_semana` | string | `"Domingo"` | maiúsculas/minúsculas e acentos são normalizados |
| `trecho.fase_dia` | string | `"Pleno dia"` | `Amanhecer`, `Pleno dia`, `Anoitecer`, `Plena noite` |

**Saída — sucesso:**

| Campo | Descrição |
|---|---|
| `nivel` | `BAIXO`, `MEDIO` ou `ALTO` |
| `score` | probabilidade da classe prevista (0–1) |
| `fatores` | 5 fatores mais importantes do modelo (importância global, não explicação individual) |
| `jurisdicao` | UOP, delegacia, regional e município responsáveis pelo trecho |
| `model_version` | versão do modelo que respondeu |

**Saída — abstenção (`INSUFICIENTE`):** `score: null`, `fatores: []`, `jurisdicao: null` e um campo `motivo`. Exemplo (trecho com pouco histórico):

```bash
curl -s -X POST http://localhost:3000/estimar_risco \
  -H "Content-Type: application/json" \
  -d '{"trecho":{"uf":"AC","br":364,"km":25,"dia_semana":"Terça-feira","fase_dia":"Pleno dia"}}'
```

```json
{"nivel": "INSUFICIENTE", "score": null, "motivo": "menos de 10 acidentes registrados neste trecho no historico 2020-2024", "fatores": [], "jurisdicao": null, "model_version": "2026.09.22"}
```

**Entrada inválida:** campo faltando devolve HTTP 400 com a lista dos campos ausentes (validação do pydantic).

Mais dois casos de sucesso:

```bash
# MEDIO
curl -s -X POST http://localhost:3000/estimar_risco -H "Content-Type: application/json" \
  -d '{"trecho":{"uf":"AC","br":317,"km":285,"dia_semana":"Sexta-feira","fase_dia":"Plena noite"}}'
# BAIXO
curl -s -X POST http://localhost:3000/estimar_risco -H "Content-Type: application/json" \
  -d '{"trecho":{"uf":"AC","br":317,"km":65,"dia_semana":"Domingo","fase_dia":"Amanhecer"}}'
```

Respostas completas e screenshots do Swagger em [`evidencias/`](evidencias/).

### Outras rotas

| Rota | Uso |
|---|---|
| `GET /livez`, `GET /readyz` | probes padrão do BentoML |
| `POST /treinar` | retreina o modelo (opcional, ver abaixo) |

## Retreinar (opcional)

`POST /treinar` roda o pipeline completo (`build_dataset.py normalize` → `build_dataset.py build` → `train.py`) e recarrega o modelo sem reiniciar o serviço. Requer os CSVs brutos da PRF (variante `todas_causas_tipos`) em `csvs/`.

```bash
curl -s -X POST http://localhost:3000/treinar -H "Content-Type: application/json" -d '{}'
```

Parâmetros opcionais: `forcar_normalizacao`, `km_bin`, `limiar`, `anos_historico`, `ano_alvo`, `test_size`, `seed`. Bloqueia até terminar (segundos se `data/` já tem os parquets; alguns minutos se for reprocessar os CSVs). Chamadas concorrentes recebem "já existe um treinamento em andamento".

## Origem do modelo servido

Modelo: `HistGradientBoostingClassifier` (scikit-learn 1.9.1), versão `2026.09.22`, treinado em 22/09/2026 com `src/train.py` sobre os dados abertos da PRF. Features construídas com acidentes de 2020–2024 e rótulo de risco derivado de 2025; split por trecho (`GroupShuffleSplit`, `test_size=0.25`, `seed=42`). Detalhes, métricas e limitações em `models/metadata.json`.

Limitação declarada: sem dado de exposição (VMDA do DNIT) o modelo mede volume de acidentes, não taxa por veículo-km. Veja também o [guia de arquitetura](docs/ARQUITETURA.md).

## Dados e privacidade

Os dados vêm do portal de dados abertos da PRF. O repositório **não contém dado pessoal**: `models/perfis.parquet` e `models/jurisdicao.parquet` são agregados por trecho (UF, BR, km, dia, fase do dia, contagens e unidades da PRF). Os CSVs brutos (`csvs/`, ~1,2 GB) ficam fora do git.

## Segredos

O projeto não usa chaves, tokens nem senhas. Mesmo assim, `.env` está no `.gitignore` e `.env.example` documenta o padrão (nenhum valor versionado).

## Estrutura

```
src/
├─ service.py         # BentoML: rotas HTTP (/estimar_risco, /treinar)
├─ risco.py           # motor de inferência
├─ pipeline.py        # orquestra o retreino
├─ build_dataset.py   # csvs/ -> data/*.parquet
└─ train.py           # data/ -> models/
models/               # model.pkl, metadata.json, perfis.parquet, jurisdicao.parquet
evidencias/           # curls e screenshots do Swagger
```

CI (`.github/workflows/ci.yml`): smoke test com Docker a cada push.

## Uso de IA

| **Ferramenta** | Claude (Anthropic), via Claude Code |

| **O que foi pedido** | Revisar o repositório a partir da lista de itens obrigatórios e requisitos da entrega. Ajustes no README. |

## Licença

[MIT](LICENSE)
