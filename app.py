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
# LOTOFÁCIL MONITOR — BACKEND V25.7
# ============================================================

APP_VERSION = "V25.7"

app = FastAPI(
    title="Lotofácil Monitor",
    version=APP_VERSION
)


# ============================================================
# FONTES
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

MAX_WORKERS = 12


# ============================================================
# CACHE
# ============================================================

_cache = {
    "latest": None,
    "latest_time": 0,

    "history": None,
    "history_time": 0,

    "history_by_limit": {},
    "history_by_limit_time": {}
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
    source="unknown",
    response_time=0
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
# NORMALIZA LISTA DE RESULTADOS
# ============================================================

def normalize_history(data, source="history"):

    if isinstance(data, dict):

        candidatos = (
            data.get("resultados")
            or data.get("data")
            or data.get("concursos")
            or data.get("history")
            or data.get("historico")
        )

        if isinstance(candidatos, list):

            data = candidatos

        else:

            data = [data]

    if not isinstance(data, list):

        raise RuntimeError(
            "Histórico inválido"
        )

    resultados = []

    for item in data:

        try:

            resultado = normalize_result(
                item,
                source=source,
                response_time=0
            )

            resultados.append(
                resultado
            )

        except Exception:

            continue

    resultados.sort(
        key=lambda x: x["concurso"],
        reverse=True
    )

    return resultados


# ============================================================
# BUSCAR ÚLTIMO
# ============================================================

def buscar_latest():

    agora = time.time()

    with _cache_lock:

        cached = _cache["latest"]

        cached_time = _cache["latest_time"]

        if (
            cached is not None
            and agora - cached_time < LATEST_CACHE_TTL
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
# HISTÓRICO — MÉTODO RÁPIDO
# ============================================================

def buscar_historico_rapido():

    # Primeiro tenta obter todos os concursos
    # através do endpoint base.

    try:

        started = time.time()

        data, elapsed = http_get_json(
            FALLBACK_BASE
        )

        resultados = normalize_history(
            data,
            "fallback-history"
        )

        if resultados:

            for item in resultados:

                item["_response_time"] = round(
                    time.time() - started,
                    2
                )

            return resultados

    except Exception:

        pass


    # --------------------------------------------------------
    # Segunda tentativa: CAIXA
    # --------------------------------------------------------

    try:

        data, elapsed = http_get_json(
            CAIXA_BASE
        )

        resultados = normalize_history(
            data,
            "caixa-history"
        )

        if resultados:

            return resultados

    except Exception:

        pass


    return []


# ============================================================
# BUSCAR CONCURSO INDIVIDUAL
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
# HISTÓRICO COMPLETO
# ============================================================

def buscar_historico(limit=120):

    limit = max(
        1,
        min(
            int(limit),
            MAX_HISTORY
        )
    )


    agora = time.time()


    # --------------------------------------------------------
    # CACHE POR LIMITE
    # --------------------------------------------------------

    with _cache_lock:

        cached = _cache[
            "history_by_limit"
        ].get(limit)

        cached_time = _cache[
            "history_by_limit_time"
        ].get(limit, 0)

        if (
            cached is not None
            and agora - cached_time < HISTORY_CACHE_TTL
        ):

            return cached


    # --------------------------------------------------------
    # CACHE GERAL
    # --------------------------------------------------------

    with _cache_lock:

        full_cache = _cache["history"]

        full_cache_time = _cache[
            "history_time"
        ]

        if (
            full_cache is not None
            and agora - full_cache_time
            < HISTORY_CACHE_TTL
        ):

            resultados = full_cache[
                "resultados"
            ][:limit]

            retorno = {
                "concursoAtual":
                    full_cache["concursoAtual"],

                "total":
                    len(resultados),

                "resultados":
                    resultados
            }

            _cache[
                "history_by_limit"
            ][limit] = retorno

            _cache[
                "history_by_limit_time"
            ][limit] = time.time()

            return retorno


    # --------------------------------------------------------
    # TENTATIVA RÁPIDA
    # --------------------------------------------------------

    resultados = buscar_historico_rapido()


    if resultados:

        resultados.sort(
            key=lambda x: x["concurso"],
            reverse=True
        )

        # Remove duplicados

        unicos = {}

        for item in resultados:

            unicos[
                item["concurso"]
            ] = item

        resultados = list(
            unicos.values()
        )

        resultados.sort(
            key=lambda x: x["concurso"],
            reverse=True
        )


        # Se encontrou pelo menos parte
        # suficiente do histórico

        if len(resultados) >= limit:

            resultados = resultados[:limit]

        else:

            # ------------------------------------------------
            # COMPLETA O QUE FALTOU
            # ------------------------------------------------

            latest = buscar_latest()

            atual = int(
                latest["concurso"]
            )

            existentes = {
                x["concurso"]
                for x in resultados
            }

            faltantes = []

            for concurso in range(
                atual,
                max(
                    0,
                    atual - limit
                ),
                -1
            ):

                if concurso not in existentes:

                    faltantes.append(
                        concurso
                    )

                if (
                    len(resultados)
                    + len(faltantes)
                    >= limit
                ):

                    break


            if faltantes:

                with ThreadPoolExecutor(
                    max_workers=MAX_WORKERS
                ) as executor:

                    futures = {
                        executor.submit(
                            buscar_concurso,
                            concurso
                        ): concurso

                        for concurso
                        in faltantes
                    }

                    for future in as_completed(
                        futures
                    ):

                        try:

                            resultados.append(
                                future.result()
                            )

                        except Exception:

                            continue


        resultados.sort(
            key=lambda x: x["concurso"],
            reverse=True
        )

        resultados = resultados[:limit]


    else:

        # ----------------------------------------------------
        # FALLBACK FINAL
        # ----------------------------------------------------

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

        with ThreadPoolExecutor(
            max_workers=MAX_WORKERS
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

                    resultados.append(
                        future.result()
                    )

                except Exception:

                    continue

        resultados.sort(
            key=lambda x: x["concurso"],
            reverse=True
        )

        resultados = resultados[:limit]


    # --------------------------------------------------------
    # CONCURSO ATUAL
    # --------------------------------------------------------

    if resultados:

        concurso_atual = max(
            x["concurso"]
            for x in resultados
        )

    else:

        concurso_atual = int(
            buscar_latest()["concurso"]
        )


    retorno_completo = {

        "concursoAtual":
            concurso_atual,

        "total":
            len(resultados),

        "resultados":
            resultados
    }


    # --------------------------------------------------------
    # SALVA CACHE
    # --------------------------------------------------------

    with _cache_lock:

        _cache["history"] = (
            retorno_completo
        )

        _cache["history_time"] = (
            time.time()
        )


        retorno = {
            "concursoAtual":
                concurso_atual,

            "total":
                len(resultados),

            "resultados":
                resultados[:limit]
        }


        _cache[
            "history_by_limit"
        ][limit] = retorno

        _cache[
            "history_by_limit_time"
        ][limit] = time.time()


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
                latest.get("_source"),

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
# LATEST
# ============================================================

@app.get("/api/lotofacil/latest")
def lotofacil_latest():

    try:

        return buscar_latest()

    except Exception as e:

        raise HTTPException(

            status_code=503,

            detail=str(e)

        )


# ============================================================
# HISTORY
# ============================================================

@app.get("/api/lotofacil/history")
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
# CONCURSO INDIVIDUAL
# ============================================================

@app.get("/api/lotofacil/{concurso}")
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
# FRONTEND
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
