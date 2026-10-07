from fastapi import FastAPI, HTTPException, Query
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
# LOTOFÁCIL MONITOR — V25.15
# ============================================================

APP_VERSION = "V25.15"

HISTORY_LIMIT = 120

CACHE_TTL = 900

HTTP_TIMEOUT = 25

CAIXA_TIMEOUT = 20

MAX_CAIXA_WORKERS = 8


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
# PÁGINA DO DATASET — FALLBACK
# ------------------------------------------------------------

REMOTE_DATA_PAGE = (
    "https://valorfinal.com.br/"
    "dados/loteria-lotofacil"
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
# STATUS DA FONTE
# ============================================================

source_status = {

    "fonte": None,

    "ultima_tentativa": None,

    "erro_valorfinal": None,

    "erro_caixa": None,

    "total": 0,

    "ultimo_concurso": None

}

source_status_lock = threading.Lock()


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(

    title="Lotofácil Monitor",

    version=APP_VERSION,

    description=(
        "API do Lotofácil Monitor"
    )
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(

    CORSMiddleware,

    allow_origins=["*"],

    allow_credentials=False,

    allow_methods=["*"],

    allow_headers=["*"]

)


# ============================================================
# STATUS
# ============================================================

def update_source_status(

    fonte=None,

    erro_valorfinal=None,

    erro_caixa=None,

    total=None,

    ultimo_concurso=None

):

    with source_status_lock:

        source_status[
            "ultima_tentativa"
        ] = time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )


        if fonte is not None:

            source_status[
                "fonte"
            ] = fonte


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
            ] = int(total)


        if ultimo_concurso is not None:

            source_status[
                "ultimo_concurso"
            ] = int(
                ultimo_concurso
            )


# ============================================================
# HTTP GET JSON
# ============================================================

def http_get_json(

    url: str,

    timeout=HTTP_TIMEOUT

):

    headers_list = [

        {

            "User-Agent": (
                "Mozilla/5.0 "
                "(iPhone; CPU iPhone OS 18_0 "
                "like Mac OS X) "
                "AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) "
                "Version/18.0 "
                "Mobile/15E148 "
                "Safari/604.1"
            ),

            "Accept": (
                "application/json,"
                "text/plain,"
                "*/*"
            ),

            "Accept-Language": (
                "pt-BR,pt;q=0.9,en;q=0.8"
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

                headers=headers,

                method="GET"

            )


            with urlopen(

                req,

                timeout=timeout

            ) as response:

                raw = response.read()


                if not raw:

                    raise RuntimeError(
                        "Resposta vazia."
                    )


                charset = (
                    response.headers.get_content_charset()
                    or "utf-8"
                )


                text = raw.decode(
                    charset,
                    errors="replace"
                )


                if not text.strip():

                    raise RuntimeError(
                        "Resposta vazia."
                    )


                return json.loads(
                    text
                )


        except HTTPError as e:

            ultimo_erro = (
                f"HTTP {e.code}: {e.reason}"
            )


        except URLError as e:

            ultimo_erro = (
                f"URL Error: {e.reason}"
            )


        except Exception as e:

            ultimo_erro = str(e)


        time.sleep(0.5)


    raise RuntimeError(

        "Falha HTTP em "
        f"{url}: {ultimo_erro}"

    )


# ============================================================
# NORMALIZAR RESULTADO INDIVIDUAL
# ============================================================

def normalize_result(data):

    if not isinstance(
        data,
        dict
    ):

        return None


    numero = (

        data.get("numero")

        or data.get("concurso")

        or data.get("numeroConcurso")

        or data.get("Concurso")

    )


    dezenas = (

        data.get("listaDezenas")

        or data.get("dezenas")

        or data.get(
            "dezenasSorteadasOrdemSorteio"
        )

        or data.get("Dezenas")

    )


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


    try:

        numero = int(
            numero
        )

    except Exception:

        return None


    try:

        if isinstance(
            dezenas,
            str
        ):

            dezenas = (

                dezenas
                .replace(",", " ")
                .replace(";", " ")
                .split()

            )


        dezenas = [

            int(str(x).strip())

            for x in dezenas

        ]


        dezenas = sorted(
            set(dezenas)
        )


    except Exception:

        return None


    dezenas = [

        x

        for x in dezenas

        if 1 <= x <= 25

    ]


    if len(dezenas) != 15:

        return None


    return {

        "concurso": numero,

        "data": str(
            data_apuracao
        ),

        "dezenas": dezenas

    }


# ============================================================
# NORMALIZAR DATASET
# ============================================================

def normalize_dataset(data):

    resultados = []


    # ========================================================
    # DICIONÁRIO
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

                        continue


                    if not isinstance(
                        item,
                        (list, tuple)
                    ):

                        continue


                    if len(item) < 3:

                        continue


                    numero = int(
                        item[0]
                    )


                    data_sorteio = (

                        str(item[1])

                        if item[1] is not None

                        else ""

                    )


                    dezenas = item[2]


                    if isinstance(
                        dezenas,
                        str
                    ):

                        dezenas = (

                            dezenas
                            .replace(",", " ")
                            .split()

                        )


                    dezenas = sorted(
                        set(
                            int(x)
                            for x in dezenas
                        )
                    )


                    dezenas = [

                        x

                        for x in dezenas

                        if 1 <= x <= 25

                    ]


                    if len(dezenas) != 15:

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
        # FORMATO:
        #
        # {
        #   "3798": [1,2,3,...]
        # }
        # ----------------------------------------------------

        else:

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
                        set(
                            int(x)
                            for x in valor
                        )
                    )


                    dezenas = [

                        x

                        for x in dezenas

                        if 1 <= x <= 25

                    ]


                    if len(dezenas) != 15:

                        continue


                    resultados.append({

                        "concurso":
                            numero,

                        "data":
                            "",

                        "dezenas":
                            dezenas

                    })


                except Exception:

                    continue


    # ========================================================
    # LISTA
    # ========================================================

    elif isinstance(
        data,
        list
    ):

        for item in data:

            # ------------------------------------------------
            # DICIONÁRIO
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
            # LISTA
            # ------------------------------------------------

            elif isinstance(
                item,
                (list, tuple)
            ):

                try:

                    if len(item) < 3:

                        continue


                    numero = int(
                        item[0]
                    )


                    data_sorteio = (

                        str(item[1])

                        if item[1] is not None

                        else ""

                    )


                    dezenas = item[2]


                    if isinstance(
                        dezenas,
                        str
                    ):

                        dezenas = (

                            dezenas
                            .replace(",", " ")
                            .split()

                        )


                    dezenas = sorted(
                        set(
                            int(x)
                            for x in dezenas
                        )
                    )


                    dezenas = [

                        x

                        for x in dezenas

                        if 1 <= x <= 25

                    ]


                    if len(dezenas) != 15:

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

        try:

            numero = int(
                resultado[
                    "concurso"
                ]
            )


            unicos[
                numero
            ] = resultado

        except Exception:

            continue


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
# VALIDAR HISTÓRICO
# ============================================================

def validate_history(
    resultados
):

    if not resultados:

        return False


    validos = []


    for resultado in resultados:

        if not isinstance(
            resultado,
            dict
        ):

            continue


        try:

            concurso = int(
                resultado[
                    "concurso"
                ]
            )


            dezenas = resultado[
                "dezenas"
            ]


            if concurso < 1:

                continue


            if not isinstance(
                dezenas,
                list
            ):

                continue


            if len(dezenas) != 15:

                continue


            if len(
                set(dezenas)
            ) != 15:

                continue


            if any(
                x < 1 or x > 25
                for x in dezenas
            ):

                continue


            validos.append(
                resultado
            )


        except Exception:

            continue


    return len(validos) > 0


# ============================================================
# CACHE GET
# ============================================================

def cache_get(key):

    with cache_lock:

        item = cache.get(
            key
        )


        if item is None:

            return None


        timestamp, value = item


        if (
            time.time()
            - timestamp
            > CACHE_TTL
        ):

            cache.pop(
                key,
                None
            )

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
# LIMPAR CACHE
# ============================================================

def cache_clear():

    with cache_lock:

        cache.clear()


# ============================================================
# SALVAR DATASET LOCAL
# ============================================================

def save_local_dataset(
    data
):

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


        if validate_history(
            resultados
        ):

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

        timeout=HTTP_TIMEOUT

    )


    resultados = normalize_dataset(
        data
    )


    if not validate_history(
        resultados
    ):

        raise RuntimeError(

            "Dataset ValorFinal "
            "inválido ou formato "
            "não reconhecido."

        )


    # --------------------------------------------------------
    # SALVA SOMENTE SE FOR VÁLIDO
    # --------------------------------------------------------

    save_local_dataset(
        data
    )


    return resultados


# ============================================================
# CARREGAR CONCURSO DA CAIXA
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

            "Resposta inválida "
            f"para concurso {numero}"

        )


    return resultado


# ============================================================
# ÚLTIMO CONCURSO DA CAIXA
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
# HISTÓRICO DA CAIXA
# ============================================================

def load_caixa_history(

    limit=HISTORY_LIMIT

):

    latest = get_caixa_latest()


    ultimo_numero = latest[
        "concurso"
    ]


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


    resultados = [
        latest
    ]


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

            ): numero

            for numero in numeros_restantes

        }


        for future in as_completed(
            futures
        ):

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
            resultado[
                "concurso"
            ]
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
# ATUALIZAR HISTÓRICO
# ============================================================

def refresh_history():

    erros = []


    # ========================================================
    # 1 — VALORFINAL
    # ========================================================

    try:

        remoto = load_remote_dataset()


        if validate_history(
            remoto
        ):

            update_source_status(

                fonte="ValorFinal",

                erro_valorfinal=None,

                total=len(remoto),

                ultimo_concurso=(
                    remoto[-1]["concurso"]
                )

            )


            cache_set(

                "all_history",

                remoto

            )


            return remoto


    except Exception as e:

        erros.append(
            f"ValorFinal: {e}"
        )


        update_source_status(

            erro_valorfinal=e

        )


    # ========================================================
    # 2 — CAIXA
    # ========================================================

    try:

        caixa = load_caixa_history(

            HISTORY_LIMIT

        )


        if validate_history(
            caixa
        ):

            update_source_status(

                fonte="Caixa",

                erro_caixa=None,

                total=len(caixa),

                ultimo_concurso=(
                    caixa[-1]["concurso"]
                )

            )


            cache_set(

                "all_history",

                caixa

            )


            return caixa


    except Exception as e:

        erros.append(
            f"Caixa: {e}"
        )


        update_source_status(

            erro_caixa=e

        )


    # ========================================================
    # 3 — ARQUIVO LOCAL
    # ========================================================

    try:

        local = load_local_dataset()


        if validate_history(
            local
        ):

            update_source_status(

                fonte="Local",

                total=len(local),

                ultimo_concurso=(
                    local[-1]["concurso"]
                )

            )


            cache_set(

                "all_history",

                local

            )


            return local


    except Exception as e:

        erros.append(
            f"Local: {e}"
        )


    # ========================================================
    # FALHA TOTAL
    # ========================================================

    raise HTTPException(

        status_code=502,

        detail={

            "mensagem":
                "Não foi possível "
                "carregar o histórico.",

            "erros":
                erros

        }

    )


# ============================================================
# HISTÓRICO COMPLETO
# ============================================================

def get_all_history():

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    cached = cache_get(
        "all_history"
    )


    if cached:

        return cached


    # --------------------------------------------------------
    # ATUALIZA
    # --------------------------------------------------------

    return refresh_history()


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
# HISTÓRICO LIMITADO
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


    return history[-limite:]


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    path = os.path.join(

        "static",

        "index.html"

    )


    if os.path.exists(path):

        return FileResponse(
            path
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

    limit: int = Query(

        HISTORY_LIMIT,

        ge=1,

        le=HISTORY_LIMIT

    )

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
# API — HISTÓRICO LOTOFÁCIL
# ============================================================

@app.get("/api/lotofacil/history")
def api_lotofacil_history(

    limit: int = Query(

        HISTORY_LIMIT,

        ge=1,

        le=HISTORY_LIMIT

    )

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
# ALIAS — HISTORY
# ============================================================

@app.get("/history")
def history_alias(

    limit: int = Query(

        HISTORY_LIMIT,

        ge=1,

        le=HISTORY_LIMIT

    )

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
# ALIAS — ÚLTIMO
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
# API — ÚLTIMO LOTOFÁCIL
# ============================================================

@app.get("/api/lotofacil/latest")
def api_lotofacil_latest():

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
# STATUS
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
# TESTAR VALORFINAL
# ============================================================

@app.get("/api/test-source")
def test_source():

    resultado = {

        "version":
            APP_VERSION,

        "valorfinal":
            None,

        "caixa":
            None,

        "local":
            None

    }


    # ========================================================
    # VALORFINAL
    # ========================================================

    try:

        data = http_get_json(

            REMOTE_HISTORY_URL,

            timeout=HTTP_TIMEOUT

        )


        normalizado = normalize_dataset(
            data
        )


        resultado[
            "valorfinal"
        ] = {

            "status":
                "ok"
                if validate_history(
                    normalizado
                )
                else "invalido",

            "total":
                len(normalizado),

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


    # ========================================================
    # CAIXA
    # ========================================================

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


    # ========================================================
    # LOCAL
    # ========================================================

    try:

        local = load_local_dataset()


        resultado[
            "local"
        ] = {

            "status":
                "ok"
                if local
                else "vazio",

            "total":
                len(local)
                if local
                else 0,

            "ultimo":
                (
                    local[-1]

                    if local

                    else None
                )

        }


    except Exception as e:

        resultado[
            "local"
        ] = {

            "status":
                "erro",

            "erro":
                str(e)

        }


    return resultado


# ============================================================
# FORÇAR ATUALIZAÇÃO
# ============================================================

@app.get("/api/refresh")
def api_refresh():

    cache_clear()


    try:

        history = refresh_history()


        ultimo = (

            history[-1]

            if history

            else None

        )


        return {

            "status":
                "ok",

            "version":
                APP_VERSION,

            "fonte":
                source_status[
                    "fonte"
                ],

            "total":
                len(history),

            "ultimo":
                ultimo

        }


    except HTTPException:

        raise


    except Exception as e:

        raise HTTPException(

            status_code=502,

            detail=str(e)

        )


# ============================================================
# DEBUG
# ============================================================

@app.get("/api/debug")
def api_debug():

    with source_status_lock:

        status_copy = dict(
            source_status
        )


    cached = cache_get(
        "all_history"
    )


    return {

        "app":
            "Lotofácil Monitor",

        "version":
            APP_VERSION,

        "remote_history_url":
            REMOTE_HISTORY_URL,

        "caixa_base_url":
            CAIXA_BASE_URL,

        "cache":
            bool(cached),

        "cache_total":
            len(cached)
            if cached
            else 0,

        "local_file_exists":
            os.path.exists(
                LOCAL_DATA_FILE
            ),

        "status":
            status_copy

    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup_event():

    print(
        "=========================================="
    )

    print(
        f"LOTOFÁCIL MONITOR {APP_VERSION}"
    )

    print(
        "Servidor iniciado."
    )

    print(
        "=========================================="
    )


# ============================================================
# MAIN
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
