import time
from collections import Counter
from typing import Any

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Lotofácil Monitor V5")

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


def parity_stats(draws):
    values = []
    for draw in draws:
        evens = sum(int(n) % 2 == 0 for n in draw["dezenas"])
        values.append({
            "concurso": draw["concurso"],
            "pares": evens,
            "impares": 15 - evens,
        })
    return values


def range_stats(draws):
    groups = {
        "01-05": set(str(i).zfill(2) for i in range(1, 6)),
        "06-10": set(str(i).zfill(2) for i in range(6, 11)),
        "11-15": set(str(i).zfill(2) for i in range(11, 16)),
        "16-20": set(str(i).zfill(2) for i in range(16, 21)),
        "21-25": set(str(i).zfill(2) for i in range(21, 26)),
    }
    totals = {key: 0 for key in groups}
    for draw in draws:
        for key, nums in groups.items():
            totals[key] += len(nums.intersection(draw["dezenas"]))
    return {
        key: round(total / len(draws), 3) if draws else 0
        for key, total in totals.items()
    }


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
        # Score is descriptive: frequency + recent frequency + overdue.
        # It is not a probability or prediction.
        score = (
            0.45 * (overall[n] / max_overall)
            + 0.35 * (recent_counts[n] / max_recent)
            + 0.20 * (overdue[n] / max_overdue)
        )
        scores[n] = round(score, 4)
    return scores, overall, recent_counts, overdue


def build_candidate_games(draws, game_count=5, recent_window=20):
    scores, overall, recent_counts, overdue = score_numbers(
        draws, recent_window
    )
    ordered = sorted(scores, key=lambda n: (-scores[n], int(n)))

    games = []
    used_sets = set()

    # These are candidate sets for statistical backtesting, not predictions.
    for offset in range(game_count):
        ranked = ordered[offset:] + ordered[:offset]
        selected = []
        odd_count = 0

        for n in ranked:
            if len(selected) >= 15:
                break

            # Keep a roughly balanced parity profile.
            if int(n) % 2 == 1:
                if odd_count >= 8:
                    continue
                odd_count += 1
            selected.append(n)

        # Fill if parity filter skipped too much.
        for n in ranked:
            if len(selected) >= 15:
                break
            if n not in selected:
                selected.append(n)

        selected = sorted(selected[:15], key=int)
        key = tuple(selected)
        if key in used_sets:
            continue
        used_sets.add(key)

        games.append({
            "id": len(games) + 1,
            "dezenas": selected,
            "pontuacaoMedia": round(
                sum(scores[n] for n in selected) / len(selected), 4
            ),
        })

    return games, scores, overall, recent_counts, overdue


def evaluate_games(draws, games, backtest_window=100):
    test_draws = draws[-backtest_window:]
    results = []

    for game in games:
        selected = set(game["dezenas"])
        hits = [
            len(selected.intersection(draw["dezenas"]))
            for draw in test_draws
        ]
        counter = Counter(hits)
        results.append({
            "id": game["id"],
            "mediaAcertos": round(sum(hits) / len(hits), 3) if hits else None,
            "minAcertos": min(hits) if hits else None,
            "maxAcertos": max(hits) if hits else None,
            "distribuicao": dict(sorted(counter.items())),
        })

    return results


@app.get("/api/lotofacil/latest")
async def latest():
    try:
        draws = await fetch_history()
        latest_draw = draws[-1]
        return {
            "ok": True,
            "concurso": latest_draw["concurso"],
            "data": latest_draw["data"],
            "dezenas": latest_draw["dezenas"],
            "proximoConcurso": latest_draw["concurso"] + 1,
        }
    except Exception as exc:
        return {
            "ok": False,
            "erro": "Não foi possível carregar o último concurso.",
            "detalhe": str(exc),
        }


@app.get("/api/lotofacil/history")
async def history(limit: int = Query(50, ge=5, le=500)):
    try:
        draws = await fetch_history()
        selected = list(reversed(draws[-limit:]))
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

        hottest = sorted(
            overall,
            key=lambda n: (-overall[n], int(n)),
        )[:10]
        coldest = sorted(
            overall,
            key=lambda n: (overall[n], int(n)),
        )[:10]
        most_overdue = sorted(
            overdue,
            key=lambda n: (-overdue[n], int(n)),
        )[:10]

        parity = parity_stats(sample)
        avg_even = round(sum(x["pares"] for x in parity) / len(parity), 3)
        avg_odd = round(sum(x["impares"] for x in parity) / len(parity), 3)

        return {
            "ok": True,
            "amostra": len(sample),
            "concursoAtual": draws[-1]["concurso"],
            "quentes": [
                {"numero": n, "ocorrencias": overall[n]}
                for n in hottest
            ],
            "frios": [
                {"numero": n, "ocorrencias": overall[n]}
                for n in coldest
            ],
            "atrasados": [
                {"numero": n, "concursosSemSair": overdue[n]}
                for n in most_overdue
            ],
            "paresMedia": avg_even,
            "imparesMedia": avg_odd,
            "faixasMedia": range_stats(sample),
            "pontuacao": [
                {"numero": n, "score": scores[n]}
                for n in sorted(scores, key=lambda x: (-scores[x], int(x)))
            ],
            "observacao": (
                "Estatísticas históricas. Frequência, atraso e tendência "
                "não garantem o resultado do próximo concurso."
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "erro": "Não foi possível executar a análise.",
            "detalhe": str(exc),
        }


@app.get("/api/lotofacil/candidates")
async def candidates(
    games: int = Query(5, ge=1, le=10),
    recent: int = Query(20, ge=5, le=100),
    backtest: int = Query(100, ge=30, le=500),
):
    try:
        draws = await fetch_history()
        game_list, scores, overall, recent_counts, overdue = build_candidate_games(
            draws,
            game_count=games,
            recent_window=recent,
        )
        evaluation = evaluate_games(draws, game_list, backtest)
        evaluation_by_id = {x["id"]: x for x in evaluation}

        for game in game_list:
            game["backtest"] = evaluation_by_id.get(game["id"])

        return {
            "ok": True,
            "concursoBase": draws[-1]["concurso"],
            "janelaRecente": recent,
            "amostraBacktest": backtest,
            "jogos": game_list,
            "observacao": (
                "Jogos candidatos gerados por regras estatísticas "
                "para teste. Não são previsões nem garantia de prêmio."
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "erro": "Não foi possível gerar os candidatos.",
            "detalhe": str(exc),
            "jogos": [],
        }


@app.get("/api/lotofacil/backtest")
async def backtest(
    limit: int = Query(100, ge=30, le=500),
    window: int = Query(20, ge=5, le=100),
):
    try:
        draws = await fetch_history()
        draws = draws[-limit:]

        def evaluate(strategy_name):
            rows = []
            for i in range(window, len(draws)):
                prior = draws[i - window:i]
                target = draws[i]

                counts = frequency_stats(prior)
                ordered = sorted(
                    counts,
                    key=lambda n: (-counts[n], int(n)),
                )

                if strategy_name == "recent":
                    recent_counts = frequency_stats(prior[-10:])
                    ordered = sorted(
                        counts,
                        key=lambda n: (
                            -0.6 * counts[n] - 0.4 * recent_counts[n],
                            int(n),
                        ),
                    )

                selected = set(ordered[:15])
                rows.append(
                    len(selected.intersection(target["dezenas"]))
                )

            return {
                "estrategia": strategy_name,
                "janela": window,
                "testes": len(rows),
                "mediaAcertos": round(sum(rows) / len(rows), 3),
                "minAcertos": min(rows),
                "maxAcertos": max(rows),
                "distribuicao": dict(sorted(Counter(rows).items())),
            }

        return {
            "ok": True,
            "amostra": len(draws),
            "janela": window,
            "resultados": [
                evaluate("frequency"),
                evaluate("recent"),
            ],
            "observacao": (
                "Backtest histórico. Não representa garantia ou previsão "
                "do próximo concurso."
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "erro": "Não foi possível executar o backtest.",
            "detalhe": str(exc),
            "resultados": [],
        }


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "Lotofácil Monitor V5"}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")
