from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import httpx
import asyncio
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional


# =========================================================
# LOTOFÁCIL MONITOR — API V23.1
# =========================================================

APP_VERSION = "V23.1"

CAIXA_BASE = (
    "https://servicebus2.caixa.gov.br/"
    "portaldeloterias/api/lotofacil"
)

# APIs alternativas
ALT_BASES = [
    "https://loteriascaixa-api.herokuapp.com/api/lotofacil",
    "https://loterias-gutotech.herokuapp.com/api/lotofacil",
    "https://loterias-caixa-gov.herokuapp.com/api/lotofacil",
]

DEFAULT_HISTORY_LIMIT = 60
MAX_HISTORY_LIMIT = 100

CACHE_TTL_SECONDS = 300

REQUEST_TIMEOUT = 20.0

# =========================================================
# CACHE
# =========================================================

_cache_latest: Optional[Dict[str, Any]] = None
_cache_latest_time: float = 0

_cache_history: Optional[List[Dict[str, Any]]] = None
_cache_history_time: float = 0


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="Lotofácil Monitor V23",
    version=APP_VERSION,
    description=(
        "API intermediária do Lotofácil Monitor V23. "
        "Consulta resultados da Lotofácil e fornece "
        "dados normalizados para o aplicativo."
    )
)


# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# FUNÇÕES AUXILIARES
# =========================================================

def now_ts() -> float:
    return time.time()


def normalize_number(
    value: Any
) -> Optional[int]:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    text = str(value).strip()

    match = re.search(
        r"\d+",
        text
    )

    if not match:
        return None

    try:
        return int(match.group(0))
    except Exception:
        return None


def extract_contest(
    data: Dict[str, Any]
) -> Optional[int]:

    fields = [
        "numero",
        "numeroConcurso",
        "concurso",
        "Concurso",
        "numero_concurso",
    ]

    for field in fields:

        if field not in data:
            continue

        value = normalize_number(
            data.get(field)
        )

        if value is not None:
            return value

    return None


def extract_date(
    data: Dict[str, Any]
) -> Optional[str]:

    fields = [
        "dataApuracao",
        "data_apuracao",
        "data",
        "Data",
        "date",
    ]

    for field in fields:

        value = data.get(field)

        if value:
            return str(value)

    return None


def extract_numbers(
    data: Dict[str, Any]
) -> List[int]:

    fields = [
        "listaDezenas",
        "dezenas",
        "Dezenas",
        "dezenasOrdemSorteio",
        "dezenasSorteadas",
        "numbers",
    ]

    raw = None

    for field in fields:

        if field not in data:
            continue

        if data[field] is None:
            continue

        raw = data[field]
        break

    if raw is None:
        return []

    numbers: List[int] = []

    # -----------------------------------------------------
    # LISTA
    # -----------------------------------------------------

    if isinstance(raw, list):

        for item in raw:

            number = normalize_number(item)

            if (
                number is not None
                and 1 <= number <= 25
            ):
                numbers.append(number)

    # -----------------------------------------------------
    # STRING
    # -----------------------------------------------------

    elif isinstance(raw, str):

        found = re.findall(
            r"\d{1,2}",
            raw
        )

        for item in found:

            number = normalize_number(item)

            if (
                number is not None
                and 1 <= number <= 25
            ):
                numbers.append(number)

    # -----------------------------------------------------
    # NORMALIZA
    # -----------------------------------------------------

    return sorted(
        set(numbers)
    )


def normalize_result(
    data: Dict[str, Any],
    source: str
) -> Dict[str, Any]:

    if not isinstance(data, dict):
        raise ValueError(
            "Resposta inválida."
        )

    concurso = extract_contest(data)
    data_sorteio = extract_date(data)
    dezenas = extract_numbers(data)

    if concurso is None:
        raise ValueError(
            "Número do concurso não encontrado."
        )

    if len(dezenas) != 15:
        raise ValueError(
            "Resultado inválido: "
            f"esperadas 15 dezenas, "
            f"recebidas {len(dezenas)}."
        )

    return {
        "concurso": concurso,
        "data": data_sorteio or "",
        "dezenas": dezenas,
        "source": source,
    }


# =========================================================
# HEADERS
# =========================================================

def caixa_headers() -> Dict[str, str]:

    return {
        "User-Agent": (
            "Mozilla/5.0 "
            "(iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) "
            "Version/17.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": (
            "application/json,"
            "text/plain,"
            "*/*"
        ),
        "Referer": (
            "https://loterias.caixa.gov.br/"
        ),
        "Origin": (
            "https://loterias.caixa.gov.br"
        ),
    }


# =========================================================
# HTTP JSON
# =========================================================

async def http_get_json(
    client: httpx.AsyncClient,
    url: str,
    headers: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:

    response = await client.get(
        url,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"HTTP {response.status_code}"
        )

    try:

        data = response.json()

    except Exception as error:

        raise RuntimeError(
            f"JSON inválido: {error}"
        )

    if not isinstance(data, dict):

        raise RuntimeError(
            "Resposta não é um objeto JSON."
        )

    return data


# =========================================================
# CAIXA — ÚLTIMO RESULTADO
# =========================================================

async def fetch_caixa_latest(
    client: httpx.AsyncClient
) -> Dict[str, Any]:

    data = await http_get_json(
        client,
        CAIXA_BASE,
        caixa_headers()
    )

    return normalize_result(
        data,
        "CAIXA"
    )


# =========================================================
# CAIXA — CONCURSO
# =========================================================

async def fetch_caixa_contest(
    client: httpx.AsyncClient,
    concurso: int
) -> Dict[str, Any]:

    url = (
        f"{CAIXA_BASE}/"
        f"{concurso}"
    )

    data = await http_get_json(
        client,
        url,
        caixa_headers()
    )

    return normalize_result(
        data,
        "CAIXA"
    )


# =========================================================
# API ALTERNATIVA — ÚLTIMO
# =========================================================

async def fetch_alt_latest(
    client: httpx.AsyncClient
) -> Dict[str, Any]:

    last_error = None

    for base in ALT_BASES:

        try:

            url = (
                f"{base}/latest"
            )

            data = await http_get_json(
                client,
                url
            )

            return normalize_result(
                data,
                "API_ALTERNATIVA"
            )

        except Exception as error:

            last_error = error

            print(
                "[AVISO] API alternativa:",
                base,
                error
            )

    raise RuntimeError(
        "Nenhuma API alternativa respondeu. "
        f"Último erro: {last_error}"
    )


# =========================================================
# API ALTERNATIVA — CONCURSO
# =========================================================

async def fetch_alt_contest(
    client: httpx.AsyncClient,
    concurso: int
) -> Dict[str, Any]:

    last_error = None

    for base in ALT_BASES:

        try:

            url = (
                f"{base}/"
                f"{concurso}"
            )

            data = await http_get_json(
                client,
                url
            )

            return normalize_result(
                data,
                "API_ALTERNATIVA"
            )

        except Exception as error:

            last_error = error

            print(
                "[AVISO] API alternativa:",
                base,
                concurso,
                error
            )

    raise RuntimeError(
        "Não foi possível consultar "
        f"o concurso {concurso}. "
        f"Último erro: {last_error}"
    )


# =========================================================
# BUSCA INTELIGENTE — ÚLTIMO
# =========================================================

async def fetch_latest_result() -> Dict[str, Any]:

    global _cache_latest
    global _cache_latest_time

    # -----------------------------------------------------
    # CACHE
    # -----------------------------------------------------

    if (
        _cache_latest is not None
        and (
            now_ts()
            - _cache_latest_time
            < CACHE_TTL_SECONDS
        )
    ):

        return _cache_latest

    # -----------------------------------------------------
    # CONSULTA
    # -----------------------------------------------------

    async with httpx.AsyncClient() as client:

        # 1 — CAIXA

        try:

            result = await fetch_caixa_latest(
                client
            )

            _cache_latest = result
            _cache_latest_time = now_ts()

            print(
                "[OK] Último resultado obtido da CAIXA:",
                result["concurso"]
            )

            return result

        except Exception as error:

            print(
                "[AVISO] CAIXA indisponível:",
                error
            )

        # 2 — ALTERNATIVAS

        try:

            result = await fetch_alt_latest(
                client
            )

            _cache_latest = result
            _cache_latest_time = now_ts()

            print(
                "[OK] Último resultado obtido por fallback:",
                result["concurso"]
            )

            return result

        except Exception as error:

            print(
                "[ERRO] Todas as fontes falharam:",
                error
            )

            raise HTTPException(
                status_code=502,
                detail=(
                    "Não foi possível consultar "
                    "o último resultado da Lotofácil."
                )
            )


# =========================================================
# BUSCA INTELIGENTE — CONCURSO
# =========================================================

async def fetch_result_by_contest(
    concurso: int
) -> Dict[str, Any]:

    if concurso <= 0:

        raise HTTPException(
            status_code=400,
            detail="Número de concurso inválido."
        )

    async with httpx.AsyncClient() as client:

        # -------------------------------------------------
        # 1 — CAIXA
        # -------------------------------------------------

        try:

            return await fetch_caixa_contest(
                client,
                concurso
            )

        except Exception as error:

            print(
                f"[AVISO] CAIXA concurso "
                f"{concurso}:",
                error
            )

        # -------------------------------------------------
        # 2 — FALLBACK
        # -------------------------------------------------

        try:

            return await fetch_alt_contest(
                client,
                concurso
            )

        except Exception as error:

            print(
                f"[ERRO] Fallback concurso "
                f"{concurso}:",
                error
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
    limit: int = DEFAULT_HISTORY_LIMIT
) -> List[Dict[str, Any]]:

    global _cache_history
    global _cache_history_time

    # -----------------------------------------------------
    # LIMITAÇÃO
    # -----------------------------------------------------

    try:
        limit = int(limit)
    except Exception:
        limit = DEFAULT_HISTORY_LIMIT

    limit = max(
        1,
        min(
            limit,
            MAX_HISTORY_LIMIT
        )
    )

    # -----------------------------------------------------
    # CACHE
    # -----------------------------------------------------

    if (
        _cache_history is not None
        and (
            now_ts()
            - _cache_history_time
            < CACHE_TTL_SECONDS
        )
    ):

        return _cache_history[:limit]

    # -----------------------------------------------------
    # ÚLTIMO
    # -----------------------------------------------------

    latest = await fetch_latest_result()

    latest_contest = latest.get(
        "concurso"
    )

    if latest_contest is None:

        raise HTTPException(
            status_code=502,
            detail=(
                "Não foi possível "
                "identificar o último concurso."
            )
        )

    history: List[Dict[str, Any]] = [
        latest
    ]

    # -----------------------------------------------------
    # BUSCAR ANTERIORES
    # -----------------------------------------------------

    async with httpx.AsyncClient() as client:

        for i in range(
            1,
            limit
        ):

            contest_number = (
                latest_contest - i
            )

            if contest_number <= 0:
                break

            result = None

            # ---------------------------------------------
            # CAIXA
            # ---------------------------------------------

            try:

                result = (
                    await fetch_caixa_contest(
                        client,
                        contest_number
                    )
                )

            except Exception as error:

                print(
                    f"[AVISO] CAIXA "
                    f"{contest_number}:",
                    error
                )

            # ---------------------------------------------
            # FALLBACK
            # ---------------------------------------------

            if result is None:

                try:

                    result = (
                        await fetch_alt_contest(
                            client,
                            contest_number
                        )
                    )

                except Exception as error:

                    print(
                        f"[AVISO] Fallback "
                        f"{contest_number}:",
                        error
                    )

            # ---------------------------------------------
            # ADICIONA
            # ---------------------------------------------

            if result is not None:

                history.append(
                    result
                )

            # Pequena pausa para evitar
            # sobrecarregar a fonte

            await asyncio.sleep(
                0.10
            )

    # -----------------------------------------------------
    # REMOVE DUPLICADOS
    # -----------------------------------------------------

    unique: Dict[
        int,
        Dict[str, Any]
    ] = {}

    for item in history:

        concurso = item.get(
            "concurso"
        )

        if concurso is not None:

            unique[
                int(concurso)
            ] = item

    history = list(
        unique.values()
    )

    # -----------------------------------------------------
    # ORDEM
    # -----------------------------------------------------

    history.sort(
        key=lambda x: int(
            x.get(
                "concurso",
                0
            )
        ),
        reverse=True
    )

    history = history[
        :MAX_HISTORY_LIMIT
    ]

    # -----------------------------------------------------
    # CACHE
    # -----------------------------------------------------

    _cache_history = history
    _cache_history_time = now_ts()

    print(
        f"[OK] Histórico atualizado: "
        f"{len(history)} concursos."
    )

    return history[:limit]


# =========================================================
# INVALIDAR CACHE
# =========================================================

def clear_cache():

    global _cache_latest
    global _cache_latest_time
    global _cache_history
    global _cache_history_time

    _cache_latest = None
    _cache_latest_time = 0

    _cache_history = None
    _cache_history_time = 0


# =========================================================
# ROTAS
# =========================================================

@app.get("/")
async def root():

    path = "static/index.html"

    if not os.path.isfile(path):

        return {
            "ok": True,
            "message": (
                "API Lotofácil Monitor "
                "online."
            ),
            "version": APP_VERSION,
        }

    return FileResponse(
        path
    )


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
async def health():

    return {
        "ok": True,
        "status": "online",
        "service": "lotofacil-monitor",
        "version": APP_VERSION,
        "timestamp": datetime.now().isoformat(),
    }


# =========================================================
# TESTE
# =========================================================

@app.get("/api/test")
async def api_test():

    return {
        "ok": True,
        "message": (
            "API do Lotofácil Monitor "
            "V23 funcionando."
        ),
        "version": APP_VERSION,
    }


# =========================================================
# ÚLTIMO RESULTADO
# =========================================================

@app.get(
    "/api/lotofacil/latest"
)
async def lotofacil_latest():

    result = (
        await fetch_latest_result()
    )

    return {
        "ok": True,
        "version": APP_VERSION,
        "fonte": result.get(
            "source"
        ),
        "concurso": result.get(
            "concurso"
        ),
        "data": result.get(
            "data"
        ),
        "dezenas": result.get(
            "dezenas"
        ),
    }


# =========================================================
# HISTÓRICO
#
# IMPORTANTE:
# ESTA ROTA VEM ANTES DA ROTA
# /api/lotofacil/{concurso}
# =========================================================

@app.get(
    "/api/lotofacil/history"
)
async def lotofacil_history(
    limit: int = DEFAULT_HISTORY_LIMIT
):

    history = await build_history(
        limit
    )

    return {
        "ok": True,
        "version": APP_VERSION,
        "total": len(history),
        "historico": history,
    }


# =========================================================
# CONCURSO ESPECÍFICO
# =========================================================

@app.get(
    "/api/lotofacil/{concurso}"
)
async def lotofacil_contest(
    concurso: int
):

    result = (
        await fetch_result_by_contest(
            concurso
        )
    )

    return {
        "ok": True,
        "version": APP_VERSION,
        "fonte": result.get(
            "source"
        ),
        "concurso": result.get(
            "concurso"
        ),
        "data": result.get(
            "data"
        ),
        "dezenas": result.get(
            "dezenas"
        ),
    }


# =========================================================
# ROTA DE COMPATIBILIDADE
# =========================================================

@app.get(
    "/api/lotofacil"
)
async def lotofacil_api():

    return await lotofacil_latest()


# =========================================================
# LIMPAR CACHE
# =========================================================

@app.post(
    "/api/cache/clear"
)
async def api_clear_cache():

    clear_cache()

    return {
        "ok": True,
        "message": "Cache limpo.",
        "version": APP_VERSION,
    }


# =========================================================
# ARQUIVOS ESTÁTICOS
# =========================================================

if os.path.isdir("static"):

    app.mount(
        "/static",
        StaticFiles(
            directory="static"
        ),
        name="static",
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
        port=port,
    )
