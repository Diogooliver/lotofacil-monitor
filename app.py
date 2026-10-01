import random
import time
from collections import Counter
from typing import Any

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Lotofácil Monitor V6")

DATA_URL = "https://valorfinal.com.br/data/lotofacil-historico.json"

CACHE: dict[str, tuple[float, Any]] = {}
CACHE_TTL = 15 * 60


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
    return sorted(list(dict.fromkeys(result)), key=int)


def normalize_draw(item):
    if not isinstance(item, list) or len(item) < 3:
        return None
    try:
        concurso = int(item[0])
    except (TypeError, ValueError):
        return None

    dezenas = normalize_numbers(item[2])
    if len(dezenas) != 15:
        return None

    return {
        "concurso": concurso,
        "data": str(item[1] or ""),
        "dezenas": dezenas,
    }


async def fetch_history():
    cached = cache_get("history_all")
    if cached is not None:
        return cached

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "application/json,text/plain,*/*",
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
            follow_redirects=True,
        ) as client:
            response = await client.get(DATA_URL, headers=headers)
            response.raise_for_status()
            raw = response.json()
    except Exception as exc:
        raise RuntimeError(f"Falha ao consultar fonte de dados: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuntimeError("A fonte retornou um formato JSON inesperado.")

    raw_draws = raw.get("concursos", [])
    if not isinstance(raw_draws, list):
        raise RuntimeError("O campo 'concursos' não foi encontrado.")

    draws = [d for item in raw_draws if (d := normalize_draw(item)) is not None]
    draws.sort(key=lambda x: x["concurso"])

    if not draws:
        raise RuntimeError("Nenhum concurso válido foi encontrado.")

    cache_put("history_all", draws)
    return draws


def frequency_stats(draws):
    counts = Counter()
    for draw in draws:
        counts.update(draw["dezenas"])
    return {str(i).zfill(2): counts[str(i).zfill(2)] for i in range(1, 26)}


def overdue_stats(draws):
    ordered = sorted(draws, key=lambda x: x["concurso"], reverse=True)
    result = {}
    for n in range(1, 26):
        key = str(n).zfill(2)
        gap = len(ordered)
        for idx, draw in enumerate(ordered):
            if key in draw["dezenas"]:
                gap = idx
                break
        result[key] = gap
    return result


def score_numbers(draws, recent_window=20):
    recent = draws[-recent_window:]
    overall = frequency_stats(draws)
    recent_counts = frequency_stats(recent)
    overdue = overdue_stats(draws)

    max_overall = max(overall.values()) or 1
    max_recent = max(recent_counts.values()) or 1
    max_overdue = max(overdue.values()) or 1

    scores = {}
    for i in range(1, 26):
        n = str(i).zfill(2)
        scores[n] = round(
            0.45 * (overall[n] / max_overall)
            + 0.35 * (recent_counts[n] / max_recent)
            + 0.20 * (overdue[n] / max_overdue),
            6,
        )
    return scores, overall, recent_counts, overdue


def build_candidate(draws, recent_window=20, offset=0):
    scores, _, _, _ = score_numbers(draws, recent_window)
    ordered = sorted(scores, key=lambda n: (-scores[n], int(n)))

    # Deterministic candidate from information available BEFORE the target draw.
    ranked = ordered[offset:] + ordered[:offset]
    selected = []
    odd_count = 0

    for n in ranked:
        if len(selected) >= 15:
            break
        if int(n) % 2 == 1:
            if odd_count >= 8:
                continue
            odd_count += 1
        selected.append(n)

    for n in ranked:
        if len(selected) >= 15:
            break
        if n not in selected:
            selected.append(n)

    return sorted(selected[:15], key=int)


def random_game():
    return sorted(
        [str(n).zfill(2) for n in random.sample(range(1, 26), 15)],
        key=int,
    )


def hit_count(game, target):
    return len(set(game).intersection(target["dezenas"]))


def walk_forward(
    draws,
    warmup=100,
    recent_window=20,
    tests=300,
    random_trials=5,
):
    ordered = sorted(draws, key=lambda x: int(x["concurso"]))
    if len(ordered) <= warmup:
        return {
            "testes": 0,
            "warmup": warmup,
            "estrategia": [],
            "aleatorio": [],
            "distribuicao": {},
        }

    usable = ordered[warmup:]
    if tests:
        usable = usable[-tests:]

    strategy_hits = []
    random_hits = []

    for i, target in enumerate(usable):
        # Find the target in the full chronological sequence.
        target_index = next(
            idx for idx, d in enumerate(ordered)
            if d["concurso"] == target["concurso"]
        )
        prior = ordered[:target_index]

        if len(prior) < recent_window:
            continue

        candidate = build_candidate(
            prior,
            recent_window=recent_window,
            offset=i % 3,
        )
        strategy_hits.append(hit_count(candidate, target))

        # Fixed-seed deterministic random baseline, generated without seeing target.
        rng = random.Random(20261001 + target["concurso"])
        for _ in range(random_trials):
            game = sorted(
                [str(n).zfill(2) for n in rng.sample(range(1, 26), 15)],
                key=int,
            )
            random_hits.append(hit_count(game, target))

    def summarize(values):
        if not values:
            return {
                "testes": 0,
                "mediaAcertos": None,
                "minAcertos": None,
                "maxAcertos": None,
                "distribuicao": {},
                "acertos11ouMais": 0,
                "acertos12ouMais": 0,
                "acertos13ouMais": 0,
                "acertos14ouMais": 0,
                "acertos15": 0,
            }
        c = Counter(values)
        return {
            "testes": len(values),
            "mediaAcertos": round(sum(values) / len(values), 3),
            "minAcertos": min(values),
            "maxAcertos": max(values),
            "distribuicao": dict(sorted(c.items())),
            "acertos11ouMais": sum(v for k, v in c.items() if k >= 11),
            "acertos12ouMais": sum(v for k, v in c.items() if k >= 12),
            "acertos13ouMais": sum(v for k, v in c.items() if k >= 13),
            "acertos14ouMais": sum(v for k, v in c.items() if k >= 14),
            "acertos15": c.get(15, 0),
        }

    return {
        "testes": len(strategy_hits),
        "warmup": warmup,
        "recent_window": recent_window,
        "estrategia": summarize(strategy_hits),
        "aleatorio": summarize(random_hits),
        "randomTrialsPorConcurso": random_trials,
    }


@app.get("/api/lotofacil/latest")
async def latest():
    try:
        draws = await fetch_history()
        d = draws[-1]
        return {
            "ok": True,
            "concurso": d["concurso"],
            "data": d["data"],
            "dezenas": d["dezenas"],
            "proximoConcurso": d["concurso"] + 1,
        }
    except Exception as exc:
        return {
            "ok": False,
            "erro": "Não foi possível carregar o último concurso.",
            "detalhe": str(exc),
        }


@app.get("/api/lotofacil/history")
async def history(limit: int = Query(10, ge=5, le=500)):
    try:
        draws = await fetch_history()
        return {
            "ok": True,
            "concursoAtual": draws[-1]["concurso"],
            "amostra": min(limit, len(draws)),
            "concursos": list(reversed(draws[-limit:])),
        }
    except Exception as exc:
        return {
            "ok": False,
            "erro": "Não foi possível carregar o histórico.",
            "detalhe": str(exc),
            "concursos": [],
        }


@app.get("/api/lotofacil/analysis")
async def analysis(
    limit: int = Query(100, ge=30, le=1000),
    recent: int = Query(20, ge=5, le=100),
):
    try:
        draws = await fetch_history()
        sample = draws[-limit:]
        scores, overall, recent_counts, overdue = score_numbers(
            sample, min(recent, len(sample))
        )

        hottest = sorted(overall, key=lambda n: (-overall[n], int(n)))[:10]
        coldest = sorted(overall, key=lambda n: (overall[n], int(n)))[:10]
        late = sorted(overdue, key=lambda n: (-overdue[n], int(n)))[:10]

        parity = []
        for draw in sample:
            evens = sum(int(n) % 2 == 0 for n in draw["dezenas"])
            parity.append((evens, 15 - evens))

        groups = {
            "01-05": set(str(i).zfill(2) for i in range(1, 6)),
            "06-10": set(str(i).zfill(2) for i in range(6, 11)),
            "11-15": set(str(i).zfill(2) for i in range(11, 16)),
            "16-20": set(str(i).zfill(2) for i in range(16, 21)),
            "21-25": set(str(i).zfill(2) for i in range(21, 26)),
        }
        ranges = {}
        for name, nums in groups.items():
            ranges[name] = round(
                sum(len(nums.intersection(d["dezenas"])) for d in sample)
                / len(sample),
                3,
            )

        return {
            "ok": True,
            "amostra": len(sample),
            "concursoAtual": draws[-1]["concurso"],
            "quentes": [{"numero": n, "ocorrencias": overall[n]} for n in hottest],
            "frios": [{"numero": n, "ocorrencias": overall[n]} for n in coldest],
            "atrasados": [
                {"numero": n, "concursosSemSair": overdue[n]} for n in late
            ],
            "paresMedia": round(sum(x[0] for x in parity) / len(parity), 3),
            "imparesMedia": round(sum(x[1] for x in parity) / len(parity), 3),
            "faixasMedia": ranges,
            "pontuacao": [
                {"numero": n, "score": scores[n]}
                for n in sorted(scores, key=lambda x: (-scores[x], int(x)))
            ],
            "observacao": (
                "Estatísticas históricas. Não representam previsão "
                "ou garantia do próximo concurso."
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "erro": "Não foi possível executar a análise.",
            "detalhe": str(exc),
        }


@app.get("/api/lotofacil/walkforward")
async def walkforward(
    warmup: int = Query(100, ge=30, le=1000),
    recent: int = Query(20, ge=5, le=100),
    tests: int = Query(300, ge=30, le=1000),
    random_trials: int = Query(5, ge=1, le=20),
):
    try:
        draws = await fetch_history()
        result = walk_forward(
            draws,
            warmup=warmup,
            recent_window=recent,
            tests=tests,
            random_trials=random_trials,
        )
        return {
            "ok": True,
            "concursoAtual": draws[-1]["concurso"],
            **result,
            "observacao": (
                "Walk-forward: cada concurso é testado usando apenas "
                "concursos anteriores ao alvo. O baseline aleatório usa "
                "jogos gerados sem acesso ao resultado-alvo. Isso melhora "
                "a avaliação histórica, mas não demonstra vantagem futura."
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "erro": "Não foi possível executar o walk-forward.",
            "detalhe": str(exc),
        }


@app.get("/api/lotofacil/candidate")
async def candidate(
    recent: int = Query(20, ge=5, le=100),
    offset: int = Query(0, ge=0, le=4),
):
    try:
        draws = await fetch_history()
        game = build_candidate(draws, recent_window=recent, offset=offset)
        scores, _, _, _ = score_numbers(draws, recent)
        return {
            "ok": True,
            "concursoBase": draws[-1]["concurso"],
            "janela": recent,
            "dezenas": game,
            "scoreMedio": round(sum(scores[n] for n in game) / 15, 4),
            "observacao": (
                "Conjunto candidato gerado para análise estatística; "
                "não é previsão nem garantia de prêmio."
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "erro": "Não foi possível gerar o candidato.",
            "detalhe": str(exc),
        }


@app.get("/api/lotofacil/backtest")
async def backtest(
    limit: int = Query(100, ge=30, le=500),
    window: int = Query(20, ge=5, le=100),
):
    try:
        draws = await fetch_history()
        sample = draws[-limit:]
        rows = []
        for i in range(window, len(sample)):
            prior = sample[i - window:i]
            target = sample[i]
            counts = frequency_stats(prior)
            ordered = sorted(counts, key=lambda n: (-counts[n], int(n)))
            selected = set(ordered[:15])
            rows.append(len(selected.intersection(target["dezenas"])))

        c = Counter(rows)
        return {
            "ok": True,
            "amostra": len(sample),
            "janela": window,
            "resultados": [{
                "estrategia": "frequency",
                "testes": len(rows),
                "mediaAcertos": round(sum(rows) / len(rows), 3),
                "minAcertos": min(rows),
                "maxAcertos": max(rows),
                "distribuicao": dict(sorted(c.items())),
            }],
            "observacao": (
                "Backtest histórico simples. O walk-forward da V6 "
                "é a avaliação principal para evitar olhar o futuro."
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "erro": "Não foi possível executar o backtest.",
            "detalhe": str(exc),
        }


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "Lotofácil Monitor V6"}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")
