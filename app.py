import time
from typing import Any

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Lotofácil Monitor V4")


# ============================================================
# FONTE DE DADOS
# ============================================================

DATA_URL = "https://valorfinal.com.br/data/lotofacil-historico.json"

CACHE: dict[str, tuple[float, Any]] = {}

# 15 minutos
CACHE_TTL = 15 * 60


# ============================================================
# CACHE
# ============================================================

def cache_get(key: str):
    item = CACHE.get(key)

    if item is None:
        return None

    timestamp, value = item

    if time.time() - timestamp < CACHE_TTL:
        return value

    CACHE.pop(key, None)

    return None


def cache_put(key: str, value: Any):
    CACHE[key] = (time.time(), value)


# ============================================================
# NORMALIZAÇÃO
# ============================================================

def normalize_numbers(numbers):
    if not isinstance(numbers, list):
        return []

    result = []

    for number in numbers:

        try:
            value = int(number)

            if 1 <= value <= 25:
                result.append(str(value).zfill(2))

        except (TypeError, ValueError):
            continue

    return sorted(
        list(dict.fromkeys(result)),
        key=lambda x: int(x)
    )


def normalize_draw(item):
    """
    Formato esperado do ValorFinal:

    [
        concurso,
        data,
        [dezenas]
    ]
    """

    if not isinstance(item, list):
        return None

    if len(item) < 3:
        return None

    try:
        concurso = int(item[0])
    except (TypeError, ValueError):
        return None

    data = str(item[1] or "")

    dezenas = normalize_numbers(item[2])

    if len(dezenas) != 15:
        return None

    return {
        "concurso": concurso,
        "data": data,
        "dezenas": dezenas,
        "acumulou": None,
        "valorEstimadoProximo": None,
        "proximoConcurso": concurso + 1,
    }


# ============================================================
# BUSCA DO HISTÓRICO
# ============================================================

async def fetch_history():

    cached = cache_get("history_all")

    if cached is not None:
        return cached

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 "
            "Version/18.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "application/json,text/plain,*/*",
    }

    try:

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10,
                read=30,
                write=10,
                pool=10,
            ),
            follow_redirects=True,
        ) as client:

            response = await client.get(
                DATA_URL,
                headers=headers,
            )

            response.raise_for_status()

            raw = response.json()

    except Exception as exc:

        raise RuntimeError(
            f"Falha ao consultar fonte de dados: {exc}"
        ) from exc

    if not isinstance(raw, dict):

        raise RuntimeError(
            "A fonte retornou um formato JSON inesperado."
        )

    raw_draws = raw.get("concursos", [])

    if not isinstance(raw_draws, list):

        raise RuntimeError(
            "O campo 'concursos' não foi encontrado."
        )

    draws = []

    for item in raw_draws:

        draw = normalize_draw(item)

        if draw is not None:
            draws.append(draw)

    draws.sort(
        key=lambda x: x["concurso"]
    )

    if not draws:

        raise RuntimeError(
            "Nenhum concurso válido foi encontrado."
        )

    cache_put(
        "history_all",
        draws
    )

    return draws


# ============================================================
# ÚLTIMO CONCURSO
# ============================================================

@app.get("/api/lotofacil/latest")
async def latest():

    cached = cache_get("latest")

    if cached is not None:
        return cached

    try:

        draws = await fetch_history()

        latest_draw = draws[-1]

        result = {
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

        cache_put(
            "latest",
            result
        )

        return result

    except Exception as exc:

        return {
            "ok": False,
            "erro": "Não foi possível carregar o último concurso.",
            "detalhe": str(exc),
        }


# ============================================================
# HISTÓRICO
# ============================================================

@app.get("/api/lotofacil/history")
async def history(
    limit: int = Query(
        50,
        ge=5,
        le=500,
    )
):

    try:

        draws = await fetch_history()

        selected = draws[-limit:]

        selected = list(
            reversed(selected)
        )

        return {
            "ok": True,
            "concursoAtual": draws[-1]["concurso"],
            "amostra": len(selected),
            "concursos": selected,
        }

    except Exception as exc:

        return {
            "ok": False,
            "erro": "Não foi possível carregar o histórico.",
            "detalhe": str(exc),
            "concursos": [],
        }


# ============================================================
# ESTRATÉGIA 1 — FREQUÊNCIA
# ============================================================

def strategy_frequency(
    history,
    top_n=15,
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
        key=lambda number: (
            -counts[number],
            int(number),
        ),
    )

    return set(
        ordered[:top_n]
    )


# ============================================================
# ESTRATÉGIA 2 — RECENTES
# ============================================================

def strategy_recent(
    history,
    top_n=15,
):

    counts = {
        str(i).zfill(2): 0
        for i in range(1, 26)
    }

    for age, draw in enumerate(history):

        # Concursos mais recentes recebem
        # um peso maior.
        weight = max(
            1,
            10 - age,
        )

        for number in draw["dezenas"]:

            counts[number] += weight

    ordered = sorted(
        counts,
        key=lambda number: (
            -counts[number],
            int(number),
        ),
    )

    return set(
        ordered[:top_n]
    )


# ============================================================
# BACKTEST
# ============================================================

def evaluate_strategy(
    draws,
    strategy_name,
    window=20,
    pick=15,
):

    ordered = sorted(
        draws,
        key=lambda x: int(x["concurso"]),
    )

    rows = []

    for i in range(
        window,
        len(ordered),
    ):

        prior = ordered[
            i - window:i
        ]

        target = ordered[i]

        if strategy_name == "frequency":

            selected = strategy_frequency(
                prior,
                pick,
            )

        else:

            selected = strategy_recent(
                prior,
                pick,
            )

        hits = len(
            selected.intersection(
                target["dezenas"]
            )
        )

        rows.append(
            {
                "concurso": target["concurso"],
                "acertos": hits,
                "selecionadas": sorted(
                    selected,
                    key=int,
                ),
                "resultado": target["dezenas"],
            }
        )

    if not rows:

        return {
            "estrategia": strategy_name,
            "janela": window,
            "testes": 0,
            "mediaAcertos": None,
            "minAcertos": None,
            "maxAcertos": None,
            "distribuicao": {},
            "ultimos": [],
        }

    distribution = {}

    for row in rows:

        hits = str(
            row["acertos"]
        )

        distribution[hits] = (
            distribution.get(hits, 0)
            + 1
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
            3,
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
        "ultimos": rows[-10:],
    }


# ============================================================
# ENDPOINT DO BACKTEST
# ============================================================

@app.get("/api/lotofacil/backtest")
async def backtest(
    limit: int = Query(
        100,
        ge=30,
        le=500,
    ),
    window: int = Query(
        20,
        ge=5,
        le=100,
    ),
):

    try:

        draws = await fetch_history()

        draws = draws[-limit:]

        frequency = evaluate_strategy(
            draws,
            "frequency",
            window,
        )

        recent = evaluate_strategy(
            draws,
            "recent",
            window,
        )

        return {
            "ok": True,
            "amostra": len(draws),
            "janela": window,
            "resultados": [
                frequency,
                recent,
            ],
            "observacao": (
                "Backtest descritivo baseado "
                "em resultados históricos. "
                "Não representa garantia ou "
                "previsão do próximo concurso."
            ),
        }

    except Exception as exc:

        return {
            "ok": False,
            "erro": "Não foi possível executar o backtest.",
            "detalhe": str(exc),
            "resultados": [],
        }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/api/health")
async def health():

    return {
        "ok": True,
        "service": "Lotofácil Monitor V4",
    }


# ============================================================
# ARQUIVOS ESTÁTICOS
# ============================================================

app.mount(
    "/static",
    StaticFiles(
        directory="static"
    ),
    name="static",
)


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

@app.get("/")
async def index():

    return FileResponse(
        "static/index.html"
    )
