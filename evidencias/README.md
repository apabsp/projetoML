# Evidências

- `curl_alto.md`, `curl_medio.md`, `curl_baixo.md` — os 3 casos de sucesso
  (RE-7/RE-1 na resposta: fatores e jurisdição).
- `curl_insuficiente.md` — o caso de erro: abstenção RE-6.
- `evidencia-alta.png`, `evidencia-media.png`, `evidencia-baixa.png`,
  `evidencia-insuficiente.png` — screenshot do Swagger (`http://localhost:3000`,
  fica na raiz) pra cada um dos 4 casos acima, request e response reais.
- `ROTEIRO_ENSAIO.md` — roteiro cronometrado dos 15 min de apresentação.

Todos os arquivos foram gerados rodando o serviço real
(`docker compose up --build`) com exemplos verdadeiros do dataset — não são
inventados. Pra regenerar depois de um retreino, repita os comandos dentro
de cada `curl_*.md` ou refaça os mesmos payloads no Swagger.
