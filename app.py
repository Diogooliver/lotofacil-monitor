import os
import time
import asyncio
from typing import Any

import httpx

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


# =========================================================
# LOTOFÁCIL MONITOR V21.3
# BACKEND
# =========================================================

app = FastAPI(
    title="Lotofácil Monitor V21.3",
    version="21.3"
)


# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# CAMINHOS
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATIC_DIR = os.path.join(
    BASE_DIR,
    "static"
)

INDEX_FILE = os.path.join(
    STATIC_DIR,
    "index.html"
)


# =========================================================
# CAIXA
# =========================================================

CAIXA_URL = (
    "https://servicebus2.caixa.gov.br/"
    "portaldeloterias/api/lotofacil"
)


# =========================================================
# CACHE
# =========================================================

CACHE_TTL = 300

CACHE = {
    "latest": None,
    "history": None,
    "latest_time": 0,
    "history_time": 0,
}


# =========================================================
# HEADERS
# =========================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) "
        "Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "application/json,"
        "text/plain,"
        "*/*"
    ),
    "Referer": (
        "https://loterias.caixa.gov.br/"
    ),
}


# =========================================================
# FUNÇÕES DE CACHE
# =========================================================

def cache_valid(key: str) -> bool:

    value = CACHE.get(key)

    timestamp_key = f"{key}_time"

    timestamp = CACHE.get(
        timestamp_key,
        0
    )

    if value is None:
        return False

    return (
        time.time() - timestamp
        < CACHE_TTL
    )


def cache_save(
    key: str,
    value: Any
):

    CACHE[key] = value

    CACHE[f"{key}_time"] = (
        time.time()
    )


# =========================================================
# CONVERSÃO DE NÚMERO
# =========================================================

def to_int(value):

    try:

        if value is None:
            return None

        numero = int(
            str(value).strip()
        )

        if numero > 0:
            return numero

    except Exception:
        pass

    return None


# =========================================================
# EXTRAIR CONCURSO
# =========================================================

def extract_contest(
    data,
    fallback=None
):

    if not isinstance(
        data,
        dict
    ):
        return fallback


    fields = [
        "numero",
        "numeroConcurso",
        "concurso",
        "numero_concurso",
    ]


    for field in fields:

        value = data.get(field)

        numero = to_int(value)

        if numero:
            return numero


    resultado = data.get(
        "resultado"
    )


    if isinstance(
        resultado,
        dict
    ):

        for field in fields:

            value = resultado.get(
                field
            )

            numero = to_int(value)

            if numero:
                return numero


    return fallback


# =========================================================
# EXTRAIR DATA
# =========================================================

def extract_date(data):

    if not isinstance(
        data,
        dict
    ):
        return ""


    fields = [
        "dataApuracao",
        "dataApuracaoStr",
        "data",
        "dataSorteio",
    ]


    for field in fields:

        value = data.get(field)

        if value:

            return str(value)


    resultado = data.get(
        "resultado"
    )


    if isinstance(
        resultado,
        dict
    ):

        for field in fields:

            value = resultado.get(
                field
            )

            if value:

                return str(value)


    return ""


# =========================================================
# EXTRAIR DEZENAS
# =========================================================

def normalize_numbers(value):

    if not isinstance(
        value,
        list
    ):
        return []


    numbers = []


    for item in value:

        try:

            numero = int(
                str(item).strip()
            )

            if 1 <= numero <= 25:

                numbers.append(
                    numero
                )

        except Exception:
            pass


    numbers = sorted(
        set(numbers)
    )


    if len(numbers) == 15:

        return numbers


    return []


def extract_numbers(data):

    if not isinstance(
        data,
        dict
    ):
        return []


    fields = [
        "listaDezenas",
        "dezenas",
        "listaDezenasSorteadas",
        "dezenasSorteadas",
    ]


    # -----------------------------------------------------
    # Primeiro nível
    # -----------------------------------------------------

    for field in fields:

        numbers = normalize_numbers(
            data.get(field)
        )

        if len(numbers) == 15:

            return numbers


    # -----------------------------------------------------
    # Dentro de resultado
    # -----------------------------------------------------

    resultado = data.get(
        "resultado"
    )


    if isinstance(
        resultado,
        dict
    ):

        for field in fields:

            numbers = normalize_numbers(
                resultado.get(field)
            )

            if len(numbers) == 15:

                return numbers


    # -----------------------------------------------------
    # Procura recursivamente listas
    # -----------------------------------------------------

    def search(obj):

        if isinstance(
            obj,
            dict
        ):

            for value in obj.values():

                result = search(value)

                if result:

                    return result


        elif isinstance(
            obj,
            list
        ):

            numbers = normalize_numbers(
                obj
            )

            if len(numbers) == 15:

                return numbers


            for value in obj:

                result = search(value)

                if result:

                    return result


        return []


    return search(data)


# =========================================================
# NORMALIZAR RESULTADO
# =========================================================

def normalize_result(
    data,
    fallback_contest=None
):

    contest = extract_contest(
        data,
        fallback_contest
    )

    date = extract_date(
        data
    )

    numbers = extract_numbers(
        data
    )


    if not numbers:

        raise ValueError(
            "As 15 dezenas não foram encontradas."
        )


    if len(numbers) != 15:

        raise ValueError(
            "O resultado não possui 15 dezenas."
        )


    return {
        "concurso": contest,
        "data": date,
        "dezenas": numbers,
    }


# =========================================================
# CONSULTAR CAIXA
# =========================================================

async def request_caixa(
    url: str
):

    timeout = httpx.Timeout(
        connect=20,
        read=30,
        write=20,
        pool=20
    )


    async with httpx.AsyncClient(
        timeout=timeout,
        headers=HEADERS,
        follow_redirects=True
    ) as client:

        response = await client.get(
            url
        )


    if response.status_code != 200:

        raise RuntimeError(
            "CAIXA retornou HTTP "
            + str(response.status_code)
        )


    content_type = (
        response.headers
        .get(
            "content-type",
            ""
        )
        .lower()
    )


    try:

        data = response.json()

        return data

    except Exception:

        text = response.text[:500]

        raise RuntimeError(
            "CAIXA não retornou JSON válido. "
            + text
        )


# =========================================================
# ROTA PRINCIPAL
# =========================================================

@app.get("/")
async def home():

    if not os.path.exists(
        INDEX_FILE
    ):

        raise HTTPException(
            status_code=500,
            detail=(
                "index.html não encontrado "
                "dentro da pasta static."
            )
        )


    return FileResponse(
        INDEX_FILE
    )


# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
async def health():

    return {
        "ok": True,
        "status": "online",
        "version": "V21.3",
    }


# =========================================================
# TESTE DA API
# =========================================================

@app.get("/api/test")
async def api_test():

    return {
        "ok": True,
        "message": (
            "API Lotofácil Monitor funcionando."
        ),
        "version": "V21.3",
    }


# =========================================================
# ÚLTIMO RESULTADO
# =========================================================

@app.get(
    "/api/lotofacil/latest"
)
async def latest_result():

    if cache_valid("latest"):

        return {
            "ok": True,
            "source": "cache",
            "resultado": CACHE["latest"],
        }


    try:

        data = await request_caixa(
            CAIXA_URL
        )


        resultado = normalize_result(
            data
        )


        cache_save(
            "latest",
            resultado
        )


        return {
            "ok": True,
            "source": "caixa",
            "resultado": resultado,
        }


    except Exception as error:

        # -------------------------------------------------
        # Se houver cache antigo, usa como fallback
        # -------------------------------------------------

        if CACHE["latest"]:

            return {
                "ok": True,
                "source": "cache_fallback",
                "resultado": CACHE["latest"],
                "warning": str(error),
            }


        raise HTTPException(
            status_code=502,
            detail=(
                "Não foi possível consultar "
                "o último resultado: "
                + str(error)
            )
        )


# =========================================================
# CONCURSO ESPECÍFICO
# =========================================================

@app.get(
    "/api/lotofacil/{concurso}"
)
async def specific_contest(
    concurso: int
):

    if concurso <= 0:

        raise HTTPException(
            status_code=400,
            detail="Concurso inválido."
        )


    url = (
        CAIXA_URL
        + "/"
        + str(concurso)
    )


    try:

        data = await request_caixa(
            url
        )


        resultado = normalize_result(
            data,
            fallback_contest=concurso
        )


        return {
            "ok": True,
            "source": "caixa",
            "resultado": resultado,
        }


    except Exception as error:

        raise HTTPException(
            status_code=502,
            detail=(
                "Erro ao consultar o concurso "
                + str(concurso)
                + ": "
                + str(error)
            )
        )


# =========================================================
# HISTÓRICO
# =========================================================

@app.get(
    "/api/lotofacil/history"
)
async def history(
    limit: int = 30
):

    # Limite de segurança
    limit = max(
        1,
        min(
            int(limit),
            50
        )
    )


    # -----------------------------------------------------
    # CACHE
    # -----------------------------------------------------

    if cache_valid("history"):

        historico = CACHE[
            "history"
        ]

        return {
            "ok": True,
            "source": "cache",
            "historico": historico[:limit],
        }


    try:

        # -------------------------------------------------
        # Primeiro pega o último concurso
        # -------------------------------------------------

        latest_data = await request_caixa(
            CAIXA_URL
        )


        latest = normalize_result(
            latest_data
        )


        latest_contest = latest.get(
            "concurso"
        )


        if not latest_contest:

            raise RuntimeError(
                "A CAIXA não informou "
                "o número do concurso."
            )


        results = [
            latest
        ]


        # -------------------------------------------------
        # Busca concursos anteriores
        # -------------------------------------------------

        for offset in range(
            1,
            limit
        ):

            contest_number = (
                latest_contest
                - offset
            )


            if contest_number <= 0:

                break


            url = (
                CAIXA_URL
                + "/"
                + str(contest_number)
            )


            try:

                data = await request_caixa(
                    url
                )


                result = normalize_result(
                    data,
                    fallback_contest=contest_number
                )


                results.append(
                    result
                )


            except Exception:

                # Se um concurso falhar,
                # continua para o próximo.
                pass


            # Pequeno intervalo entre consultas.
            await asyncio.sleep(
                0.15
            )


        # -------------------------------------------------
        # Remove duplicados
        # -------------------------------------------------

        unique = {}


        for result in results:

            contest = result.get(
                "concurso"
            )


            if contest:

                unique[
                    int(contest)
                ] = result


        results = list(
            unique.values()
        )


        # -------------------------------------------------
        # Mais recente primeiro
        # -------------------------------------------------

        results.sort(
            key=lambda item: int(
                item["concurso"]
            ),
            reverse=True
        )


        results = results[
            :limit
        ]


        # -------------------------------------------------
        # Salva cache
        # -------------------------------------------------

        cache_save(
            "history",
            results
        )


        cache_save(
            "latest",
            results[0]
            if results
            else latest
        )


        return {
            "ok": True,
            "source": "caixa",
            "historico": results,
        }


    except Exception as error:

        # -------------------------------------------------
        # Cache de emergência
        # -------------------------------------------------

        if CACHE["history"]:

            return {
                "ok": True,
                "source": "cache_fallback",
                "historico": CACHE[
                    "history"
                ][:limit],
                "warning": str(error),
            }


        raise HTTPException(
            status_code=502,
            detail=(
                "Erro ao carregar histórico: "
                + str(error)
            )
        )


# =========================================================
# ROTA COMPATÍVEL
# =========================================================

@app.get(
    "/api/lotofacil"
)
async def lotofacil_compat():

    return await latest_result()


# =========================================================
# MONTAR ARQUIVOS ESTÁTICOS
# =========================================================

if os.path.isdir(
    STATIC_DIR
):

    app.mount(
        "/static",
        StaticFiles(
            directory=STATIC_DIR
        ),
        name="static"
    )


# =========================================================
# EXECUÇÃO LOCAL
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
        "app:app",
        host="0.0.0.0",
        port=port
    )
