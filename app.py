from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json
import time
import os

# =========================================================
# LOTOFÁCIL MONITOR V25.4
# BACKEND FASTAPI + PROXY CAIXA
# =========================================================

app = FastAPI(
    title="Lotofácil Monitor V25.4",
    version="25.4"
)

# ---------------------------------------------------------
# CORS
# ---------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# CONFIGURAÇÕES
# ---------------------------------------------------------

CAIXA_BASE = (
    "https://servicebus2.caixa.gov.br/"
    "portaldeloterias/api/lotofacil"
)

MAX_HISTORY = 200
DEFAULT_HISTORY = 120

CACHE_TTL_LATEST = 60
CACHE_TTL_CONTEST = 86400

REQUEST_TIMEOUT = 20

# Cache em memória
cache_latest = {
    "data": None,
    "timestamp": 0
}

cache_contests = {}


# =========================================================
# UTILITÁRIOS
# =========================================================

def agora():
    return time.time()


def cache_valido(timestamp, ttl):
    return (
        timestamp > 0
        and agora() - timestamp < ttl
    )


def fazer_request(url):
    """
    Consulta a API da CAIXA.
    Usa urllib para não depender de requests/httpx.
    """

    headers = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120 Safari/537.36"
        ),
        "Connection": "close"
    }

    ultimo_erro = None

    for tentativa in range(3):

        try:

            req = Request(
                url,
                headers=headers,
                method="GET"
            )

            with urlopen(
                req,
                timeout=REQUEST_TIMEOUT
            ) as response:

                status = response.status

                if status != 200:
                    raise RuntimeError(
                        f"CAIXA retornou HTTP {status}"
                    )

                raw = response.read()

                return json.loads(
                    raw.decode("utf-8")
                )

        except HTTPError as e:

            ultimo_erro = (
                f"HTTP {e.code}"
            )

            if e.code in [429, 500, 502, 503, 504]:
                time.sleep(1.2 * (tentativa + 1))
                continue

            break

        except (
            URLError,
            TimeoutError,
            RuntimeError,
            json.JSONDecodeError
        ) as e:

            ultimo_erro = str(e)

            time.sleep(
                0.8 * (tentativa + 1)
            )

    raise RuntimeError(
        ultimo_erro or
        "Falha desconhecida ao consultar a CAIXA"
    )


# =========================================================
# NORMALIZAÇÃO
# =========================================================

def extrair_dezenas(data):

    if not isinstance(data, dict):
        return None

    campos = [
        "listaDezenas",
        "dezenas",
        "dezenasSorteadas",
        "dezenasSorteadasOrdemSorteio"
    ]

    for campo in campos:

        valor = data.get(campo)

        if not isinstance(valor, list):
            continue

        numeros = []

        for item in valor:

            try:

                if isinstance(item, int):
                    numero = item
                else:
                    texto = str(item)

                    somente_numeros = "".join(
                        c for c in texto
                        if c.isdigit()
                    )

                    if not somente_numeros:
                        continue

                    numero = int(
                        somente_numeros
                    )

                if 1 <= numero <= 25:
                    numeros.append(numero)

            except Exception:
                continue

        numeros = sorted(
            set(numeros)
        )

        if len(numeros) == 15:
            return numeros

    return None


def extrair_concurso(data):

    if not isinstance(data, dict):
        return 0

    for campo in [
        "numero",
        "concurso",
        "numeroConcurso"
    ]:

        try:

            valor = int(
                data.get(campo, 0)
            )

            if valor > 0:
                return valor

        except Exception:
            pass

    return 0


def normalizar_resultado(data):

    dezenas = extrair_dezenas(data)

    concurso = extrair_concurso(data)

    if not dezenas or not concurso:
        return None

    return {
        "concurso": concurso,
        "data": (
            data.get("dataApuracao")
            or data.get("data")
            or ""
        ),
        "dezenas": dezenas
    }


# =========================================================
# CONSULTA DE CONCURSO
# =========================================================

def buscar_concurso(numero):

    numero = int(numero)

    if numero <= 0:
        return None

    cached = cache_contests.get(numero)

    if cached:

        if cache_valido(
            cached["timestamp"],
            CACHE_TTL_CONTEST
        ):
            return cached["data"]

    url = f"{CAIXA_BASE}/{numero}"

    data = fazer_request(url)

    resultado = normalizar_resultado(data)

    if not resultado:
        raise RuntimeError(
            f"Resultado inválido para o concurso {numero}"
        )

    cache_contests[numero] = {
        "data": resultado,
        "timestamp": agora()
    }

    return resultado


# =========================================================
# ROOT / HEALTH
# =========================================================

@app.get("/")
def home():

    arquivo = Path(__file__).with_name(
        "index.html"
    )

    if arquivo.exists():
        return FileResponse(
            arquivo,
            media_type="text/html"
        )

    return {
        "status": "online",
        "app": "Lotofácil Monitor",
        "version": "V25.4"
    }


@app.get("/health")
def health():

    return {
        "status": "ok",
        "app": "Lotofácil Monitor",
        "version": "V25.4",
        "time": agora()
    }


# =========================================================
# ÚLTIMO CONCURSO
# =========================================================

@app.get("/api/lotofacil/latest")
def latest():

    if cache_latest["data"] is not None:

        if cache_valido(
            cache_latest["timestamp"],
            CACHE_TTL_LATEST
        ):
            return cache_latest["data"]

    try:

        data = fazer_request(
            CAIXA_BASE
        )

        resultado = normalizar_resultado(
            data
        )

        if not resultado:
            raise RuntimeError(
                "A CAIXA retornou um resultado inválido."
            )

        cache_latest["data"] = resultado
        cache_latest["timestamp"] = agora()

        # Também guarda o concurso no cache geral
        cache_contests[
            resultado["concurso"]
        ] = {
            "data": resultado,
            "timestamp": agora()
        }

        return resultado

    except Exception as e:

        raise HTTPException(
            status_code=502,
            detail=(
                "Não foi possível consultar "
                f"a CAIXA: {str(e)}"
            )
        )


# =========================================================
# CONCURSO ESPECÍFICO
# =========================================================

@app.get("/api/lotofacil/{concurso}")
def concurso(concurso: int):

    try:

        resultado = buscar_concurso(
            concurso
        )

        if not resultado:
            raise HTTPException(
                status_code=404,
                detail="Concurso não encontrado."
            )

        return resultado

    except HTTPException:
        raise

    except Exception as e:

        raise HTTPException(
            status_code=502,
            detail=str(e)
        )


# =========================================================
# HISTÓRICO
# =========================================================

@app.get("/api/lotofacil/history")
def history(
    limit: int = Query(
        DEFAULT_HISTORY,
        ge=1,
        le=MAX_HISTORY
    )
):

    try:

        # Primeiro descobre o concurso atual
        latest_data = latest()

        ultimo = int(
            latest_data["concurso"]
        )

        concursos = list(
            range(
                ultimo,
                max(
                    0,
                    ultimo - limit
                ),
                -1
            )
        )

        resultados = []

        # -------------------------------------------------
        # Busca paralela limitada
        # -------------------------------------------------

        with ThreadPoolExecutor(
            max_workers=8
        ) as executor:

            tarefas = {
                executor.submit(
                    buscar_concurso,
                    numero
                ): numero
                for numero in concursos
            }

            for tarefa in as_completed(
                tarefas
            ):

                numero = tarefas[tarefa]

                try:

                    resultado = tarefa.result()

                    if resultado:
                        resultados.append(
                            resultado
                        )

                except Exception as e:

                    print(
                        f"[HISTORY] Concurso "
                        f"{numero} falhou: {e}"
                    )

        # -------------------------------------------------
        # Ordena do mais antigo para o mais recente
        # -------------------------------------------------

        resultados.sort(
            key=lambda x: int(
                x["concurso"]
            )
        )

        return resultados

    except Exception as e:

        raise HTTPException(
            status_code=502,
            detail=(
                "Erro ao carregar histórico: "
                f"{str(e)}"
            )
        )


# =========================================================
# INFORMAÇÕES
# =========================================================

@app.get("/api")
def api_info():

    return {
        "app": "Lotofácil Monitor",
        "version": "V25.4",
        "status": "online",
        "endpoints": [
            "/api/lotofacil/latest",
            "/api/lotofacil/history",
            "/api/lotofacil/{concurso}",
            "/health"
        ]
    }


# =========================================================
# START LOCAL
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
        "main:app",
        host="0.0.0.0",
        port=port
    )
