import time
from typing import Any

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Lotofácil Monitor V3")

# Fonte alternativa aos servidores da Caixa.
# O arquivo contém os resultados históricos da Lotofácil.
DATA_URL = (
    "https://raw.githubusercontent.com/"
    "guilhermeasn/loteria.json/master/data/lotofacil.json"
)

CACHE: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 15 * 60


def cache_get(key):
    item = CACHE.get(key)

    if item and time.time() - item[0] < CACHE_TTL:
        return item[1]

    return None


def cache_put(key, value):
    CACHE[key] = (time.time(), value)


def normalize_numbers(numbers):
    if not numbers:
        return []

    return sorted(
        [str(x).zfill(2) for x in numbers],
        key=lambda x: int(x)
    )


def normalize_draw(concurso, numbers):
    return {
        "concurso": int(concurso),
        "data": "",
        "dezenas": normalize_numbers(numbers),
        "acumulou": None,
        "valorEstimadoProximo": None,
        "proximoConcurso": int(concurso) + 1,
    }


async def fetch_history():
    cached = cache_get("all_history")

    if cached:
        return cached

    headers = {
        "User-Agent": "Mozilla/5.0 LotofacilMonitor/3.0",
        "Accept": "application/json,text/plain,*/*",
    }

    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True
    ) as client:

        response = await client.get(
            DATA_URL,
            headers=headers
        )

        response.raise_for_status()

        raw = response.json()

    draws = []

    # Formato esperado:
    # {
    #   "1": [1,2,3,...],
    #   "2": [1,2,3,...],
    #   ...
    # }

    if isinstance(raw, dict):

        for concurso, numbers in raw.items():

            try:
                numero = int(concurso)
            except (TypeError, ValueError):
                continue

            if isinstance(numbers, list):

                draw = normalize_draw(
                    numero,
                    numbers
                )

                if len(draw["dezenas"]) == 15:
                    draws.append(draw)

    draws.sort(
        key=lambda x: int(x["concurso"])
    )

    cache_put("all_history", draws)

    return draws


@app.get("/api/lotofacil/latest")
async def latest():

    cached = cache_get("latest")

    if cached:
        return cached

    try:
        draws = await fetch_history()

        if not draws:
            return {
                "erro": "Nenhum resultado encontrado."
            }

        latest_draw = draws[-1]

        data = {
            "concurso": latest_draw["concurso"],
            "data": latest_draw["data"],
            "dezenas": latest_draw["dezenas"],
            "acumulou": latest_draw["acumulou"],
            "valorEstimadoProximo": latest_draw[
                "valorEstimadoProximo"
            ],
            "proximoConcurso": latest_draw[
                "proximoConcurso"
            ],
        }

        cache_put("latest", data)

        return data

    except Exception as e:

        return {
            "erro": "Não foi possível carregar o resultado.",
            "detalhe": str(e)
        }


@app.get("/api/lotofacil/history")
async def history(
    limit: int = Query(
        50,
        ge=5,
        le=500
    )
):

    try:

        draws = await fetch_history()

        selected = draws[-limit:]

        selected = list(
            reversed(selected)
        )

        latest_num = (
            draws[-1]["concurso"]
            if draws
            else None
        )

        return {
            "concursoAtual": latest_num,
            "concursos": selected
        }

    except Exception as e:

        return {
            "erro": "Não foi possível carregar o histórico.",
            "detalhe": str(e),
            "concursos": []
        }


def strategy_frequency(
    history,
    top_n=15
):

    counts = {
        str(i).zfill(2): 0
        for i in range(1, 26)
    }

    for draw in history:

        for number in draw["dezenas"]:

            counts[number] += 1

    ordered = sorted(
        counts,
        key=lambda n: (
            -counts[n],
            int(n)
        )
    )

    return set(
        ordered[:top_n]
    )


def strategy_recent(
    history,
    top_n=15
):

    counts = {
        str(i).zfill(2): 0
        for i in range(1, 26)
    }

    for age, draw in enumerate(history):

        weight = max(
            1,
            10 - age
        )

        for number in draw["dezenas"]:

            counts[number] += weight

    ordered = sorted(
        counts,
        key=lambda n: (
            -counts[n],
            int(n)
        )
    )

    return set(
        ordered[:top_n]
    )


def evaluate_strategy(
    draws,
    strategy_name,
    window=20,
    pick=15
):

    ordered = sorted(
        draws,
        key=lambda x: int(x["concurso"])
    )

    rows = []

    for i in range(
        window,
        len(ordered)
    ):

        prior = ordered[
            max(0, i - window):i
        ]

        target = ordered[i]

        if strategy_name == "frequency":

            selected = strategy_frequency(
                prior,
                pick
            )

        else:

            selected = strategy_recent(
                prior,
                pick
            )

        hits = len(
            selected.intersection(
                target["dezenas"]
            )
        )

        rows.append({
            "concurso": target["concurso"],
            "acertos": hits,
            "selecionadas": sorted(
                selected,
                key=int
            ),
            "resultado": target["dezenas"],
        })

    if not rows:

        return {
            "estrategia": strategy_name,
            "janela": window,
            "testes": 0,
            "mediaAcertos": None,
            "distribuicao": {}
        }

    distribution = {}

    for row in rows:

        hits = str(
            row["acertos"]
        )

        distribution[hits] = (
            distribution.get(hits, 0) + 1
        )

    average = (
        sum(
            row["acertos"]
            for row in rows
        )
        / len(rows)
    )

    return {
        "estrategia": strategy_name,
        "janela": window,
        "testes": len(rows),
        "mediaAcertos": round(
            average,
            3
        ),
        "minAcertos": min(
            row["acertos"]
            for row in rows
        ),
        "maxAcertos": max(
            row["acertos"]
            for row in rows
        ),
        "distribuicao": distribution,
        "ultimos": rows[-10:]
    }


@app.get("/api/lotofacil/backtest")
async def backtest(
    limit: int = Query(
        100,
        ge=30,
        le=500
    ),
    window: int = Query(
        20,
        ge=5,
        le=100
    )
):

    draws = await fetch_history()

    draws = draws[-limit:]

    return {
        "amostra": len(draws),
        "janela": window,
        "resultados": [
            evaluate_strategy(
                draws,
                "frequency",
                window
            ),
            evaluate_strategy(
                draws,
                "recent",
                window
            )
        ],
        "observacao": (
            "Backtest descritivo sobre dados "
            "históricos; não representa garantia "
            "ou previsão do próximo concurso."
        )
    }


@app.get("/api/health")
async def health():

    return {
        "ok": True,
        "service": "Lotofácil Monitor V3"
    }


app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)


@app.get("/")
async def index():

    return FileResponse(
        "static/index.html"
    )
