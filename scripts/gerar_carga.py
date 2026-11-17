"""
DetectaRisco - gerador de carga para ver o rastro (logs/inferencia.jsonl) e o
/metrics evoluirem.

Os pedidos sao sorteados do proprio conjunto de referencia
(models/perfis.parquet, o universo de trechos-periodo usado no treino). Dois
cenarios:

  referencia  sorteio uniforme do universo de treino. As distribuicoes do log
              devem bater com a referencia (PSI ~ 0, KS sem rejeicao).

  drift       simula uma mudanca no padrao de uso: gestor planejando operacao
              de fim de semana a noite, consultando sobretudo trechos criticos
              (sorteio ponderado pelo historico de acidentes). Desloca
              dia_semana, fase_dia, tr_n_acidentes, tp_n_acidentes e prob_alto
              - o KS/PSI tem de acusar.

Em ambos, uma fracao dos pedidos cai fora da malha avaliavel (resposta
INSUFICIENTE) e outra e invalida de proposito (HTTP 400), para que a taxa de
erros do /metrics e o status do rastro nao fiquem vazios.

So usa biblioteca padrao + pandas (ja e dependencia do projeto).

Exemplos:
    uv run python scripts/gerar_carga.py                       # 300 pedidos, referencia
    uv run python scripts/gerar_carga.py --n 500 --modo drift
    uv run python scripts/gerar_carga.py --n 0 --taxa 2        # infinito, 2 pedidos/s (Ctrl+C para)
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]

# Os perfis guardam os valores normalizados; o cliente real manda com acento
# e maiuscula. A API normaliza - mandar "bonito" testa isso de verdade.
DIAS = {
    "domingo": "Domingo", "segunda-feira": "Segunda-feira", "terca-feira": "Terça-feira",
    "quarta-feira": "Quarta-feira", "quinta-feira": "Quinta-feira",
    "sexta-feira": "Sexta-feira", "sabado": "Sábado",
}
FASES = {"amanhecer": "Amanhecer", "pleno dia": "Pleno dia",
         "anoitecer": "Anoitecer", "plena noite": "Plena noite"}

INVALIDOS = [
    lambda t: {"trecho": {k: v for k, v in t.items() if k != "km"}},          # campo faltando
    lambda t: {"trecho": {**t, "br": "BR-" + str(t["br"])}},                   # tipo errado
    lambda t: {"trecho": {**t, "km": "quilometro"}},                           # tipo errado
    lambda t: {"uf": t["uf"], "br": t["br"]},                                  # sem o envelope
]


def carregar_universo(modo: str) -> tuple[pd.DataFrame, pd.Series | None]:
    perfis = pd.read_parquet(RAIZ / "models" / "perfis.parquet",
                             columns=["uf", "br", "km_bin", "dia_semana", "fase_dia",
                                      "tr_n_acidentes"])
    if modo == "referencia":
        return perfis, None
    recorte = perfis[perfis.dia_semana.isin(["sexta-feira", "sabado", "domingo"])
                     & perfis.fase_dia.isin(["anoitecer", "plena noite"])]
    return recorte, recorte["tr_n_acidentes"]


def montar_trecho(linha, rng: random.Random) -> dict:
    return {
        "uf": linha.uf,
        "br": int(linha.br),
        "km": round(int(linha.km_bin) + rng.uniform(0, 9.9), 1),
        "dia_semana": DIAS[linha.dia_semana],
        "fase_dia": FASES[linha.fase_dia],
    }


def fora_da_malha(base: dict, trechos_validos: set, rng: random.Random) -> dict:
    """Mesmo uf/br, km sem historico suficiente -> INSUFICIENTE."""
    for _ in range(50):
        km = rng.uniform(0, 1200)
        if (base["uf"], base["br"], int(km // 10 * 10)) not in trechos_validos:
            return {**base, "km": round(km, 1)}
    return {**base, "km": 9999.0}


def enviar(url: str, corpo: dict, timeout: float) -> tuple[int, str]:
    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
    pedido = urllib.request.Request(url, data=dados, method="POST",
                                    headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
            return resposta.status, json.loads(resposta.read()).get("nivel", "?")
    except urllib.error.HTTPError as erro:
        return erro.code, f"HTTP {erro.code}"
    except (urllib.error.URLError, TimeoutError, ConnectionError) as erro:
        return 0, f"sem conexao ({erro})"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:3000")
    parser.add_argument("--modo", choices=["referencia", "drift"], default="referencia")
    parser.add_argument("--n", type=int, default=300, help="numero de pedidos (0 = sem fim)")
    parser.add_argument("--taxa", type=float, default=20.0,
                        help="pedidos por segundo (0 = o mais rapido possivel)")
    parser.add_argument("--concorrencia", type=int, default=4)
    parser.add_argument("--frac-fora-malha", type=float, default=0.05)
    parser.add_argument("--frac-invalidos", type=float, default=0.03)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    universo, pesos = carregar_universo(args.modo)
    trechos_validos = set(zip(universo.uf, universo.br, universo.km_bin)) if args.frac_fora_malha else set()
    if args.modo == "drift":  # o conjunto de validos e o universo inteiro, nao o recorte
        perfis = pd.read_parquet(RAIZ / "models" / "perfis.parquet", columns=["uf", "br", "km_bin"])
        trechos_validos = set(zip(perfis.uf, perfis.br, perfis.km_bin))

    url = args.url.rstrip("/") + "/estimar_risco"
    status, cenarios = Counter(), Counter()
    intervalo = 1 / args.taxa if args.taxa > 0 else 0
    print(f"{args.modo}: {'sem fim' if args.n == 0 else args.n} pedidos -> {url} "
          f"({args.taxa or 'max'} req/s, {args.concorrencia} em paralelo)")

    def proximo_corpo() -> dict:
        linha = universo.sample(1, weights=pesos, random_state=rng.randrange(2**32)).iloc[0]
        trecho = montar_trecho(linha, rng)
        sorteio = rng.random()
        if sorteio < args.frac_invalidos:
            cenarios["invalido"] += 1
            return rng.choice(INVALIDOS)(trecho)
        if sorteio < args.frac_invalidos + args.frac_fora_malha:
            cenarios["fora_da_malha"] += 1
            return {"trecho": fora_da_malha(trecho, trechos_validos, rng)}
        cenarios[args.modo] += 1
        return {"trecho": trecho}

    enviados, inicio = 0, time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=args.concorrencia) as pool:
            futuros = []
            while args.n == 0 or enviados < args.n:
                futuros.append(pool.submit(enviar, url, proximo_corpo(), args.timeout))
                enviados += 1
                if enviados % 50 == 0:
                    print(f"  {enviados} enviados...")
                if intervalo:
                    atraso = inicio + enviados * intervalo - time.monotonic()
                    if atraso > 0:
                        time.sleep(atraso)
                futuros = [f for f in futuros if not _contabilizar(f, status)]
            for f in futuros:
                status[f.result()[1]] += 1
    except KeyboardInterrupt:
        print("\ninterrompido.")

    duracao = time.monotonic() - inicio
    print(f"\n{enviados} pedidos em {duracao:.1f} s ({enviados / max(duracao, 1e-9):.1f} req/s)")
    print("cenarios sorteados:", dict(cenarios))
    print("respostas:         ", dict(status))
    if any(chave.startswith("sem conexao") for chave in status):
        print("\nO servico nao respondeu. Ele esta no ar? (ver README, 'Do git clone a primeira predicao')")
        return 1
    return 0


def _contabilizar(futuro, status: Counter) -> bool:
    if futuro.done():
        status[futuro.result()[1]] += 1
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
