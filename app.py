from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from urllib.request import Request, urlopen

import json
import time
import threading
import os


# ============================================================
# LOTOFÁCIL MONITOR — V25.13
# ============================================================

APP_VERSION = "V25.13"

HISTORY_LIMIT = 120
CACHE_TTL = 900


# ============================================================
# FONTES
# ============================================================

# Fonte principal:
# Histórico completo atualizado do ValorFinal.
REMOTE_HISTORY_URL = (
    "https://valorfinal.com.br/"
    "data/lotofacil-historico.json"
)

# Arquivo local opcional.
# Se existir, ele terá prioridade sobre a fonte remota.
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
# HTTP GET JSON
# ============================================================

def http_get_json(url: str):

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
            "Accept-Language": "pt-BR,pt;q=0.9",
            "Connection": "close",
        },
        {
            "User-Agent": "LotofacilMonitor/25.13",
            "Accept": "application/json,*/*",
            "Connection": "close",
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
                timeout=25
            ) as response:

                raw = response.read().decode(
                    "utf-8"
                )

                return json.loads(raw)

        except Exception as e:

            ultimo_erro = e

            time.sleep(0.5)

    raise RuntimeError(
        f"Falha ao acessar fonte: {ultimo_erro}"
    )


# ============================================================
# NORMALIZAÇÃO DE UM RESULTADO
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
        or data.get(
            "dezenasSorteadasOrdemSorteio"
        )
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
            int(str(x))
            for x in dezenas
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
        "data": str(data_apuracao),
        "dezenas": dezenas
    }


# ============================================================
# NORMALIZAÇÃO DO DATASET
# ============================================================

def normalize_dataset(data):

    resultados = []

    # --------------------------------------------------------
    # FORMATO VALORFINAL
    #
    # {
    #   "atualizadoEm": "...",
    #   "ultimoConcurso": 3798,
    #   "concursos": [
    #       [3798, "2026-10-06", [1,2,...]]
    #   ]
    # }
    # --------------------------------------------------------

    if isinstance(data, dict):

        concursos = data.get(
            "concursos"
        )

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
                        str(item[1])
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

                    resultados.append({
                        "concurso": numero,
                        "data": data_sorteio,
                        "dezenas": dezenas
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

                    numero = int(chave)

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

                resultados.append({
                    "concurso": numero,
                    "data": "",
                    "dezenas": dezenas
                })

    # --------------------------------------------------------
    # FORMATO LISTA
    # --------------------------------------------------------

    elif isinstance(data, list):

        for item in data:

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

            elif isinstance(
                item,
                list
            ):

                try:

                    if len(item) >= 3:

                        numero = int(
                            item[0]
                        )

                        data_sorteio = (
                            str(item[1])
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

                        if len(dezenas) == 15:

                            resultados.append({
                                "concurso": numero,
                                "data": data_sorteio,
                                "dezenas": dezenas
                            })

                except Exception:

                    continue

    # --------------------------------------------------------
    # REMOVE DUPLICADOS
    # --------------------------------------------------------

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

    resultados.sort(
        key=lambda x: x["concurso"]
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
            time.time() - timestamp
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

            data = json.load(f)

        resultados = normalize_dataset(
            data
        )

        if resultados:

            return resultados

    except Exception:

        pass

    return None


# ============================================================
# CARREGAR DATASET REMOTO
# ============================================================

def load_remote_dataset():

    data = http_get_json(
        REMOTE_HISTORY_URL
    )

    resultados = normalize_dataset(
        data
    )

    if not resultados:

        raise RuntimeError(
            "Dataset remoto inválido."
        )

    # Guarda uma cópia local
    # para permitir recuperação.
    save_local_dataset(
        data
    )

    return resultados


# ============================================================
# DATASET COMPLETO
# ============================================================

def get_all_history():

    key = "all_history"

    cached = cache_get(
        key
    )

    if cached:

        return cached

    # --------------------------------------------------------
    # 1. Tenta arquivo local
    # --------------------------------------------------------

    local = load_local_dataset()

    if local:

        cache_set(
            key,
            local
        )

        return local

    # --------------------------------------------------------
    # 2. Tenta fonte remota
    # --------------------------------------------------------

    try:

        remoto = load_remote_dataset()

        cache_set(
            key,
            remoto
        )

        return remoto

    except Exception as remote_error:

        raise HTTPException(
            status_code=502,
            detail=(
                "Não foi possível carregar "
                "o histórico da Lotofácil. "
                f"Fonte: {remote_error}"
            )
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

        limite = int(limit)

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
            "app": "Lotofácil Monitor",
            "version": APP_VERSION,
            "status": "online"
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
        "status": "online",
        "app": "Lotofácil Monitor",
        "version": APP_VERSION
    }


# ============================================================
# API — ÚLTIMO RESULTADO
# ============================================================

@app.get("/api/latest")
def api_latest():

    resultado = get_latest()

    return {
        "status": "ok",
        "version": APP_VERSION,
        "resultado": resultado
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
        "status": "ok",
        "version": APP_VERSION,
        "total": len(resultados),
        "resultados": resultados
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
        "status": "ok",
        "version": APP_VERSION,
        "total": len(resultados),
        "resultados": resultados
    }


# ============================================================
# ALIAS — ÚLTIMO RESULTADO
# ============================================================

@app.get("/latest")
def latest_alias():

    resultado = get_latest()

    return {
        "status": "ok",
        "version": APP_VERSION,
        "resultado": resultado
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/api/status")
def api_status():

    try:

        history = get_all_history()

        ultimo = history[-1] if history else None

        return {
            "status": "online",
            "app": "Lotofácil Monitor",
            "version": APP_VERSION,
            "total_concursos": len(history),
            "ultimo_concurso": (
                ultimo["concurso"]
                if ultimo
                else None
            ),
            "cache_ttl": CACHE_TTL
        }

    except Exception as e:

        return JSONResponse(
            status_code=502,
            content={
                "status": "error",
                "app": "Lotofácil Monitor",
                "version": APP_VERSION,
                "erro": str(e)
            }
        )


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
