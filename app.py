from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from concurrent.futures import ThreadPoolExecutor, as_completed

import json
import time
import threading
import os


# ============================================================
# LOTOFÁCIL MONITOR — V25.14
# ============================================================

APP_VERSION = "V25.14"

HISTORY_LIMIT = 120

CACHE_TTL = 900

CAIXA_TIMEOUT = 20

MAX_CAIXA_WORKERS = 10


# ============================================================
# FONTES
# ============================================================

# ------------------------------------------------------------
# HISTÓRICO COMPLETO — VALORFINAL
# ------------------------------------------------------------

REMOTE_HISTORY_URL = (
    "https://valorfinal.com.br/"
    "data/lotofacil-historico.json"
)


# ------------------------------------------------------------
# API OFICIAL CAIXA
# ------------------------------------------------------------

CAIXA_BASE_URL = (
    "https://servicebus2.caixa.gov.br/"
    "portaldeloterias/api/lotofacil"
)


# ------------------------------------------------------------
# ARQUIVO LOCAL
# ------------------------------------------------------------

LOCAL_DATA_DIR = "data"

LOCAL_DATA_FILE = os.path.join(
    LOCAL_DATA_DIR,
    "lotofacil.json"
)


# ============================================================
# CACHE
# ============================================================

cache = {}

cache_lock = threading.Lock()


# ============================================================
# INFORMAÇÕES DA ÚLTIMA FONTE
# ============================================================

source_status = {
    "fonte": None,
    "ultima_tentativa": None,
    "erro_valorfinal": None,
    "erro_caixa": None,
    "total": 0
}

source_status_lock = threading.Lock()


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Lotofácil Monitor",
    version=APP_VERSION
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ATUALIZAR STATUS DA FONTE
# ============================================================

def update_source_status(
    fonte=None,
    erro_valorfinal=None,
    erro_caixa=None,
    total=None
):

    with source_status_lock:

        source_status["ultima_tentativa"] = (
            time.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )

        if fonte is not None:

            source_status["fonte"] = fonte

        if erro_valorfinal is not None:

            source_status[
                "erro_valorfinal"
            ] = str(
                erro_valorfinal
            )

        if erro_caixa is not None:

            source_status[
                "erro_caixa"
            ] = str(
                erro_caixa
            )

        if total is not None:

            source_status[
                "total"
            ] = total


# ============================================================
# HTTP GET JSON
# ============================================================

def http_get_json(
    url: str,
    timeout=CAIXA_TIMEOUT
):

    headers_list = [

        {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0 Safari/537.36"
            ),
            "Accept": (
                "application/json,"
                "text/plain,"
                "*/*"
            ),
            "Accept-Language": (
                "pt-BR,pt;q=0.9"
            ),
            "Connection": "close"
        },

        {
            "User-Agent": (
                "LotofacilMonitor/"
                + APP_VERSION
            ),
            "Accept": (
                "application/json,"
                "*/*"
            ),
            "Connection": "close"
        }

    ]

    ultimo_erro = None

    for headers in headers_list:

        try:

            req = Request(
                url,
                headers=headers
            )

            with urlopen(
                req,
                timeout=timeout
            ) as response:

                raw = response.read().decode(
                    "utf-8"
                )

                if not raw:

                    raise RuntimeError(
                        "Resposta vazia."
                    )

                return json.loads(
                    raw
                )

        except Exception as e:

            ultimo_erro = e

            time.sleep(
                0.5
            )

    raise RuntimeError(
        f"Falha HTTP: {ultimo_erro}"
    )


# ============================================================
# NORMALIZAÇÃO DE UM RESULTADO
# ============================================================

def normalize_result(data):

    if not isinstance(
        data,
        dict
    ):

        return None


    # --------------------------------------------------------
    # NÚMERO DO CONCURSO
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


    if not dezenas:

        return None


    # --------------------------------------------------------
    # CONVERTE NÚMERO
    # --------------------------------------------------------

    try:

        numero = int(
            numero
        )

    except Exception:

        return None


    # --------------------------------------------------------
    # CONVERTE DEZENAS
    # --------------------------------------------------------

    try:

        # API Caixa pode devolver string.
        if isinstance(
            dezenas,
            str
        ):

            dezenas = (
                dezenas
                .replace(
                    ",",
                    " "
                )
                .split()
            )

        dezenas = sorted(
            int(str(x))
            for x in dezenas
        )

    except Exception:

        return None


    # --------------------------------------------------------
    # FILTRA FAIXA
    # --------------------------------------------------------

    dezenas = [
        x
        for x in dezenas
        if 1 <= x <= 25
    ]


    # --------------------------------------------------------
    # LOTOFÁCIL TEM 15 DEZENAS
    # --------------------------------------------------------

    if len(dezenas) != 15:

        return None


    # --------------------------------------------------------
    # REMOVE DUPLICADAS
    # --------------------------------------------------------

    if len(
        set(dezenas)
    ) != 15:

        return None


    return {
        "concurso": numero,
        "data": str(
            data_apuracao
        ),
        "dezenas": dezenas
    }


# ============================================================
# NORMALIZAÇÃO DO DATASET
# ============================================================

def normalize_dataset(data):

    resultados = []


    # ========================================================
    # FORMATO DICIONÁRIO
    # ========================================================

    if isinstance(
        data,
        dict
    ):

        concursos = data.get(
            "concursos"
        )


        # ----------------------------------------------------
        # FORMATO VALORFINAL
        # ----------------------------------------------------

        if isinstance(
            concursos,
            list
        ):

            for item in concursos:

                try:

                    if not isinstance(
                        item,
                        list
                    ):

                        continue


                    if len(item) < 3:

                        continue


                    numero = int(
                        item[0]
                    )


                    data_sorteio = (

                        str(
                            item[1]
                        )

                        if item[1] is not None

                        else ""
                    )


                    dezenas = sorted(
                        int(x)
                        for x in item[2]
                    )


                    dezenas = [
                        x
                        for x in dezenas
                        if 1 <= x <= 25
                    ]


                    if len(dezenas) != 15:

                        continue


                    if len(
                        set(dezenas)
                    ) != 15:

                        continue


                    resultados.append({

                        "concurso":
                            numero,

                        "data":
                            data_sorteio,

                        "dezenas":
                            dezenas
                    })


                except Exception:

                    continue


        # ----------------------------------------------------
        # FORMATO DICIONÁRIO
        #
        # {
        #   "3798": [1,2,4,...]
        # }
        # ----------------------------------------------------

        elif not concursos:

            for chave, valor in data.items():

                try:

                    numero = int(
                        chave
                    )

                except Exception:

                    continue


                if not isinstance(
                    valor,
                    list
                ):

                    continue


                try:

                    dezenas = sorted(
                        int(x)
                        for x in valor
                    )

                except Exception:

                    continue


                dezenas = [
                    x
                    for x in dezenas
                    if 1 <= x <= 25
                ]


                if len(dezenas) != 15:

                    continue


                if len(
                    set(dezenas)
                ) != 15:

                    continue


                resultados.append({

                    "concurso":
                        numero,

                    "data":
                        "",

                    "dezenas":
                        dezenas
                })


    # ========================================================
    # FORMATO LISTA
    # ========================================================

    elif isinstance(
        data,
        list
    ):

        for item in data:

            # ------------------------------------------------
            # ITEM DICIONÁRIO
            # ------------------------------------------------

            if isinstance(
                item,
                dict
            ):

                result = normalize_result(
                    item
                )

                if result:

                    resultados.append(
                        result
                    )


            # ------------------------------------------------
            # ITEM LISTA
            # ------------------------------------------------

            elif isinstance(
                item,
                list
            ):

                try:

                    if len(item) < 3:

                        continue


                    numero = int(
                        item[0]
                    )


                    data_sorteio = (

                        str(
                            item[1]
                        )

                        if item[1] is not None

                        else ""
                    )


                    dezenas = sorted(
                        int(x)
                        for x in item[2]
                    )


                    dezenas = [
                        x
                        for x in dezenas
                        if 1 <= x <= 25
                    ]


                    if len(dezenas) != 15:

                        continue


                    if len(
                        set(dezenas)
                    ) != 15:

                        continue


                    resultados.append({

                        "concurso":
                            numero,

                        "data":
                            data_sorteio,

                        "dezenas":
                            dezenas
                    })


                except Exception:

                    continue


    # ========================================================
    # REMOVE DUPLICADOS
    # ========================================================

    unicos = {}


    for resultado in resultados:

        numero = resultado[
            "concurso"
        ]

        unicos[
            numero
        ] = resultado


    resultados = list(
        unicos.values()
    )


    # ========================================================
    # ORDENA
    # ========================================================

    resultados.sort(
        key=lambda x:
            x["concurso"]
    )


    return resultados


# ============================================================
# CACHE GET
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
            time.time()
            - timestamp
            > CACHE_TTL
        ):

            return None


        return value


# ============================================================
# CACHE SET
# ============================================================

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
# SALVAR DATASET LOCAL
# ============================================================

def save_local_dataset(data):

    try:

        os.makedirs(
            LOCAL_DATA_DIR,
            exist_ok=True
        )


        temporary_file = (
            LOCAL_DATA_FILE
            + ".tmp"
        )


        with open(
            temporary_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False
            )


        os.replace(
            temporary_file,
            LOCAL_DATA_FILE
        )


        return True


    except Exception:

        return False


# ============================================================
# LER DATASET LOCAL
# ============================================================

def load_local_dataset():

    if not os.path.exists(
        LOCAL_DATA_FILE
    ):

        return None


    try:

        with open(
            LOCAL_DATA_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(
                f
            )


        resultados = normalize_dataset(
            data
        )


        if resultados:

            return resultados


    except Exception:

        pass


    return None


# ============================================================
# CARREGAR VALORFINAL
# ============================================================

def load_remote_dataset():

    data = http_get_json(
        REMOTE_HISTORY_URL,
        timeout=25
    )


    resultados = normalize_dataset(
        data
    )


    if not resultados:

        raise RuntimeError(
            "Dataset ValorFinal inválido "
            "ou formato não reconhecido."
        )


    # --------------------------------------------------------
    # SALVA CÓPIA LOCAL
    # --------------------------------------------------------

    save_local_dataset(
        data
    )


    return resultados


# ============================================================
# CARREGAR UM CONCURSO DA CAIXA
# ============================================================

def load_caixa_contest(
    numero
):

    url = (
        CAIXA_BASE_URL
        + "/"
        + str(numero)
    )


    data = http_get_json(
        url,
        timeout=CAIXA_TIMEOUT
    )


    resultado = normalize_result(
        data
    )


    if not resultado:

        raise RuntimeError(
            f"Resposta inválida "
            f"para concurso {numero}"
        )


    return resultado


# ============================================================
# DESCOBRIR ÚLTIMO CONCURSO NA CAIXA
# ============================================================

def get_caixa_latest():

    data = http_get_json(
        CAIXA_BASE_URL,
        timeout=CAIXA_TIMEOUT
    )


    resultado = normalize_result(
        data
    )


    if not resultado:

        raise RuntimeError(
            "API Caixa retornou "
            "dados inválidos."
        )


    return resultado


# ============================================================
# CARREGAR HISTÓRICO PELA CAIXA
# ============================================================

def load_caixa_history(
    limit=HISTORY_LIMIT
):

    # --------------------------------------------------------
    # PRIMEIRO DESCOBRE O ÚLTIMO
    # --------------------------------------------------------

    latest = get_caixa_latest()


    ultimo_numero = latest[
        "concurso"
    ]


    limite = max(
        1,
        min(
            int(limit),
            HISTORY_LIMIT
        )
    )


    primeiro_numero = max(
        1,
        ultimo_numero
        - limite
        + 1
    )


    numeros = list(
        range(
            primeiro_numero,
            ultimo_numero + 1
        )
    )


    resultados = []


    # --------------------------------------------------------
    # CONCURSO MAIS RECENTE
    # --------------------------------------------------------

    resultados.append(
        latest
    )


    numeros_restantes = [
        n
        for n in numeros
        if n != ultimo_numero
    ]


    # --------------------------------------------------------
    # BUSCA CONCORRENTE
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=MAX_CAIXA_WORKERS
    ) as executor:

        futures = {

            executor.submit(
                load_caixa_contest,
                numero
            ):
                numero

            for numero in numeros_restantes
        }


        for future in as_completed(
            futures
        ):

            numero = futures[
                future
            ]


            try:

                resultado = future.result()

                if resultado:

                    resultados.append(
                        resultado
                    )


            except Exception:

                continue


    # --------------------------------------------------------
    # REMOVE DUPLICADOS
    # --------------------------------------------------------

    unicos = {}


    for resultado in resultados:

        unicos[
            resultado["concurso"]
        ] = resultado


    resultados = list(
        unicos.values()
    )


    resultados.sort(
        key=lambda x:
            x["concurso"]
    )


    if not resultados:

        raise RuntimeError(
            "A API da Caixa não "
            "retornou concursos."
        )


    # --------------------------------------------------------
    # SALVA FORMATO LOCAL
    # --------------------------------------------------------

    local_format = {

        "atualizadoEm":
            time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "ultimoConcurso":
            resultados[-1][
                "concurso"
            ],

        "concursos": [

            [
                x["concurso"],
                x["data"],
                x["dezenas"]
            ]

            for x in resultados
        ]
    }


    save_local_dataset(
        local_format
    )


    return resultados


# ============================================================
# DATASET COMPLETO
# ============================================================

def get_all_history():

    key = "all_history"


    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    cached = cache_get(
        key
    )


    if cached:

        return cached


    # --------------------------------------------------------
    # 1 — ARQUIVO LOCAL
    # --------------------------------------------------------

    local = load_local_dataset()


    if local:

        update_source_status(
            fonte="local",
            total=len(local)
        )


        cache_set(
            key,
            local
        )


        return local


    # --------------------------------------------------------
    # 2 — VALORFINAL
    # --------------------------------------------------------

    try:

        remoto = load_remote_dataset()


        update_source_status(
            fonte="ValorFinal",
            erro_valorfinal=None,
            total=len(remoto)
        )


        cache_set(
            key,
            remoto
        )


        return remoto


    except Exception as valorfinal_error:

        update_source_status(
            erro_valorfinal=
                valorfinal_error
        )


    # --------------------------------------------------------
    # 3 — CAIXA
    # --------------------------------------------------------

    try:

        caixa = load_caixa_history(
            HISTORY_LIMIT
        )


        update_source_status(
            fonte="Caixa",
            erro_caixa=None,
            total=len(caixa)
        )


        cache_set(
            key,
            caixa
        )


        return caixa


    except Exception as caixa_error:

        update_source_status(
            fonte="erro",
            erro_caixa=caixa_error,
            total=0
        )


        raise HTTPException(

            status_code=502,

            detail={

                "mensagem":
                    "Não foi possível "
                    "carregar o histórico.",

                "valorfinal":
                    str(
                        valorfinal_error
                    )
                    if 'valorfinal_error'
                    in locals()
                    else None,

                "caixa":
                    str(
                        caixa_error
                    )
            }
        )


# ============================================================
# ÚLTIMO RESULTADO
# ============================================================

def get_latest():

    history = get_all_history()


    if not history:

        raise HTTPException(
            status_code=502,
            detail="Histórico vazio."
        )


    return history[-1]


# ============================================================
# HISTÓRICO
# ============================================================

def get_history(
    limit=HISTORY_LIMIT
):

    history = get_all_history()


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


    return history[
        -limite:
    ]


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

        "app":
            "Lotofácil Monitor",

        "version":
            APP_VERSION
    }


# ============================================================
# API — ÚLTIMO RESULTADO
# ============================================================

@app.get("/api/latest")
def api_latest():

    resultado = get_latest()


    return {

        "status":
            "ok",

        "version":
            APP_VERSION,

        "resultado":
            resultado
    }


# ============================================================
# API — HISTÓRICO
# ============================================================

@app.get("/api/history")
def api_history(
    limit: int = HISTORY_LIMIT
):

    resultados = get_history(
        limit
    )


    return {

        "status":
            "ok",

        "version":
            APP_VERSION,

        "total":
            len(resultados),

        "resultados":
            resultados
    }


# ============================================================
# ALIAS — HISTÓRICO
# ============================================================

@app.get("/history")
def history_alias(
    limit: int = HISTORY_LIMIT
):

    resultados = get_history(
        limit
    )


    return {

        "status":
            "ok",

        "version":
            APP_VERSION,

        "total":
            len(resultados),

        "resultados":
            resultados
    }


# ============================================================
# ALIAS — ÚLTIMO RESULTADO
# ============================================================

@app.get("/latest")
def latest_alias():

    resultado = get_latest()


    return {

        "status":
            "ok",

        "version":
            APP_VERSION,

        "resultado":
            resultado
    }


# ============================================================
# STATUS COMPLETO
# ============================================================

@app.get("/api/status")
def api_status():

    with source_status_lock:

        status_copy = dict(
            source_status
        )


    try:

        history = get_all_history()


        ultimo = (
            history[-1]
            if history
            else None
        )


        return {

            "status":
                "online",

            "app":
                "Lotofácil Monitor",

            "version":
                APP_VERSION,

            "fonte":
                status_copy.get(
                    "fonte"
                ),

            "total_concursos":
                len(history),

            "ultimo_concurso":
                (
                    ultimo["concurso"]
                    if ultimo
                    else None
                ),

            "cache_ttl":
                CACHE_TTL,

            "ultima_tentativa":
                status_copy.get(
                    "ultima_tentativa"
                ),

            "erro_valorfinal":
                status_copy.get(
                    "erro_valorfinal"
                ),

            "erro_caixa":
                status_copy.get(
                    "erro_caixa"
                )
        }


    except Exception as e:

        return JSONResponse(

            status_code=502,

            content={

                "status":
                    "error",

                "app":
                    "Lotofácil Monitor",

                "version":
                    APP_VERSION,

                "erro":
                    str(e),

                "fonte":
                    status_copy.get(
                        "fonte"
                    ),

                "erro_valorfinal":
                    status_copy.get(
                        "erro_valorfinal"
                    ),

                "erro_caixa":
                    status_copy.get(
                        "erro_caixa"
                    )
            }
        )


# ============================================================
# TESTE DA FONTE VALORFINAL
# ============================================================

@app.get("/api/test-source")
def test_source():

    resultado = {

        "version":
            APP_VERSION,

        "valorfinal":
            None,

        "caixa":
            None
    }


    # --------------------------------------------------------
    # TESTE VALORFINAL
    # --------------------------------------------------------

    try:

        data = http_get_json(
            REMOTE_HISTORY_URL,
            timeout=20
        )


        normalizado = normalize_dataset(
            data
        )


        resultado[
            "valorfinal"
        ] = {

            "status":
                "ok",

            "total":
                len(
                    normalizado
                ),

            "ultimo":
                (
                    normalizado[-1]
                    if normalizado
                    else None
                )
        }


    except Exception as e:

        resultado[
            "valorfinal"
        ] = {

            "status":
                "erro",

            "erro":
                str(e)
        }


    # --------------------------------------------------------
    # TESTE CAIXA
    # --------------------------------------------------------

    try:

        latest = get_caixa_latest()


        resultado[
            "caixa"
        ] = {

            "status":
                "ok",

            "ultimo":
                latest
        }


    except Exception as e:

        resultado[
            "caixa"
        ] = {

            "status":
                "erro",

            "erro":
                str(e)
        }


    return resultado


# ============================================================
# FINAL
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

        app,

        host="0.0.0.0",

        port=port
    )
