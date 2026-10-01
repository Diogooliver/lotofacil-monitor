import asyncio
import time
from typing import Any

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Lotofácil Monitor V2")

CAIXA_BASE = "https://servicebus2.caixa.gov.br/portaldeloterias/api/lotofacil"
CACHE: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 15 * 60

def cache_get(key):
    item = CACHE.get(key)
    if item and time.time() - item[0] < CACHE_TTL:
        return item[1]
    return None

def cache_put(key, value):
    CACHE[key] = (time.time(), value)

def normalize(draw: dict) -> dict:
    numbers = draw.get("listaDezenas") or draw.get("dezenas") or draw.get("dezenasSorteadasOrdemSorteio") or []
    numbers = [str(x).zfill(2) for x in numbers]
    return {
        "concurso": draw.get("numero") or draw.get("concurso"),
        "data": draw.get("dataApuracao") or draw.get("data") or "",
        "dezenas": sorted(numbers, key=lambda x: int(x)),
        "acumulou": draw.get("acumulado") if "acumulado" in draw else draw.get("acumulou"),
        "valorEstimadoProximo": draw.get("valorEstimadoProximoConcurso"),
        "proximoConcurso": draw.get("numeroConcursoProximo"),
    }

async def fetch_json(client, url):
    r = await client.get(
        url,
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0 LotofacilMonitor/1.0", "Accept": "application/json"},
    )
    r.raise_for_status()
    return r.json()

@app.get("/api/lotofacil/latest")
async def latest():
    cached = cache_get("latest")
    if cached:
        return cached
    async with httpx.AsyncClient(verify=False) as client:
        raw = await fetch_json(client, CAIXA_BASE)
    data = normalize(raw)
    cache_put("latest", data)
    return data

@app.get("/api/lotofacil/history")
async def history(limit: int = Query(50, ge=5, le=100)):
    latest_data = await latest()
    latest_num = int(latest_data["concurso"])
    key = f"history:{latest_num}:{limit}"
    cached = cache_get(key)
    if cached:
        return cached

    urls = [f"{CAIXA_BASE}/{n}" for n in range(latest_num, latest_num - limit, -1)]
    sem = asyncio.Semaphore(8)

    async def one(url):
        async with sem:
            try:
                async with httpx.AsyncClient(verify=False) as client:
                    raw = await fetch_json(client, url)
                return normalize(raw)
            except Exception:
                return None

    results = await asyncio.gather(*(one(u) for u in urls))
    data = [x for x in results if x and x["concurso"]]
    data.sort(key=lambda x: int(x["concurso"]), reverse=True)
    cache_put(key, data)
    return {"concursoAtual": latest_num, "concursos": data}

def strategy_frequency(history, top_n=15):
    counts = {str(i).zfill(2): 0 for i in range(1, 26)}
    for d in history:
        for n in d["dezenas"]:
            counts[n] += 1
    ordered = sorted(counts, key=lambda n: (-counts[n], int(n)))
    return set(ordered[:top_n])

def strategy_recent(history, top_n=15):
    counts = {str(i).zfill(2): 0 for i in range(1, 26)}
    # Give newer draws a little more weight, but keep the rule explicit.
    for age, d in enumerate(history):
        weight = max(1, 10 - age)
        for n in d["dezenas"]:
            counts[n] += weight
    ordered = sorted(counts, key=lambda n: (-counts[n], int(n)))
    return set(ordered[:top_n])

def evaluate_strategy(draws, strategy_name, window=20, pick=15):
    # Backtest: at each historical point, use only draws that occurred before
    # the target draw. We then count how many of the 15 selected numbers hit.
    ordered = sorted(draws, key=lambda x: int(x["concurso"]))
    rows = []
    for i in range(window, len(ordered)):
        prior = ordered[max(0, i-window):i]
        target = ordered[i]
        if strategy_name == "frequency":
            selected = strategy_frequency(prior, pick)
        else:
            selected = strategy_recent(prior, pick)
        hits = len(selected.intersection(target["dezenas"]))
        rows.append({
            "concurso": target["concurso"],
            "acertos": hits,
            "selecionadas": sorted(selected, key=int),
            "resultado": target["dezenas"],
        })
    if not rows:
        return {"estrategia": strategy_name, "janela": window, "testes": 0, "mediaAcertos": None, "distribuicao": {}}
    dist = {}
    for r in rows:
        dist[str(r["acertos"])] = dist.get(str(r["acertos"]), 0) + 1
    avg = sum(r["acertos"] for r in rows) / len(rows)
    return {
        "estrategia": strategy_name,
        "janela": window,
        "testes": len(rows),
        "mediaAcertos": round(avg, 3),
        "minAcertos": min(r["acertos"] for r in rows),
        "maxAcertos": max(r["acertos"] for r in rows),
        "distribuicao": dist,
        "ultimos": rows[-10:],
    }

@app.get("/api/lotofacil/backtest")
async def backtest(limit: int = Query(100, ge=30, le=500), window: int = Query(20, ge=5, le=100)):
    h = await history(limit)
    draws = h["concursos"]
    return {
        "amostra": len(draws),
        "janela": window,
        "resultados": [
            evaluate_strategy(draws, "frequency", window),
            evaluate_strategy(draws, "recent", window),
        ],
        "observacao": "Backtest descritivo sobre dados históricos; não representa garantia ou previsão do próximo concurso."
    }

@app.get("/api/health")
async def health():
    return {"ok": True, "service": "Lotofácil Monitor V2"}

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def index():
    return FileResponse("static/index.html")
