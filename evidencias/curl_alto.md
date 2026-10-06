# Caso: alto

## Requisição
```bash
curl -s -X POST http://localhost:3000/estimar_risco \
  -H "Content-Type: application/json" \
  -d '{"trecho":{"uf":"AC","br":364,"km":125,"dia_semana":"Domingo","fase_dia":"Pleno dia"}}'
```

## Resposta
```json
{
    "nivel": "ALTO",
    "score": 0.5266,
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
        "municipio": "RIO BRANCO"
    },
    "model_version": "2026.09.22"
}
```
