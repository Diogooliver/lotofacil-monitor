from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json
import time
import threading
import os


# ============================================================
# LOTOFÁCIL MONITOR — V25.10
# BACKEND
# ============================================================

APP_VERSION = "V25.10"

CAIXA_BASE = (
    "https://servicebus2.caixa.gov.br/"
    "portaldeloterias/api/lotofacil"
)

HISTORY_LIMIT = 120
CACHE_TTL = 300

cache = {}
cache_lock = threading.Lock()


# ============================================================
# FASTAPI
# ============================================================

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


# ============================================================
# HTTP CAIXA
# ============================================================

def caixa_get(url: str):
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                "AppleWebKit/605.1.15 "
                "Version/18.0 Mobile/15E148 Safari/604.1"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://loterias.caixa.gov.br/"
        }
    )

    with urlopen(req, timeout=15) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw)


# ============================================================
# NORMALIZA RESULTADO
# ============================================================

def normalize_result(data):

    if not isinstance(data, dict):
        return None

    numero = (
        data.get("numero")
        or data.get("concurso")
        or data.get("numeroConcurso")
    )

    dezenas = (
        data.get("listaDezenas")
        or data.get("dezenas")
        or data.get("dezenasSorteadasOrdemSorteio")
    )

    data_apuracao = (
        data.get("dataApuracao")
        or data.get("data")
        or ""
    )

    if numero is None or not dezenas:
        return None

    try:
        numero = int(numero)
    except Exception:
        return None

    try:
        dezenas = sorted(
            int(str(x).zfill(2))
            for x in dezenas
        )
    except Exception:
        return None

    dezenas = [
        x for x in dezenas
        if 1 <= x <= 25
    ]

    if len(dezenas) != 15:
        return None

    return {
        "concurso": numero,
        "data": data_apuracao,
        "dezenas": dezenas
    }


# ============================================================
# CACHE
# ============================================================

def cache_get(key):

    with cache_lock:
        item = cache.get(key)

        if not item:
            return None

        timestamp, value = item

        if time.time() - timestamp > CACHE_TTL:
            return None

        return value


def cache_set(key, value):

    with cache_lock:
        cache[key] = (
            time.time(),
            value
        )


# ============================================================
# ÚLTIMO CONCURSO
# ============================================================

def get_latest():

    key = "latest"

    cached = cache_get(key)

    if cached:
        return cached

    try:
        data = caixa_get(CAIXA_BASE)
        result = normalize_result(data)

        if not result:
            raise ValueError("Resultado inválido da CAIXA")

        cache_set(key, result)

        return result

    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Erro ao consultar CAIXA: {e}"
        )


# ============================================================
# CONCURSO INDIVIDUAL
# ============================================================

def get_contest(numero):

    key = f"contest:{numero}"

    cached = cache_get(key)

    if cached:
        return cached

    url = f"{CAIXA_BASE}/{numero}"

    try:
        data = caixa_get(url)
        result = normalize_result(data)

        if not result:
            raise ValueError(
                f"Dados inválidos para concurso {numero}"
            )

        cache_set(key, result)

        return result

    except Exception:
        return None


# ============================================================
# HISTÓRICO
# ============================================================

def get_history(limit=HISTORY_LIMIT):

    latest = get_latest()

    ultimo = latest["concurso"]

    limite = max(
        1,
        min(int(limit), HISTORY_LIMIT)
    )

    numeros = [
        ultimo - i
        for i in range(limite)
        if ultimo - i > 0
    ]

    resultados = []

    with ThreadPoolExecutor(max_workers=12) as executor:

        futures = {
            executor.submit(get_contest, n): n
            for n in numeros
        }

        for future in as_completed(futures):

            result = future.result()

            if result:
                resultados.append(result)

    resultados.sort(
        key=lambda x: x["concurso"]
    )

    return resultados


# ============================================================
# ROTAS
# ============================================================

@app.get("/")
def root():

    path = os.path.join(
        "static",
        "index.html"
    )

    if not os.path.exists(path):
        return JSONResponse(
            {
                "app": "Lotofácil Monitor",
                "version": APP_VERSION,
                "status": "online"
            }
        )

    return FileResponse(path)


@app.get("/health")
def health():

    return {
        "status": "online",
        "version": APP_VERSION,
        "timestamp": int(time.time())
    }


@app.get("/api/status")
def api_status():

    latest = get_latest()

    return {
        "status": "online",
        "version": APP_VERSION,
        "ultimo_concurso": latest["concurso"],
        "data": latest["data"]
    }


@app.get("/api/lotofacil/latest")
def api_latest():

    return get_latest()


@app.get("/api/lotofacil/history")
def api_history(limit: int = HISTORY_LIMIT):

    return {
        "version": APP_VERSION,
        "limit": limit,
        "data": get_history(limit)
    }


@app.get("/api/lotofacil/{concurso}")
def api_contest(concurso: int):

    result = get_contest(concurso)

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Concurso não encontrado"
        )

    return result


# ============================================================
# EXECUÇÃO LOCAL
# ============================================================

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
