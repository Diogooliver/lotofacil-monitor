import os
import json
import time
import ssl
import urllib.request
import urllib.error
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse


# ============================================================
# LOTOFÁCIL MONITOR V25.5
# Backend FastAPI
# ============================================================

APP_NAME = "Lotofácil Monitor"
VERSION = "V25.5"

# Endpoints conhecidos da API do Portal de Loterias CAIXA.
# Tentamos mais de um host porque o acesso pode variar.
CAIXA_HOSTS = [
    "https://servicebus2.caixa.gov.br",
    "https://servicebus3.caixa.gov.br",
]

CAIXA_PATH = "/portaldeloterias/api/lotofacil"

# Endpoint alternativo que pode retornar os últimos resultados
CAIXA_HOME_PATH = "/portaldeloterias/api/home/ultimos-resultados"

# Cache em memória
CACHE = {
    "latest": None,
    "latest_time": 0,
    "contests": {},
}

LATEST_CACHE_TTL = 60
CONTEST_CACHE_TTL = 86400


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description="Backend do Lotofácil Monitor V25.5",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# HEADERS
# ============================================================

def build_headers(host):
    """
    Headers semelhantes aos utilizados por navegadores
    ao acessar os serviços da CAIXA.
    """

    return {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) "
            "Version/17.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": (
            "application/json, text/plain, */*"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
        "Referer": "https://loterias.caixa.gov.br/",
        "Origin": "https://loterias.caixa.gov.br",
    }


# ============================================================
# SSL
# ============================================================

def get_ssl_context():
    """
    Utiliza o contexto SSL padrão do sistema.
    """

    return ssl.create_default_context()


# ============================================================
# REQUISIÇÃO CAIXA
# ============================================================

def caixa_request(path, timeout=20):
    """
    Tenta consultar os hosts da CAIXA.
    Retorna JSON quando conseguir.
    """

    last_error = None

    for host in CAIXA_HOSTS:

        url = host + path

        for tentativa in range(1, 4):

            try:
                headers = build_headers(host)

                request = urllib.request.Request(
                    url,
                    headers=headers,
                    method="GET",
                )

                context = get_ssl_context()

                with urllib.request.urlopen(
                    request,
                    timeout=timeout,
                    context=context,
                ) as response:

                    status = response.getcode()
                    body = response.read().decode("utf-8", errors="replace")

                    if status != 200:
                        raise RuntimeError(
                            f"HTTP {status}"
                        )

                    data = json.loads(body)

                    return data

            except urllib.error.HTTPError as e:

                last_error = f"HTTP {e.code}"

                # 403/429 podem ser bloqueio temporário.
                if e.code in (403, 429):
                    time.sleep(1.5 * tentativa)
                    continue

                # Outros erros HTTP
                break

            except Exception as e:

                last_error = str(e)

                time.sleep(0.8 * tentativa)

        # tenta o próximo host

    raise RuntimeError(
        f"Não foi possível consultar a CAIXA: {last_error}"
    )


# ============================================================
# NORMALIZAÇÃO DAS DEZENAS
# ============================================================

def normalize_numbers(value):
    """
    Converte listas como:
    ["01","02","03"]
    ou
    [1,2,3]
    para:
    [1,2,3]
    """

    if not isinstance(value, list):
        return []

    result = []

    for item in value:

        try:

            if isinstance(item, str):
                item = item.strip()

                if not item:
                    continue

            number = int(item)

            if 1 <= number <= 25:
                result.append(number)

        except Exception:
            continue

    return sorted(set(result))


# ============================================================
# EXTRAÇÃO DAS DEZENAS
# ============================================================

def extract_numbers(data):
    """
    Tenta encontrar as dezenas em diferentes formatos
    usados pelos retornos da CAIXA.
    """

    possible_fields = [
        "listaDezenas",
        "dezenas",
        "dezenasSorteadas",
        "dezenasSorteadasOrdemSorteio",
    ]

    if isinstance(data, dict):

        for field in possible_fields:

            if field in data:

                numbers = normalize_numbers(data[field])

                if len(numbers) >= 15:
                    return numbers[:15]

    return []


# ============================================================
# NORMALIZAÇÃO DO CONCURSO
# ============================================================

def extract_contest_number(data):

    if not isinstance(data, dict):
        return None

    possible_fields = [
        "numero",
        "concurso",
        "numeroConcurso",
    ]

    for field in possible_fields:

        value = data.get(field)

        if value is not None:

            try:
                return int(value)
            except Exception:
                pass

    return None


# ============================================================
# DATA
# ============================================================

def extract_date(data):

    if not isinstance(data, dict):
        return None

    possible_fields = [
        "dataApuracao",
        "data",
        "dataSorteio",
    ]

    for field in possible_fields:

        value = data.get(field)

        if value:
            return value

    return None


# ============================================================
# NORMALIZA OBJETO
# ============================================================

def normalize_contest(data):

    if not isinstance(data, dict):
        raise RuntimeError("Resposta da CAIXA não é um objeto JSON.")

    contest = extract_contest_number(data)
    numbers = extract_numbers(data)
    date = extract_date(data)

    if contest is None:
        raise RuntimeError(
            "Não foi possível identificar o número do concurso."
        )

    if len(numbers) < 15:
        raise RuntimeError(
            "Não foi possível identificar as 15 dezenas."
        )

    return {
        "concurso": contest,
        "numero": contest,
        "data": date,
        "dataApuracao": date,
        "dezenas": numbers,
        "listaDezenas": [
            f"{n:02d}" for n in numbers
        ],
        "resultado": numbers,
        "dezenasSorteadas": numbers,
        "dezenasSorteadasOrdemSorteio": numbers,
    }


# ============================================================
# CONSULTAR CONCURSO ESPECÍFICO
# ============================================================

def get_contest(contest_number):

    contest_number = int(contest_number)

    now = time.time()

    cached = CACHE["contests"].get(contest_number)

    if cached:

        timestamp = cached.get("_timestamp", 0)

        if now - timestamp < CONTEST_CACHE_TTL:

            result = dict(cached)
            result.pop("_timestamp", None)

            return result

    path = f"{CAIXA_PATH}/{contest_number}"

    raw = caixa_request(path)

    normalized = normalize_contest(raw)

    normalized["_timestamp"] = now

    CACHE["contests"][contest_number] = normalized

    result = dict(normalized)
    result.pop("_timestamp", None)

    return result


# ============================================================
# CONSULTAR ÚLTIMO CONCURSO
# ============================================================

def get_latest():

    now = time.time()

    if CACHE["latest"]:

        if now - CACHE["latest_time"] < LATEST_CACHE_TTL:

            return CACHE["latest"]

    # --------------------------------------------------------
    # PRIMEIRA TENTATIVA
    # --------------------------------------------------------

    try:

        raw = caixa_request(CAIXA_PATH)

        normalized = normalize_contest(raw)

        CACHE["latest"] = normalized
        CACHE["latest_time"] = now

        CACHE["contests"][normalized["concurso"]] = {
            **normalized,
            "_timestamp": now,
        }

        return normalized

    except Exception as first_error:

        # ----------------------------------------------------
        # SEGUNDA TENTATIVA:
        # endpoint de últimos resultados
        # ----------------------------------------------------

        try:

            raw_home = caixa_request(
                CAIXA_HOME_PATH
            )

            # Alguns retornos trazem a Lotofácil dentro
            # de uma chave chamada "lotofacil".

            if isinstance(raw_home, dict):

                candidate = raw_home.get("lotofacil")

                if candidate:

                    normalized = normalize_contest(
                        candidate
                    )

                    CACHE["latest"] = normalized
                    CACHE["latest_time"] = now

                    CACHE["contests"][
                        normalized["concurso"]
                    ] = {
                        **normalized,
                        "_timestamp": now,
                    }

                    return normalized

            raise RuntimeError(
                "Formato alternativo da CAIXA não reconhecido."
            )

        except Exception as second_error:

            raise RuntimeError(
                "Não foi possível consultar a CAIXA. "
                f"Tentativa principal: {first_error}. "
                f"Tentativa alternativa: {second_error}."
            )


# ============================================================
# HOME
# ============================================================

@app.get("/")
def root():

    possible_files = [
        "static/index.html",
        "index.html",
    ]

    for file_path in possible_files:

        if os.path.exists(file_path):

            return FileResponse(file_path)

    return {
        "status": "online",
        "app": APP_NAME,
        "version": VERSION,
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "online",
        "app": APP_NAME,
        "version": VERSION,
        "time": datetime.utcnow().isoformat() + "Z",
    }


# ============================================================
# INFO
# ============================================================

@app.get("/api")
def api_info():

    return {
        "status": "online",
        "app": APP_NAME,
        "version": VERSION,
        "endpoints": [
            "/health",
            "/api/lotofacil/latest",
            "/api/lotofacil/{concurso}",
            "/api/lotofacil/history",
        ],
    }


# ============================================================
# ÚLTIMO RESULTADO
# ============================================================

@app.get("/api/lotofacil/latest")
def latest_lotofacil():

    try:

        return get_latest()

    except Exception as e:

        raise HTTPException(
            status_code=502,
            detail=str(e),
        )


# ============================================================
# CONCURSO ESPECÍFICO
# ============================================================

@app.get("/api/lotofacil/{concurso}")
def specific_contest(concurso: int):

    if concurso <= 0:

        raise HTTPException(
            status_code=400,
            detail="Número de concurso inválido.",
        )

    try:

        return get_contest(concurso)

    except Exception as e:

        raise HTTPException(
            status_code=502,
            detail=str(e),
        )


# ============================================================
# HISTÓRICO
# ============================================================

@app.get("/api/lotofacil/history")
def history(
    limit: int = Query(
        120,
        ge=1,
        le=120,
    )
):

    try:

        latest_data = get_latest()

        latest_number = latest_data["concurso"]

        start_number = max(
            1,
            latest_number - limit + 1
        )

        contest_numbers = list(
            range(
                start_number,
                latest_number + 1
            )
        )

        results = []

        # ----------------------------------------------------
        # Para evitar sobrecarregar a CAIXA,
        # usamos poucos workers.
        # ----------------------------------------------------

        with ThreadPoolExecutor(
            max_workers=4
        ) as executor:

            future_map = {
                executor.submit(
                    get_contest,
                    contest
                ): contest

                for contest in contest_numbers
            }

            for future in as_completed(future_map):

                contest = future_map[future]

                try:

                    result = future.result()

                    if result:
                        results.append(result)

                except Exception as e:

                    print(
                        f"Erro no concurso {contest}: {e}"
                    )

        results.sort(
            key=lambda x: x["concurso"]
        )

        return results

    except Exception as e:

        raise HTTPException(
            status_code=502,
            detail=str(e),
        )


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
        app,
        host="0.0.0.0",
        port=port,
    )
