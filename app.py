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
# LOTOFÁCIL MONITOR — V25.11
# BACKEND
# ============================================================

APP_VERSION = "V25.11"

CAIXA_BASE = (
    "https://servicebus2.caixa.gov.br/"
    "portaldeloterias/api/lotofacil"
)

# Quantidade máxima de concursos usados pelo histórico
HISTORY_LIMIT = 120

# Cache em memória
CACHE_TTL = 600

# Controle de concorrência
MAX_WORKERS = 4

# Tentativas por requisição
MAX_RETRIES = 3

# Pequeno intervalo entre tentativas
RETRY_DELAY = 0.8


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
# HTTP CAIXA
# ============================================================

def caixa_get(url: str):

    ultimo_erro = None

    for tentativa in range(1, MAX_RETRIES + 1):

        try:

            req = Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 "
                        "Version/18.0 Mobile/15E148 Safari/604.1"
                    ),
                    "Accept": (
                        "application/json,"
                        "text/plain,"
                        "*/*"
                    ),
                    "Accept-Language": "pt-BR,pt;q=0.9",
                    "Referer": (
                        "https://loterias.caixa.gov.br/"
                    ),
                    "Connection": "close"
                }
            )

            with urlopen(
                req,
                timeout=12
            ) as response:

                status = response.getcode()

                if status != 200:
                    raise RuntimeError(
                        f"HTTP {status}"
                    )

                raw = response.read().decode(
                    "utf-8"
                )

                if not raw:
                    raise ValueError(
                        "Resposta vazia da CAIXA"
                    )

                return json.loads(raw)

        except HTTPError as e:

            ultimo_erro = (
                f"HTTP {e.code}"
            )

            # Erros que podem melhorar com retry
            if e.code in (
                408,
                425,
                429,
                500,
                502,
                503,
                504
            ):

                time.sleep(
                    RETRY_DELAY * tentativa
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

            time.sleep(
                RETRY_DELAY * tentativa
            )

        except Exception as e:

            ultimo_erro = str(e)

            time.sleep(
                RETRY_DELAY * tentativa
            )

    raise RuntimeError(
        ultimo_erro or
        "Falha desconhecida ao consultar CAIXA"
    )


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
        or data.get(
            "dezenasSorteadasOrdemSorteio"
        )
    )

    data_apuracao = (
        data.get("dataApuracao")
        or data.get("data")
        or ""
    )

    if numero is None:
        return None

    if not dezenas:
        return None

    try:

        numero = int(numero)

    except Exception:

        return None

    try:

        dezenas = [
            int(str(x).zfill(2))
            for x in dezenas
        ]

    except Exception:

        return None

    dezenas = sorted(
        set(
            x for x in dezenas
            if 1 <= x <= 25
        )
    )

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

        if (
            time.time() - timestamp
            > CACHE_TTL
        ):

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

        data = caixa_get(
            CAIXA_BASE
        )

        result = normalize_result(
            data
        )

        if not result:

            raise ValueError(
                "Resultado inválido da CAIXA"
            )

        cache_set(
            key,
            result
        )

        return result

    except Exception as e:

        raise HTTPException(
            status_code=502,
            detail=(
                "Erro ao consultar CAIXA: "
                f"{e}"
            )
        )


# ============================================================
# CONCURSO INDIVIDUAL
# ============================================================

def get_contest(numero):

    key = f"contest:{numero}"

    cached = cache_get(key)

    if cached:
        return cached

    url = (
        f"{CAIXA_BASE}/{numero}"
    )

    try:

        data = caixa_get(url)

        result = normalize_result(
            data
        )

        if not result:

            return None

        # Garante que o número retornado
        # corresponde ao concurso solicitado
        if (
            result["concurso"]
            != int(numero)
        ):

            return None

        cache_set(
            key,
            result
        )

        return result

    except Exception:

        return None


# ============================================================
# HISTÓRICO
# ============================================================

def get_history(
    limit=HISTORY_LIMIT
):

    # --------------------------------------------------------
    # Validação do limite
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Descobre o último concurso
    # --------------------------------------------------------

    latest = get_latest()

    ultimo = latest["concurso"]

    # --------------------------------------------------------
    # Monta lista de concursos
    # --------------------------------------------------------

    numeros = [
        ultimo - i
        for i in range(limite)
        if ultimo - i > 0
    ]

    resultados = []

    # --------------------------------------------------------
    # Primeiro aproveita o último resultado
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
    #
    # Antes:
    # 12 conexões simultâneas
    #
    # Agora:
    # 4 conexões
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {}

        for numero in numeros_restantes:

            futures[
                executor.submit(
                    get_contest,
                    numero
                )
            ] = numero

        for future in as_completed(
            futures
        ):

            try:

                result = future.result()

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

        concurso = resultado.get(
            "concurso"
        )

        if concurso is not None:

            unicos[
                int(concurso)
            ] = resultado

    resultados = list(
        unicos.values()
    )

    # --------------------------------------------------------
    # Ordena do mais antigo para o mais recente
    # --------------------------------------------------------

    resultados.sort(
        key=lambda x: x["concurso"]
    )

    return resultados


# ============================================================
# ROTA PRINCIPAL
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
        "version": APP_VERSION,
        "timestamp": int(
            time.time()
        )
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/api/status")
def api_status():

    latest = get_latest()

    return {
        "status": "online",
        "version": APP_VERSION,
        "ultimo_concurso": latest[
            "concurso"
        ],
        "data": latest[
            "data"
        ]
    }


# ============================================================
# ÚLTIMO RESULTADO
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
        "version": APP_VERSION,
        "limit": limit,
        "count": len(dados),
        "data": dados
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
                "Concurso não encontrado"
            )
        )

    return result


# ============================================================
# EXECUÇÃO LOCAL / RENDER
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
