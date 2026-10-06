# Caso: baixo

## Requisição
```bash
curl -s -X POST http://localhost:3000/estimar_risco \
  -H "Content-Type: application/json" \
  -d '{"trecho":{"uf":"AC","br":317,"km":65,"dia_semana":"Domingo","fase_dia":"Amanhecer"}}'
```

## Resposta
```json
{
    "nivel": "BAIXO",
    "score": 0.8831,
    "fatores": [
        "hor\u00e1rio do dia (fase_dia)",
        "hist\u00f3rico de acidentes neste trecho-per\u00edodo",
        "hist\u00f3rico de acidentes neste trecho",
        "feridos graves acumulados no trecho",
        "pista dupla"
    ],
    "jurisdicao": {
        "uop": "UOP01-DEL01-AC",
        "delegacia": "DEL01-AC",
        "regional": "SPRF-AC",
        "municipio": "SENADOR GUIOMARD"
    },
    "model_version": "2026.09.22"
}
```
