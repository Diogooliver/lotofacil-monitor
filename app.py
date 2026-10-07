import os
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse


# ============================================================
# LOTOFÁCIL MONITOR — BACKEND V25.6
# ============================================================

APP_VERSION = "V25.6"

# API oficial/interna da CAIXA
CAIXA_API = (
    "https://servicebus2.caixa.gov.br/"
    "portaldeloterias/api/lotofacil"
)

# API alternativa
FALLBACK_API = (
    "https://loteriascaixa-api.herokuapp.com/"
    "api/lotofacil"
)

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

STATIC_DIR = os.path.join(
    BASE_DIR,
    "static"
)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Lotofácil Monitor",
    version=APP_VERSION,
    description="Backend do Lotofácil Monitor"
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# CACHE
# ============================================================

latest_cache = {
    "data": None,
    "timestamp": 0
}

history_cache = {}

CACHE_LATEST_SECONDS = 60

CACHE_HISTORY_SECONDS = (
    60 * 60 * 6
)


# ============================================================
# CONFIGURAÇÃO HTTP
# ============================================================

USER_AGENT = (
    "Mozilla/5.0 "
    "(Linux; Android 10; K) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/120.0 Mobile Safari/537.36"
)


# ============================================================
# REQUISIÇÃO JSON
# ============================================================

def request_json(url, timeout=5):

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Connection": "close"
    }

    request = urllib.request.Request(
        url,
        headers=headers,
        method="GET"
    )

    inicio = time.time()

    try:

        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            status = response.status

            body = response.read().decode(
                "utf-8"
            )

            tempo = round(
                time.time() - inicio,
                2
            )

            if status != 200:

                raise RuntimeError(
                    f"HTTP {status}"
                )

            data = json.loads(body)

            return data, tempo

    except Exception as e:

        raise RuntimeError(
            f"{type(e).__name__}: {str(e)}"
        )


# ============================================================
# NORMALIZAÇÃO DOS RESULTADOS
# ============================================================

def normalize_result(raw):

    if not isinstance(raw, dict):

        raise ValueError(
            "Resposta inválida da API"
        )

    concurso = (
        raw.get("concurso")
        or raw.get("numero")
        or raw.get("numeroConcurso")
    )

    data = (
        raw.get("data")
        or raw.get("dataApuracao")
        or raw.get("date")
    )

    dezenas = (
        raw.get("listaDezenas")
        or raw.get("dezenas")
        or raw.get("resultado")
        or raw.get("dezenasSorteadas")
        or raw.get(
            "dezenasSorteadasOrdemSorteio"
        )
    )

    # --------------------------------------------------------
    # Caso a API retorne string
    # --------------------------------------------------------

    if isinstance(dezenas, str):

        dezenas = (
            dezenas
            .replace("[", "")
            .replace("]", "")
            .replace('"', "")
            .replace("'", "")
            .split(",")
        )

    if not isinstance(dezenas, list):

        dezenas = []

    dezenas_normalizadas = []

    for numero in dezenas:

        try:

            numero = int(
                str(numero).strip()
            )

            if 1 <= numero <= 25:

                dezenas_normalizadas.append(
                    numero
                )

        except Exception:

            pass

    # Remove duplicados
    dezenas_normalizadas = sorted(
        list(
            set(
                dezenas_normalizadas
            )
        )
    )

    if not concurso:

        raise ValueError(
            "Concurso não encontrado"
        )

    if len(dezenas_normalizadas) < 15:

        raise ValueError(
            "Resultado incompleto: "
            f"{len(dezenas_normalizadas)} dezenas"
        )

    dezenas_formatadas = [
        f"{n:02d}"
        for n in dezenas_normalizadas
    ]

    return {

        "concurso": int(concurso),

        "numero": int(concurso),

        "data": data,

        "dataApuracao": data,

        "dezenas": dezenas_formatadas,

        "listaDezenas": dezenas_formatadas,

        "resultado": dezenas_formatadas,

        "dezenasSorteadas": dezenas_formatadas

    }


# ============================================================
# BUSCAR ÚLTIMO RESULTADO
# ============================================================

def fetch_latest():

    erros = []

    # --------------------------------------------------------
    # 1 — API ALTERNATIVA
    # --------------------------------------------------------

    try:

        raw, tempo = request_json(
            FALLBACK_API + "/latest",
            timeout=5
        )

        resultado = normalize_result(
            raw
        )

        resultado["_source"] = "fallback"

        resultado["_response_time"] = tempo

        return resultado

    except Exception as e:

        erros.append(
            f"fallback: {str(e)}"
        )

    # --------------------------------------------------------
    # 2 — CAIXA
    # --------------------------------------------------------

    try:

        raw, tempo = request_json(
            CAIXA_API,
            timeout=4
        )

        resultado = normalize_result(
            raw
        )

        resultado["_source"] = "caixa"

        resultado["_response_time"] = tempo

        return resultado

    except Exception as e:

        erros.append(
            f"caixa: {str(e)}"
        )

    raise RuntimeError(
        "Nenhuma fonte respondeu. "
        + " | ".join(erros)
    )


# ============================================================
# BUSCAR CONCURSO ESPECÍFICO
# ============================================================

def fetch_contest(concurso):

    erros = []

    fontes = [

        (
            "fallback",
            f"{FALLBACK_API}/{concurso}",
            5
        ),

        (
            "caixa",
            f"{CAIXA_API}/{concurso}",
            4
        )

    ]

    for fonte, url, timeout in fontes:

        try:

            raw, tempo = request_json(
                url,
                timeout=timeout
            )

            resultado = normalize_result(
                raw
            )

            if (
                resultado["concurso"]
                != int(concurso)
            ):

                raise ValueError(
                    "Concurso retornado "
                    "diferente do solicitado"
                )

            resultado["_source"] = fonte

            resultado["_response_time"] = tempo

            return resultado

        except Exception as e:

            erros.append(
                f"{fonte}: {str(e)}"
            )

    raise RuntimeError(
        f"Concurso {concurso} indisponível. "
        + " | ".join(erros)
    )


# ============================================================
# TESTE DO SERVIDOR
# ============================================================

@app.get("/health")
def health():

    return {

        "status": "online",

        "app": "Lotofácil Monitor",

        "version": APP_VERSION,

        "backend": "FastAPI",

        "message": "Backend funcionando"

    }


# ============================================================
# STATUS DA API
# ============================================================

@app.get("/api/status")
def api_status():

    return {

        "status": "online",

        "version": APP_VERSION,

        "sources": {

            "fallback": FALLBACK_API,

            "caixa": CAIXA_API

        },

        "cache_latest": (
            latest_cache["data"]
            is not None
        ),

        "cache_history": len(
            history_cache
        )

    }


# ============================================================
# ÚLTIMO RESULTADO
# ============================================================

@app.get("/api/lotofacil/latest")
def latest():

    agora = time.time()

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    if (

        latest_cache["data"] is not None

        and

        agora
        - latest_cache["timestamp"]
        < CACHE_LATEST_SECONDS

    ):

        return latest_cache["data"]

    # --------------------------------------------------------
    # BUSCA
    # --------------------------------------------------------

    try:

        resultado = fetch_latest()

        latest_cache["data"] = resultado

        latest_cache["timestamp"] = agora

        return resultado

    except Exception as e:

        raise HTTPException(

            status_code=502,

            detail=(
                "Não foi possível consultar "
                f"os resultados: {str(e)}"
            )

        )


# ============================================================
# HISTÓRICO
#
# IMPORTANTE:
# Esta rota vem ANTES de /{concurso}
# para evitar que "history" seja interpretado
# como número de concurso.
# ============================================================

@app.get("/api/lotofacil/history")
def history(

    limit: int = Query(
        120,
        ge=10,
        le=120
    )

):

    # --------------------------------------------------------
    # Descobrir concurso atual
    # --------------------------------------------------------

    try:

        atual = fetch_latest()

    except Exception as e:

        raise HTTPException(

            status_code=502,

            detail=(
                "Não foi possível descobrir "
                f"o concurso atual: {str(e)}"
            )

        )

    ultimo_concurso = atual[
        "concurso"
    ]

    # Guarda o atual
    history_cache[
        ultimo_concurso
    ] = {

        "data": atual,

        "timestamp": time.time()

    }

    # --------------------------------------------------------
    # Montar lista de concursos
    # --------------------------------------------------------

    concursos = [

        ultimo_concurso - i

        for i in range(limit)

        if ultimo_concurso - i > 0

    ]

    resultados = []

    faltantes = []

    agora = time.time()

    # --------------------------------------------------------
    # Verificar cache
    # --------------------------------------------------------

    for numero in concursos:

        cached = history_cache.get(
            numero
        )

        if cached:

            idade = (
                agora
                - cached["timestamp"]
            )

            if idade < CACHE_HISTORY_SECONDS:

                resultados.append(
                    cached["data"]
                )

                continue

        faltantes.append(
            numero
        )

    # --------------------------------------------------------
    # Buscar concursos faltantes
    # --------------------------------------------------------

    if faltantes:

        workers = min(
            5,
            len(faltantes)
        )

        with ThreadPoolExecutor(
            max_workers=workers
        ) as executor:

            tarefas = {

                executor.submit(
                    fetch_contest,
                    numero
                ): numero

                for numero in faltantes

            }

            for future in as_completed(
                tarefas
            ):

                numero = tarefas[
                    future
                ]

                try:

                    resultado = (
                        future.result()
                    )

                    history_cache[
                        numero
                    ] = {

                        "data": resultado,

                        "timestamp": time.time()

                    }

                    resultados.append(
                        resultado
                    )

                except Exception as e:

                    print(
                        "[HISTORY] "
                        f"Concurso {numero}: {e}"
                    )

    # --------------------------------------------------------
    # Remover duplicados
    # --------------------------------------------------------

    unicos = {}

    for resultado in resultados:

        numero = resultado[
            "concurso"
        ]

        unicos[numero] = resultado

    resultados = list(
        unicos.values()
    )

    # --------------------------------------------------------
    # Ordenar do mais recente
    # --------------------------------------------------------

    resultados.sort(

        key=lambda x: x[
            "concurso"
        ],

        reverse=True

    )

    # --------------------------------------------------------
    # Limitar quantidade
    # --------------------------------------------------------

    resultados = resultados[
        :limit
    ]

    # --------------------------------------------------------
    # Retorno
    # --------------------------------------------------------

    return {

        "concursoAtual":
            ultimo_concurso,

        "total":
            len(resultados),

        "resultados":
            resultados

    }


# ============================================================
# CONCURSO ESPECÍFICO
#
# ESTA ROTA FICA DEPOIS DE /history
# ============================================================

@app.get("/api/lotofacil/{concurso}")
def contest(concurso: int):

    if concurso <= 0:

        raise HTTPException(

            status_code=400,

            detail=(
                "Número de concurso inválido"
            )

        )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    cached = history_cache.get(
        concurso
    )

    if cached:

        idade = (
            time.time()
            - cached["timestamp"]
        )

        if idade < CACHE_HISTORY_SECONDS:

            return cached["data"]

    # --------------------------------------------------------
    # BUSCAR
    # --------------------------------------------------------

    try:

        resultado = fetch_contest(
            concurso
        )

        history_cache[
            concurso
        ] = {

            "data": resultado,

            "timestamp": time.time()

        }

        return resultado

    except Exception as e:

        raise HTTPException(

            status_code=502,

            detail=str(e)

        )


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

@app.get("/")
def root():

    index_path = os.path.join(

        STATIC_DIR,

        "index.html"

    )

    if os.path.exists(
        index_path
    ):

        return FileResponse(
            index_path
        )

    return {

        "app":
            "Lotofácil Monitor",

        "version":
            APP_VERSION,

        "status":
            "online"

    }


# ============================================================
# FAVICON
# ============================================================

@app.get("/favicon.ico")
def favicon():

    favicon_path = os.path.join(

        STATIC_DIR,

        "favicon.ico"

    )

    if os.path.exists(
        favicon_path
    ):

        return FileResponse(
            favicon_path
        )

    raise HTTPException(
        status_code=404
    )


# ============================================================
# EXECUÇÃO LOCAL
# ============================================================

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            8000
        )
    )

    uvicorn.run(

        "app:app",

        host="0.0.0.0",

        port=port

    )
