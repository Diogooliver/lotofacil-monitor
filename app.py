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
# LOTOFÁCIL MONITOR — V25.12
# BACKEND
#
# V25.12
#
# PRINCIPAL:
# API GUIDI
#
# FALLBACK 1:
# API CAIXA
#
# FALLBACK 2:
# GitHub Raw
#
# ============================================================


APP_VERSION = "V25.12"


# ============================================================
# CONFIGURAÇÕES
# ============================================================

HISTORY_LIMIT = 120

CACHE_TTL = 900

MAX_WORKERS = 4

MAX_RETRIES = 3

REQUEST_TIMEOUT = 12

RETRY_DELAY = 1.0


# ============================================================
# FONTES
# ============================================================

GUIDI_BASE = (
    "https://api.guidi.dev.br/loteria/lotofacil"
)


CAIXA_BASE = (
    "https://servicebus2.caixa.gov.br/"
    "portaldeloterias/api/lotofacil"
)


GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/"
    "maickon/free-apiloterias/"
    "refs/heads/master/"
    "database/lotofacil"
)


# ============================================================
# CACHE
# ============================================================

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
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# HTTP GENÉRICO
# ============================================================

def http_get_json(
    url,
    retries=MAX_RETRIES
):

    ultimo_erro = None

    for tentativa in range(
        1,
        retries + 1
    ):

        try:

            req = Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 "
                        "(KHTML, like Gecko) "
                        "Version/18.0 Mobile/15E148 "
                        "Safari/604.1"
                    ),

                    "Accept": (
                        "application/json,"
                        "text/plain,"
                        "*/*"
                    ),

                    "Accept-Language":
                        "pt-BR,pt;q=0.9",

                    "Connection":
                        "close"
                }
            )


            with urlopen(
                req,
                timeout=REQUEST_TIMEOUT
            ) as response:

                status = response.getcode()


                if status != 200:

                    raise RuntimeError(
                        f"HTTP {status}"
                    )


                raw = (
                    response
                    .read()
                    .decode(
                        "utf-8"
                    )
                )


                if not raw:

                    raise ValueError(
                        "Resposta vazia"
                    )


                return json.loads(
                    raw
                )


        except HTTPError as e:

            ultimo_erro = (
                f"HTTP {e.code}"
            )


            # ------------------------------------------------
            # Erros temporários
            # ------------------------------------------------

            if e.code in (
                408,
                425,
                429,
                500,
                502,
                503,
                504
            ):

                if tentativa < retries:

                    time.sleep(
                        RETRY_DELAY *
                        tentativa
                    )

                    continue


            break


        except (
            URLError,
            TimeoutError,
            ConnectionError,
            ValueError,
            json.JSONDecodeError
        ) as e:

            ultimo_erro = str(e)


            if tentativa < retries:

                time.sleep(
                    RETRY_DELAY *
                    tentativa
                )

                continue


        except Exception as e:

            ultimo_erro = str(e)


            if tentativa < retries:

                time.sleep(
                    RETRY_DELAY *
                    tentativa
                )

                continue


    raise RuntimeError(
        ultimo_erro or
        "Falha HTTP desconhecida"
    )


# ============================================================
# CACHE
# ============================================================

def cache_get(key):

    with cache_lock:

        item = cache.get(
            key
        )


        if not item:

            return None


        timestamp, value = item


        if (
            time.time() -
            timestamp
            > CACHE_TTL
        ):

            return None


        return value


def cache_set(
    key,
    value
):

    with cache_lock:

        cache[key] = (
            time.time(),
            value
        )


# ============================================================
# NORMALIZAÇÃO
# ============================================================

def normalize_result(
    data
):

    # --------------------------------------------------------
    # Algumas APIs retornam:
    #
    # objeto
    #
    # ou lista contendo objetos
    # --------------------------------------------------------

    if isinstance(
        data,
        list
    ):

        if not data:

            return None

        # Procura primeiro
        # elemento compatível

        for item in data:

            result = normalize_result(
                item
            )

            if result:

                return result

        return None


    if not isinstance(
        data,
        dict
    ):

        return None


    # --------------------------------------------------------
    # Alguns provedores podem
    # encapsular o resultado
    # --------------------------------------------------------

    for key in (
        "data",
        "resultado",
        "result",
        "dados"
    ):

        nested = data.get(
            key
        )


        if isinstance(
            nested,
            dict
        ):

            nested_result = (
                normalize_result(
                    nested
                )
            )


            if nested_result:

                return nested_result


        if isinstance(
            nested,
            list
        ):

            nested_result = (
                normalize_result(
                    nested
                )
            )


            if nested_result:

                return nested_result


    # --------------------------------------------------------
    # CONCURSO
    # --------------------------------------------------------

    numero = (
        data.get("numero")
        or data.get("concurso")
        or data.get("numeroConcurso")
        or data.get("Concurso")
    )


    # --------------------------------------------------------
    # DEZENAS
    # --------------------------------------------------------

    dezenas = (
        data.get("listaDezenas")
        or data.get("dezenas")
        or data.get(
            "dezenasSorteadasOrdemSorteio"
        )
        or data.get("Dezenas")
    )


    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    data_apuracao = (
        data.get("dataApuracao")
        or data.get("data")
        or data.get("Data")
        or ""
    )


    if numero is None:

        return None


    if dezenas is None:

        return None


    # --------------------------------------------------------
    # Número do concurso
    # --------------------------------------------------------

    try:

        numero = int(
            numero
        )

    except Exception:

        return None


    # --------------------------------------------------------
    # Dezenas
    #
    # Pode chegar como:
    #
    # ["01","02","03"]
    #
    # ou:
    #
    # "01,02,03"
    #
    # ou:
    #
    # "01 02 03"
    # --------------------------------------------------------

    if isinstance(
        dezenas,
        str
    ):

        texto = (
            dezenas
            .replace(
                ",",
                " "
            )
            .replace(
                ";",
                " "
            )
        )


        dezenas = (
            texto
            .split()
        )


    try:

        dezenas = [
            int(
                str(x)
                .strip()
                .zfill(2)
            )
            for x in dezenas
        ]

    except Exception:

        return None


    # --------------------------------------------------------
    # Remove duplicadas
    # --------------------------------------------------------

    dezenas = sorted(
        set(
            x
            for x in dezenas
            if 1 <= x <= 25
        )
    )


    # --------------------------------------------------------
    # Lotofácil sempre precisa
    # ter 15 dezenas
    # --------------------------------------------------------

    if len(dezenas) != 15:

        return None


    return {

        "concurso":
            numero,

        "data":
            data_apuracao,

        "dezenas":
            dezenas
    }


# ============================================================
# GUIDI — ÚLTIMO
# ============================================================

def get_guidi_latest():

    url = (
        GUIDI_BASE +
        "/ultimo"
    )


    data = http_get_json(
        url
    )


    result = normalize_result(
        data
    )


    if not result:

        raise ValueError(
            "Resposta inválida da API Guidi"
        )


    return result


# ============================================================
# GUIDI — CONCURSO
# ============================================================

def get_guidi_contest(
    numero
):

    url = (
        GUIDI_BASE +
        f"/{numero}"
    )


    data = http_get_json(
        url
    )


    result = normalize_result(
        data
    )


    if not result:

        return None


    if (
        result["concurso"]
        != int(numero)
    ):

        return None


    return result


# ============================================================
# CAIXA — ÚLTIMO
# ============================================================

def get_caixa_latest():

    data = http_get_json(
        CAIXA_BASE
    )


    result = normalize_result(
        data
    )


    if not result:

        raise ValueError(
            "Resposta inválida da CAIXA"
        )


    return result


# ============================================================
# CAIXA — CONCURSO
# ============================================================

def get_caixa_contest(
    numero
):

    url = (
        f"{CAIXA_BASE}/{numero}"
    )


    data = http_get_json(
        url
    )


    result = normalize_result(
        data
    )


    if not result:

        return None


    if (
        result["concurso"]
        != int(numero)
    ):

        return None


    return result


# ============================================================
# GITHUB RAW — CONCURSO
#
# É apenas um fallback.
# A base pública pode ficar atrasada.
# ============================================================

def get_github_contest(
    numero
):

    url = (
        f"{GITHUB_RAW_BASE}/"
        f"{numero}.json"
    )


    try:

        data = http_get_json(
            url,
            retries=1
        )


        result = normalize_result(
            data
        )


        if not result:

            return None


        if (
            result["concurso"]
            != int(numero)
        ):

            return None


        return result


    except Exception:

        return None


# ============================================================
# ÚLTIMO CONCURSO
#
# ORDEM:
#
# 1. CACHE
# 2. GUIDI
# 3. CAIXA
# 4. GITHUB
# ============================================================

def get_latest():

    key = "latest"


    cached = cache_get(
        key
    )


    if cached:

        return cached


    erros = []


    # --------------------------------------------------------
    # GUIDI
    # --------------------------------------------------------

    try:

        result = get_guidi_latest()


        if result:

            cache_set(
                key,
                result
            )


            return result


    except Exception as e:

        erros.append(
            "Guidi: " +
            str(e)
        )


    # --------------------------------------------------------
    # CAIXA
    # --------------------------------------------------------

    try:

        result = get_caixa_latest()


        if result:

            cache_set(
                key,
                result
            )


            return result


    except Exception as e:

        erros.append(
            "CAIXA: " +
            str(e)
        )


    # --------------------------------------------------------
    # GITHUB
    # --------------------------------------------------------

    # Não usamos _ultimo como
    # fonte oficial atual, pois
    # pode estar atrasado.
    #
    # Portanto o GitHub fica
    # apenas como fallback futuro
    # para concursos específicos.
    # --------------------------------------------------------


    raise HTTPException(

        status_code=502,

        detail=(
            "Não foi possível "
            "obter o último "
            "resultado. "
            +
            " | ".join(erros)
        )
    )


# ============================================================
# CONCURSO INDIVIDUAL
#
# ORDEM:
#
# 1. CACHE
# 2. GUIDI
# 3. CAIXA
# 4. GITHUB
# ============================================================

def get_contest(
    numero
):

    numero = int(
        numero
    )


    key = (
        f"contest:{numero}"
    )


    cached = cache_get(
        key
    )


    if cached:

        return cached


    # --------------------------------------------------------
    # GUIDI
    # --------------------------------------------------------

    try:

        result = (
            get_guidi_contest(
                numero
            )
        )


        if result:

            cache_set(
                key,
                result
            )


            return result


    except Exception:

        pass


    # --------------------------------------------------------
    # CAIXA
    # --------------------------------------------------------

    try:

        result = (
            get_caixa_contest(
                numero
            )
        )


        if result:

            cache_set(
                key,
                result
            )


            return result


    except Exception:

        pass


    # --------------------------------------------------------
    # GITHUB
    # --------------------------------------------------------

    try:

        result = (
            get_github_contest(
                numero
            )
        )


        if result:

            cache_set(
                key,
                result
            )


            return result


    except Exception:

        pass


    return None


# ============================================================
# HISTÓRICO
# ============================================================

def get_history(
    limit=HISTORY_LIMIT
):

    # --------------------------------------------------------
    # Limite
    # --------------------------------------------------------

    try:

        limite = int(
            limit
        )

    except Exception:

        limite = HISTORY_LIMIT


    limite = max(
        1,
        min(
            limite,
            HISTORY_LIMIT
        )
    )


    # --------------------------------------------------------
    # Último concurso
    # --------------------------------------------------------

    latest = get_latest()


    ultimo = (
        latest["concurso"]
    )


    # --------------------------------------------------------
    # Lista
    # --------------------------------------------------------

    numeros = [
        ultimo - i
        for i in range(
            limite
        )
        if ultimo - i > 0
    ]


    resultados = []


    # --------------------------------------------------------
    # Último resultado
    # --------------------------------------------------------

    resultados.append(
        latest
    )


    numeros_restantes = [
        n
        for n in numeros
        if n != ultimo
    ]


    # --------------------------------------------------------
    # Busca controlada
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:


        futures = {

            executor.submit(
                get_contest,
                numero
            ):
                numero

            for numero
            in numeros_restantes

        }


        for future in as_completed(
            futures
        ):

            try:

                result = (
                    future.result()
                )


                if result:

                    resultados.append(
                        result
                    )


            except Exception:

                continue


    # --------------------------------------------------------
    # Remove duplicados
    # --------------------------------------------------------

    unicos = {}


    for resultado in resultados:

        concurso = (
            resultado.get(
                "concurso"
            )
        )


        if concurso is not None:

            unicos[
                int(concurso)
            ] = resultado


    resultados = list(
        unicos.values()
    )


    # --------------------------------------------------------
    # Ordena
    # --------------------------------------------------------

    resultados.sort(
        key=lambda x:
            x["concurso"]
    )


    # --------------------------------------------------------
    # Segurança
    # --------------------------------------------------------

    if not resultados:

        raise HTTPException(

            status_code=502,

            detail=(
                "Nenhum concurso "
                "foi carregado."
            )
        )


    return resultados


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    path = os.path.join(
        "static",
        "index.html"
    )


    if not os.path.exists(
        path
    ):

        return JSONResponse({

            "app":
                "Lotofácil Monitor",

            "version":
                APP_VERSION,

            "status":
                "online"

        })


    return FileResponse(
        path
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {

        "status":
            "online",

        "version":
            APP_VERSION,

        "timestamp":
            int(
                time.time()
            )
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/api/status")
def api_status():

    latest = (
        get_latest()
    )


    return {

        "status":
            "online",

        "version":
            APP_VERSION,

        "ultimo_concurso":
            latest[
                "concurso"
            ],

        "data":
            latest[
                "data"
            ]
    }


# ============================================================
# ÚLTIMO
# ============================================================

@app.get(
    "/api/lotofacil/latest"
)
def api_latest():

    return get_latest()


# ============================================================
# HISTÓRICO
# ============================================================

@app.get(
    "/api/lotofacil/history"
)
def api_history(
    limit: int = HISTORY_LIMIT
):

    dados = get_history(
        limit
    )


    return {

        "version":
            APP_VERSION,

        "limit":
            limit,

        "count":
            len(
                dados
            ),

        "data":
            dados
    }


# ============================================================
# CONCURSO INDIVIDUAL
# ============================================================

@app.get(
    "/api/lotofacil/{concurso}"
)
def api_contest(
    concurso: int
):

    result = get_contest(
        concurso
    )


    if not result:

        raise HTTPException(

            status_code=404,

            detail=(
                "Concurso "
                "não encontrado"
            )
        )


    return result


# ============================================================
# EXECUÇÃO
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
