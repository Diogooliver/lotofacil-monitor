from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import httpx
import asyncio
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional


# =========================================================
# CONFIGURAÇÃO
# =========================================================

APP_VERSION = "V21.3"

CAIXA_BASE = "https://servicebus2.caixa.gov.br/portaldeloterias/api/lotofacil"

# API alternativa
ALT_BASES = [
    "https://loteriascaixa-api.herokuapp.com/api/lotofacil",
    "https://loterias-gutotech.herokuapp.com/api/lotofacil",
    "https://loterias-caixa-gov.herokuapp.com/api/lotofacil",
]

HISTORY_LIMIT_DEFAULT = 30

CACHE_TTL_SECONDS = 300

_cache_latest = None
_cache_latest_time = 0

_cache_history = None
_cache_history_time = 0


# =========================================================
# APP
# =========================================================

app = FastAPI(
    title="Lotofácil Monitor",
    version=APP_VERSION
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# HELPERS
# =========================================================

def now_ts() -> float:
    return datetime.now().timestamp()


def normalize_number(value: Any) -> Optional[int]:
    """
    Converte valores como:
    1
    "01"
    "1"
    "Dezena 01"
    para inteiro.
    """
    if value is None:
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    text = str(value).strip()

    match = re.search(r"\d+", text)

    if not match:
        return None

    try:
        return int(match.group(0))
    except Exception:
        return None


def extract_contest(data: Dict[str, Any]) -> Optional[int]:

    possible_fields = [
        "numero",
        "numeroConcurso",
        "concurso",
        "Concurso",
        "numero_concurso",
    ]

    for field in possible_fields:

        if field in data:

            value = normalize_number(data.get(field))

            if value is not None:
                return value

    return None


def extract_date(data: Dict[str, Any]) -> Optional[str]:

    possible_fields = [
        "dataApuracao",
        "data_apuracao",
        "data",
        "Data",
        "date",
    ]

    for field in possible_fields:

        value = data.get(field)

        if value:
            return str(value)

    return None


def extract_numbers(data: Dict[str, Any]) -> List[int]:

    possible_fields = [
        "listaDezenas",
        "dezenas",
        "Dezenas",
        "dezenasOrdemSorteio",
        "numbers",
    ]

    raw = None

    for field in possible_fields:

        if field in data and data[field] is not None:
            raw = data[field]
            break

    if raw is None:
        return []

    # Caso seja lista
    if isinstance(raw, list):

        numbers = []

        for item in raw:

            # Pode ser número simples
            number = normalize_number(item)

            if number is not None and 1 <= number <= 25:
                numbers.append(number)

        return sorted(set(numbers))

    # Caso seja string
    if isinstance(raw, str):

        found = re.findall(r"\d{1,2}", raw)

        numbers = []

        for item in found:

            number = normalize_number(item)

            if number is not None and 1 <= number <= 25:
                numbers.append(number)

        return sorted(set(numbers))

    return []


def normalize_result(
    data: Dict[str, Any],
    source: str
) -> Dict[str, Any]:

    concurso = extract_contest(data)

    date = extract_date(data)

    numbers = extract_numbers(data)

    if len(numbers) != 15:

        raise ValueError(
            f"Resultado inválido: esperado 15 dezenas, recebido {len(numbers)}"
        )

    return {
        "concurso": concurso,
        "data": date,
        "dezenas": numbers,
        "source": source
    }


# =========================================================
# HTTP CLIENT
# =========================================================

async def http_get_json(
    client: httpx.AsyncClient,
    url: str
) -> Dict[str, Any]:

    response = await client.get(
        url,
        timeout=20.0,
        follow_redirects=True
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"HTTP {response.status_code}"
        )

    data = response.json()

    if not isinstance(data, dict):

        raise RuntimeError(
            "Resposta não é um objeto JSON"
        )

    return data


# =========================================================
# FONTE 1 — CAIXA
# =========================================================

async def fetch_caixa_latest(
    client: httpx.AsyncClient
) -> Dict[str, Any]:

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 "
            "Version/17.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://loterias.caixa.gov.br/",
        "Origin": "https://loterias.caixa.gov.br",
    }

    response = await client.get(
        CAIXA_BASE,
        headers=headers,
        timeout=20.0,
        follow_redirects=True
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"CAIXA HTTP {response.status_code}"
        )

    data = response.json()

    return normalize_result(
        data,
        "CAIXA"
    )


async def fetch_caixa_contest(
    client: httpx.AsyncClient,
    concurso: int
) -> Dict[str, Any]:

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 "
            "Version/17.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://loterias.caixa.gov.br/",
        "Origin": "https://loterias.caixa.gov.br",
    }

    url = f"{CAIXA_BASE}/{concurso}"

    response = await client.get(
        url,
        headers=headers,
        timeout=20.0,
        follow_redirects=True
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"CAIXA HTTP {response.status_code}"
        )

    data = response.json()

    return normalize_result(
        data,
        "CAIXA"
    )


# =========================================================
# FONTE 2 — API ALTERNATIVA
# =========================================================

async def fetch_alt_latest(
    client: httpx.AsyncClient
) -> Dict[str, Any]:

    last_error = None

    for base in ALT_BASES:

        try:

            url = f"{base}/latest"

            data = await http_get_json(
                client,
                url
            )

            result = normalize_result(
                data,
                "API_ALTERNATIVA"
            )

            return result

        except Exception as error:

            last_error = error

            continue

    raise RuntimeError(
        f"Nenhuma API alternativa respondeu: {last_error}"
    )


async def fetch_alt_contest(
    client: httpx.AsyncClient,
    concurso: int
) -> Dict[str, Any]:

    last_error = None

    for base in ALT_BASES:

        try:

            url = f"{base}/{concurso}"

            data = await http_get_json(
                client,
                url
            )

            result = normalize_result(
                data,
                "API_ALTERNATIVA"
            )

            return result

        except Exception as error:

            last_error = error

            continue

    raise RuntimeError(
        f"Não foi possível consultar concurso {concurso}: {last_error}"
    )


# =========================================================
# BUSCA INTELIGENTE
# =========================================================

async def fetch_latest_result() -> Dict[str, Any]:

    global _cache_latest
    global _cache_latest_time

    # Cache
    if (
        _cache_latest is not None
        and now_ts() - _cache_latest_time < CACHE_TTL_SECONDS
    ):
        return _cache_latest

    async with httpx.AsyncClient() as client:

        # -------------------------------------------------
        # 1. Tenta CAIXA
        # -------------------------------------------------

        try:

            result = await fetch_caixa_latest(
                client
            )

            _cache_latest = result
            _cache_latest_time = now_ts()

            return result

        except Exception as caixa_error:

            print(
                "[AVISO] CAIXA indisponível:",
                caixa_error
            )

        # -------------------------------------------------
        # 2. Fallback
        # -------------------------------------------------

        try:

            result = await fetch_alt_latest(
                client
            )

            _cache_latest = result
            _cache_latest_time = now_ts()

            return result

        except Exception as alt_error:

            print(
                "[ERRO] API alternativa:",
                alt_error
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "Não foi possível consultar o último "
                    "resultado nas fontes disponíveis."
                )
            )


async def fetch_result_by_contest(
    concurso: int
) -> Dict[str, Any]:

    async with httpx.AsyncClient() as client:

        # -------------------------------------------------
        # 1. CAIXA
        # -------------------------------------------------

        try:

            return await fetch_caixa_contest(
                client,
                concurso
            )

        except Exception as caixa_error:

            print(
                f"[AVISO] CAIXA concurso {concurso}:",
                caixa_error
            )

        # -------------------------------------------------
        # 2. FALLBACK
        # -------------------------------------------------

        try:

            return await fetch_alt_contest(
                client,
                concurso
            )

        except Exception as alt_error:

            print(
                f"[ERRO] Fallback concurso {concurso}:",
                alt_error
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    f"Não foi possível consultar "
                    f"o concurso {concurso}."
                )
            )


# =========================================================
# HISTÓRICO
# =========================================================

async def build_history(
    limit: int = HISTORY_LIMIT_DEFAULT
) -> List[Dict[str, Any]]:

    global _cache_history
    global _cache_history_time

    limit = max(
        1,
        min(
            int(limit),
            100
        )
    )

    # Cache
    if (
        _cache_history is not None
        and now_ts() - _cache_history_time < CACHE_TTL_SECONDS
        and len(_cache_history) >= min(limit, len(_cache_history))
    ):
        return _cache_history[:limit]

    # Primeiro descobre o concurso atual
    latest = await fetch_latest_result()

    latest_contest = latest.get("concurso")

    if latest_contest is None:

        raise HTTPException(
            status_code=502,
            detail="Não foi possível identificar o número do concurso."
        )

    history = [
        latest
    ]

    # Busca anteriores
    async with httpx.AsyncClient() as client:

        for i in range(
            1,
            limit
        ):

            contest_number = latest_contest - i

            if contest_number <= 0:
                break

            result = None

            # -------------------------------------------------
            # Primeiro tenta CAIXA
            # -------------------------------------------------

            try:

                result = await fetch_caixa_contest(
                    client,
                    contest_number
                )

            except Exception as caixa_error:

                print(
                    f"[AVISO] CAIXA {contest_number}:",
                    caixa_error
                )

            # -------------------------------------------------
            # Depois fallback
            # -------------------------------------------------

            if result is None:

                try:

                    result = await fetch_alt_contest(
                        client,
                        contest_number
                    )

                except Exception as alt_error:

                    print(
                        f"[AVISO] Fallback {contest_number}:",
                        alt_error
                    )

                    # Não interrompe todo o histórico
                    continue

            history.append(
                result
            )

            # Pequena pausa para evitar excesso de requisições
            await asyncio.sleep(
                0.12
            )

    # Remove duplicados
    unique = {}

    for item in history:

        concurso = item.get("concurso")

        if concurso is not None:

            unique[concurso] = item

    history = list(
        unique.values()
    )

    history.sort(
        key=lambda x: x.get("concurso", 0),
        reverse=True
    )

    _cache_history = history
    _cache_history_time = now_ts()

    return history[:limit]


# =========================================================
# ROTAS
# =========================================================

@app.get("/")
async def root():

    return FileResponse(
        "static/index.html"
    )


@app.get("/health")
async def health():

    return {
        "ok": True,
        "status": "online",
        "version": APP_VERSION
    }


@app.get("/api/test")
async def api_test():

    return {
        "ok": True,
        "message": "API do Lotofácil Monitor funcionando.",
        "version": APP_VERSION
    }


@app.get("/api/lotofacil/latest")
async def lotofacil_latest():

    result = await fetch_latest_result()

    return {
        "ok": True,
        "version": APP_VERSION,
        "fonte": result.get("source"),
        "concurso": result.get("concurso"),
        "data": result.get("data"),
        "dezenas": result.get("dezenas")
    }


@app.get("/api/lotofacil/{concurso}")
async def lotofacil_contest(
    concurso: int
):

    if concurso <= 0:

        raise HTTPException(
            status_code=400,
            detail="Número de concurso inválido."
        )

    result = await fetch_result_by_contest(
        concurso
    )

    return {
        "ok": True,
        "version": APP_VERSION,
        "fonte": result.get("source"),
        "concurso": result.get("concurso"),
        "data": result.get("data"),
        "dezenas": result.get("dezenas")
    }


@app.get("/api/lotofacil/history")
async def lotofacil_history(
    limit: int = HISTORY_LIMIT_DEFAULT
):

    history = await build_history(
        limit
    )

    return {
        "ok": True,
        "version": APP_VERSION,
        "total": len(history),
        "historico": history
    }


# Rota de compatibilidade
@app.get("/api/lotofacil")
async def lotofacil_api():

    return await lotofacil_latest()


# =========================================================
# STATIC
# =========================================================

if os.path.isdir("static"):

    app.mount(
        "/static",
        StaticFiles(directory="static"),
        name="static"
    )


# =========================================================
# EXECUÇÃO LOCAL
# =========================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            "8000"
        )
    )

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port
    )
