# Caso: medio

## Requisição
```bash
curl -s -X POST http://localhost:3000/estimar_risco \
  -H "Content-Type: application/json" \
  -d '{"trecho":{"uf":"AC","br":317,"km":285,"dia_semana":"Sexta-feira","fase_dia":"Plena noite"}}'
```

## Resposta
```json
{
    "nivel": "MEDIO",
    "score": 0.4445,
    "fatores": [
        "hor\u00e1rio do dia (fase_dia)",
        "hist\u00f3rico de acidentes neste trecho-per\u00edodo",
        "hist\u00f3rico de acidentes neste trecho",
        "feridos graves acumulados no trecho",
        "pista dupla"
    ],
    "jurisdicao": {
        "uop": "UOP02-DEL01-AC",
        "delegacia": "DEL01-AC",
        "regional": "SPRF-AC",
        "municipio": "EPITACIOLANDIA"
    },
    "model_version": "2026.09.22"
}
```
