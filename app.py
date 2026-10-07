from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json
import time
import threading
import os


# ============================================================
# LOTOFÁCIL MONITOR
# BACKEND V25.6
# ============================================================

APP_VERSION = "V25.6"

app = FastAPI(
    title="Lotofácil Monitor",
    version=APP_VERSION
)


# ============================================================
# APIS
# ============================================================

CAIXA_BASE = (
    "https://servicebus2.caixa.gov.br/"
    "portaldeloterias/api/lotofacil"
)

FALLBACK_BASE = (
    "https://loteriascaixa-api.herokuapp.com/"
    "api/lotofacil"
)


# ============================================================
# CONFIGURAÇÕES
# ============================================================

REQUEST_TIMEOUT = 8

LATEST_CACHE_TTL = 60

HISTORY_CACHE_TTL = 21600

MAX_HISTORY = 120


# ============================================================
# CACHE
# ============================================================

_cache = {
    "latest": None,
    "latest_time": 0,

    "history": {},
    "history_time": {}
}

_cache_lock = threading.Lock()


# ============================================================
# HTTP
# ============================================================

def http_get_json(url: str):

    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) "
                "Version/17.0 Mobile/15E148 Safari/604.1"
            ),
            "Accept": "application/json,text/plain,*/*"
        }
    )

    started = time.time()

    try:

        with urlopen(
            req,
            timeout=REQUEST_TIMEOUT
        ) as response:

            raw = response.read()

            elapsed = round(
                time.time() - started,
                2
            )

            data = json.loads(
                raw.decode("utf-8")
            )

            return data, elapsed

    except HTTPError as e:

        raise RuntimeError(
            f"HTTP {e.code}"
        )

    except URLError as e:

        raise RuntimeError(
            f"URL error: {e.reason}"
        )

    except Exception as e:

        raise RuntimeError(
            str(e)
        )


# ============================================================
# NORMALIZAÇÃO
# ============================================================

def normalize_result(
    data,
    source,
    response_time
):

    if not isinstance(data, dict):

        raise RuntimeError(
            "Resposta inválida da API"
        )

    concurso = (
        data.get("concurso")
        or data.get("numero")
        or data.get("numeroConcurso")
    )

    data_apuracao = (
        data.get("dataApuracao")
        or data.get("data")
        or ""
    )

    dezenas = (
        data.get("dezenas")
        or data.get("listaDezenas")
        or data.get("resultado")
        or data.get("dezenasSorteadas")
        or []
    )

    if not isinstance(dezenas, list):

        raise RuntimeError(
            "Lista de dezenas inválida"
        )

    dezenas = [
        str(x).zfill(2)
        for x in dezenas
    ]

    dezenas = sorted(
        set(dezenas),
        key=lambda x: int(x)
    )

    if not concurso:

        raise RuntimeError(
            "Concurso não encontrado"
        )

    if len(dezenas) != 15:

        raise RuntimeError(
            f"Resultado possui {len(dezenas)} dezenas"
        )

    return {
        "concurso": int(concurso),
        "numero": int(concurso),

        "data": data_apuracao,
        "dataApuracao": data_apuracao,

        "dezenas": dezenas,
        "listaDezenas": dezenas,
        "resultado": dezenas,
        "dezenasSorteadas": dezenas,

        "_source": source,
        "_response_time": response_time
    }


# ============================================================
# BUSCAR CONCURSO ESPECÍFICO
# ============================================================

def buscar_concurso(concurso):

    erros = []


    # --------------------------------------------------------
    # FALLBACK
    # --------------------------------------------------------

    fallback_url = (
        f"{FALLBACK_BASE}/{concurso}"
    )

    try:

        data, elapsed = http_get_json(
            fallback_url
        )

        return normalize_result(
            data,
            "fallback",
            elapsed
        )

    except Exception as e:

        erros.append(
            f"fallback: {e}"
        )


    # --------------------------------------------------------
    # CAIXA
    # --------------------------------------------------------

    caixa_url = (
        f"{CAIXA_BASE}/{concurso}"
    )

    try:

        data, elapsed = http_get_json(
            caixa_url
        )

        return normalize_result(
            data,
            "caixa",
            elapsed
        )

    except Exception as e:

        erros.append(
            f"caixa: {e}"
        )


    raise RuntimeError(
        " | ".join(erros)
    )


# ============================================================
# ÚLTIMO CONCURSO
# ============================================================

def buscar_latest():

    agora = time.time()


    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    with _cache_lock:

        cached = _cache["latest"]

        cached_time = _cache["latest_time"]

        if (
            cached is not None
            and agora - cached_time
            < LATEST_CACHE_TTL
        ):

            return cached


    erros = []


    # --------------------------------------------------------
    # FALLBACK
    # --------------------------------------------------------

    try:

        data, elapsed = http_get_json(
            f"{FALLBACK_BASE}/latest"
        )

        resultado = normalize_result(
            data,
            "fallback",
            elapsed
        )

        with _cache_lock:

            _cache["latest"] = resultado

            _cache["latest_time"] = time.time()

        return resultado

    except Exception as e:

        erros.append(
            f"fallback: {e}"
        )


    # --------------------------------------------------------
    # CAIXA
    # --------------------------------------------------------

    try:

        data, elapsed = http_get_json(
            CAIXA_BASE
        )

        resultado = normalize_result(
            data,
            "caixa",
            elapsed
        )

        with _cache_lock:

            _cache["latest"] = resultado

            _cache["latest_time"] = time.time()

        return resultado

    except Exception as e:

        erros.append(
            f"caixa: {e}"
        )


    # --------------------------------------------------------
    # CACHE ANTIGO
    # --------------------------------------------------------

    with _cache_lock:

        if _cache["latest"] is not None:

            return _cache["latest"]


    raise RuntimeError(
        " | ".join(erros)
    )


# ============================================================
# HISTÓRICO
# ============================================================

def buscar_historico(limit=120):

    limit = max(
        1,
        min(int(limit), MAX_HISTORY)
    )

    agora = time.time()


    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    with _cache_lock:

        cached = _cache["history"].get(
            limit
        )

        cached_time = _cache[
            "history_time"
        ].get(
            limit,
            0
        )

        if (
            cached is not None
            and agora - cached_time
            < HISTORY_CACHE_TTL
        ):

            return cached


    # --------------------------------------------------------
    # DESCOBRIR CONCURSO ATUAL
    # --------------------------------------------------------

    latest = buscar_latest()

    atual = int(
        latest["concurso"]
    )


    concursos = list(
        range(
            atual,
            max(
                0,
                atual - limit
            ),
            -1
        )
    )


    resultados = []


    # --------------------------------------------------------
    # BUSCA PARALELA
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=5
    ) as executor:

        futures = {

            executor.submit(
                buscar_concurso,
                concurso
            ): concurso

            for concurso in concursos

        }


        for future in as_completed(
            futures
        ):

            try:

                resultado = (
                    future.result()
                )

                resultados.append(
                    resultado
                )

            except Exception:

                continue


    # --------------------------------------------------------
    # ORDENAR
    # --------------------------------------------------------

    resultados.sort(
        key=lambda x: x["concurso"],
        reverse=True
    )


    resultados = resultados[:limit]


    retorno = {

        "concursoAtual": atual,

        "total": len(
            resultados
        ),

        "resultados": resultados

    }


    # --------------------------------------------------------
    # SALVAR CACHE
    # --------------------------------------------------------

    with _cache_lock:

        _cache["history"][limit] = (
            retorno
        )

        _cache["history_time"][limit] = (
            time.time()
        )


    return retorno


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {

        "status": "online",

        "app":
            "Lotofácil Monitor",

        "version":
            APP_VERSION,

        "backend":
            "FastAPI",

        "message":
            "Backend funcionando"

    }


# ============================================================
# STATUS
# ============================================================

@app.get("/api/status")
def api_status():

    try:

        latest = buscar_latest()

        return {

            "status":
                "online",

            "version":
                APP_VERSION,

            "ultimoConcurso":
                latest["concurso"],

            "source":
                latest.get(
                    "_source"
                ),

            "response_time":
                latest.get(
                    "_response_time"
                )

        }

    except Exception as e:

        return JSONResponse(

            status_code=503,

            content={

                "status":
                    "offline",

                "version":
                    APP_VERSION,

                "error":
                    str(e)

            }

        )


# ============================================================
# ÚLTIMO RESULTADO
# ============================================================

@app.get(
    "/api/lotofacil/latest"
)
def lotofacil_latest():

    try:

        return buscar_latest()

    except Exception as e:

        raise HTTPException(

            status_code=503,

            detail=str(e)

        )


# ============================================================
# HISTÓRICO
#
# IMPORTANTE:
# ESTA ROTA VEM ANTES DE /{concurso}
# ============================================================

@app.get(
    "/api/lotofacil/history"
)
def lotofacil_history(
    limit: int = 120
):

    try:

        return buscar_historico(
            limit
        )

    except Exception as e:

        raise HTTPException(

            status_code=503,

            detail=str(e)

        )


# ============================================================
# CONCURSO ESPECÍFICO
# ============================================================

@app.get(
    "/api/lotofacil/{concurso}"
)
def lotofacil_concurso(
    concurso: int
):

    if concurso <= 0:

        raise HTTPException(

            status_code=400,

            detail=
                "Número de concurso inválido"

        )


    try:

        return buscar_concurso(
            concurso
        )

    except Exception as e:

        raise HTTPException(

            status_code=404,

            detail=str(e)

        )


# ============================================================
# PÁGINA PRINCIPAL
#
# ABRE O FRONTEND V25.7
# ============================================================

@app.get("/")
def root():

    frontend = os.path.join(
        "static",
        "index.html"
    )


    if not os.path.exists(
        frontend
    ):

        raise HTTPException(

            status_code=404,

            detail=
                "static/index.html não encontrado"

        )


    return FileResponse(
        frontend,
        media_type="text/html"
    )


# ============================================================
# FAVICON
# ============================================================

@app.get("/favicon.ico")
def favicon():

    return JSONResponse(
        content={}
    )
