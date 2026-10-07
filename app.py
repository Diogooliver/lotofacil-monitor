import os
import json
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse


# ============================================================
# LOTOFÁCIL MONITOR — BACKEND V25.6
# ============================================================

APP_VERSION = "V25.6"

# API oficial/interna da CAIXA
CAIXA_API = "https://servicebus2.caixa.gov.br/portaldeloterias/api/lotofacil"

# API alternativa utilizada como fallback
FALLBACK_API = "https://loteriascaixa-api.herokuapp.com/api/lotofacil"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


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
CACHE_HISTORY_SECONDS = 60 * 60 * 6


# ============================================================
# HTTP
# ============================================================

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/120.0 Mobile Safari/537.36"
)


def request_json(url, timeout=5):
    """
    Faz uma requisição rápida.
    Nunca deixa o backend preso por muito tempo.
    """

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Connection": "close",
    }

    request = urllib.request.Request(
        url,
        headers=headers,
        method="GET"
    )

    start = time.time()

    try:

        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            status = response.status
            body = response.read().decode("utf-8")

            elapsed = round(time.time() - start, 2)

            if status != 200:
                raise RuntimeError(
                    f"HTTP {status}"
                )

            data = json.loads(body)

            return data, elapsed

    except Exception as e:

        raise RuntimeError(
            f"{type(e).__name__}: {str(e)}"
        )


# ============================================================
# NORMALIZAÇÃO
# ============================================================

def normalize_result(raw):
    """
    Converte diferentes formatos de APIs
    para um formato único utilizado pelo frontend.
    """

    if not isinstance(raw, dict):
        raise ValueError("Resposta inválida da API")

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
        or raw.get("dezenasSorteadasOrdemSorteio")
    )

    # Alguns retornos podem vir como string
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
            numero = int(str(numero).strip())

            if 1 <= numero <= 25:
                dezenas_normalizadas.append(numero)

        except Exception:
            pass

    # Remove duplicados e ordena
    dezenas_normalizadas = sorted(
        list(set(dezenas_normalizadas))
    )

    if not concurso:
        raise ValueError("Concurso não encontrado")

    if len(dezenas_normalizadas) < 15:
        raise ValueError(
            f"Resultado incompleto: {len(dezenas_normalizadas)} dezenas"
        )

    return {
        "concurso": int(concurso),
        "numero": int(concurso),
        "data": data,
        "dataApuracao": data,
        "dezenas": [
            f"{n:02d}" for n in dezenas_normalizadas
        ],
        "listaDezenas": [
            f"{n:02d}" for n in dezenas_normalizadas
        ],
        "resultado": [
            f"{n:02d}" for n in dezenas_normalizadas
        ],
        "dezenasSorteadas": [
            f"{n:02d}" for n in dezenas_normalizadas
        ],
    }


# ============================================================
# BUSCA DO ÚLTIMO RESULTADO
# ============================================================

def fetch_latest():
    """
    Busca o concurso mais recente.

    Prioridade:
    1. API alternativa rápida
    2. API oficial CAIXA
    """

    errors = []

    # --------------------------------------------------------
    # 1 — FALLBACK
    # --------------------------------------------------------

    try:

        raw, elapsed = request_json(
            FALLBACK_API + "/latest",
            timeout=5
        )

        result = normalize_result(raw)

        result["_source"] = "fallback"
        result["_response_time"] = elapsed

        return result

    except Exception as e:

        errors.append(
            f"fallback: {str(e)}"
        )

    # --------------------------------------------------------
    # 2 — CAIXA
    # --------------------------------------------------------

    try:

        raw, elapsed = request_json(
            CAIXA_API,
            timeout=4
        )

        result = normalize_result(raw)

        result["_source"] = "caixa"
        result["_response_time"] = elapsed

        return result

    except Exception as e:

        errors.append(
            f"caixa: {str(e)}"
        )

    raise RuntimeError(
        "Nenhuma fonte respondeu. "
        + " | ".join(errors)
    )


# ============================================================
# BUSCA DE CONCURSO ESPECÍFICO
# ============================================================

def fetch_contest(contest):
    """
    Busca um concurso específico.

    O fallback vem primeiro para evitar
    que o Render fique esperando a CAIXA.
    """

    errors = []

    urls = [
        (
            "fallback",
            f"{FALLBACK_API}/{contest}",
            5
        ),
        (
            "caixa",
            f"{CAIXA_API}/{contest}",
            4
        )
    ]

    for source, url, timeout in urls:

        try:

            raw, elapsed = request_json(
                url,
                timeout=timeout
            )

            result = normalize_result(raw)

            # Garante que o resultado corresponde
            # ao concurso solicitado.
            if result["concurso"] != int(contest):
                raise ValueError(
                    "Concurso retornado diferente do solicitado"
                )

            result["_source"] = source
            result["_response_time"] = elapsed

            return result

        except Exception as e:

            errors.append(
                f"{source}: {str(e)}"
            )

    raise RuntimeError(
        f"Concurso {contest} indisponível. "
        + " | ".join(errors)
    )


# ============================================================
# HEALTH
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
# STATUS
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
        "cache_latest": bool(latest_cache["data"]),
        "cache_history": len(history_cache)
    }


# ============================================================
# ÚLTIMO RESULTADO
# ============================================================

@app.get("/api/lotofacil/latest")
def latest():

    now = time.time()

    # Cache
    if (
        latest_cache["data"] is not None
        and now - latest_cache["timestamp"]
        < CACHE_LATEST_SECONDS
    ):

        return latest_cache["data"]

    try:

        result = fetch_latest()

        latest_cache["data"] = result
        latest_cache["timestamp"] = now

        return result

    except Exception as e:

        raise HTTPException(
            status_code=502,
            detail=f"Não foi possível consultar resultados: {str(e)}"
        )


# ============================================================
# CONCURSO ESPECÍFICO
# ============================================================

@app.get("/api/lotofacil/{concurso}")
def contest(concurso: int):

    if concurso <= 0:
        raise HTTPException(
            status_code=400,
            detail="Número de concurso inválido"
        )

    # Cache
    cached = history_cache.get(concurso)

    if cached:

        age = time.time() - cached["timestamp"]

        if age < CACHE_HISTORY_SECONDS:
            return cached["data"]

    try:

        result = fetch_contest(concurso)

        history_cache[concurso] = {
            "data": result,
            "timestamp": time.time()
        }

        return result

    except Exception as e:

        raise HTTPException(
            status_code=502,
            detail=str(e)
        )


# ============================================================
# HISTÓRICO
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
    # Descobre o concurso atual
    # --------------------------------------------------------

    try:

        current = fetch_latest()

    except Exception as e:

        raise HTTPException(
            status_code=502,
            detail=f"Não foi possível descobrir o concurso atual: {str(e)}"
        )

    ultimo_concurso = current["concurso"]

    # Guarda o último no cache
    history_cache[ultimo_concurso] = {
        "data": current,
        "timestamp": time.time()
    }

    # --------------------------------------------------------
    # Lista de concursos
    # --------------------------------------------------------

    concursos = [
        ultimo_concurso - i
        for i in range(limit)
        if ultimo_concurso - i > 0
    ]

    resultados = []

    # --------------------------------------------------------
    # Pega o que já está no cache
    # --------------------------------------------------------

    faltantes = []

    agora = time.time()

    for numero in concursos:

        cached = history_cache.get(numero)

        if cached:

            idade = agora - cached["timestamp"]

            if idade < CACHE_HISTORY_SECONDS:

                resultados.append(
                    cached["data"]
                )

                continue

        faltantes.append(numero)

    # --------------------------------------------------------
    # Busca faltantes em paralelo
    # --------------------------------------------------------

    if faltantes:

        # Poucos workers para não sobrecarregar
        # o Render gratuito.
        workers = min(5, len(faltantes))

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

            for future in as_completed(tarefas):

                numero = tarefas[future]

                try:

                    resultado = future.result()

                    history_cache[numero] = {
                        "data": resultado,
                        "timestamp": time.time()
                    }

                    resultados.append(resultado)

                except Exception as e:

                    print(
                        f"[HISTORY] Concurso {numero}: {e}"
                    )

    # --------------------------------------------------------
    # Ordenação
    # --------------------------------------------------------

    resultados = sorted(
        resultados,
        key=lambda x: x.get("concurso", 0),
        reverse=True
    )

    # Remove duplicados
    unicos = {}

    for item in resultados:

        concurso_num = item["concurso"]

        unicos[concurso_num] = item

    resultados = list(
        unicos.values()
    )

    resultados.sort(
        key=lambda x: x["concurso"],
        reverse=True
    )

    resultados = resultados[:limit]

    # --------------------------------------------------------
    # Retorno
    # --------------------------------------------------------

    return {
        "concursoAtual": ultimo_concurso,
        "total": len(resultados),
        "resultados": resultados
    }


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    index_path = os.path.join(
        STATIC_DIR,
        "index.html"
    )

    if os.path.exists(index_path):

        return FileResponse(
            index_path
        )

    return {
        "app": "Lotofácil Monitor",
        "version": APP_VERSION,
        "status": "online"
    }


# ============================================================
# SERVIR ARQUIVOS ESTÁTICOS
# ============================================================

@app.get("/favicon.ico")
def favicon():

    favicon_path = os.path.join(
        STATIC_DIR,
        "favicon.ico"
    )

    if os.path.exists(favicon_path):

        return FileResponse(
            favicon_path
        )

    raise HTTPException(
        status_code=404
    )


# ============================================================
# START
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
