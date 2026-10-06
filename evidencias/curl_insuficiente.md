# Caso: insuficiente

## Requisição
```bash
curl -s -X POST http://localhost:3000/estimar_risco \
  -H "Content-Type: application/json" \
  -d '{"trecho":{"uf":"AC","br":364,"km":25,"dia_semana":"Terça-feira","fase_dia":"Pleno dia"}}'
```

## Resposta
```json
{
    "nivel": "INSUFICIENTE",
    "score": null,
    "motivo": "menos de 10 acidentes registrados neste trecho no historico 2020-2024",
    "fatores": [],
    "jurisdicao": null,
    "model_version": "2026.09.22"
}
```
