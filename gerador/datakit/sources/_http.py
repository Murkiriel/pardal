"""HTTP comum às fontes: download, descoberta do recurso mais novo num CKAN, conversão de
número com vírgula e filtro de coordenada dentro do Brasil.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Callable, List, Optional, TypeVar

import requests

UA = {"User-Agent": "Pardal-gerador (dados abertos de radares e limites; github.com/Murkiriel/pardal)"}
HTTP_TIMEOUT = 120

# Caixa do Brasil continental, com folga — (min_lat, min_lng, max_lat, max_lng)
BR_BBOX = (-34.0, -74.5, 6.0, -32.0)

# Portais do governo caem por instantes (conexão encerrada no meio do download, 502/503):
# cada requisição tenta ATTEMPTS vezes, esperando WAIT_S, 2×WAIT_S... entre elas. Erro
# que não passa com nova tentativa (404, 403, formato mudou) sobe na hora.
ATTEMPTS = 3
WAIT_S = 5.0
# Abrir a conexão demora no máximo isto; o download em si pode levar HTTP_TIMEOUT. Um servidor que
# não aceita conexão (fora do ar, ou bloqueando o lugar de onde se roda — o DNIT e o Inmetro não
# respondem fora do Brasil) custa segundos, não 3 × 120 s por requisição.
CONNECT_TIMEOUT = 20
# Servidor que não abriu conexão nem depois das novas tentativas: as próximas requisições a ele
# nesta execução falham na hora (as 27 UFs do Inmetro custavam ~6 min cada fora do Brasil).
_DEAD_HOSTS: set = set()
_SESSION: Optional[requests.Session] = None

T = TypeVar("T")


def session() -> requests.Session:
    """Sessão HTTP do build inteiro: requisições ao mesmo servidor reusam a conexão (as 27 UFs do
    Inmetro, as páginas do ArcGIS, o Power BI). Sem cabeçalhos próprios: cada chamada passa os
    seus, como antes com requests.get."""
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
    return _SESSION


def is_transient(e: BaseException) -> bool:
    """Falha que vale tentar de novo: rede, tempo esgotado, 429 ou 5xx, curl sem resposta."""
    import subprocess
    if isinstance(e, requests.HTTPError):
        code = e.response.status_code if e.response is not None else 0
        return code == 429 or code >= 500
    if isinstance(e, subprocess.CalledProcessError):
        return e.returncode != 22   # 22 = curl --fail com HTTP 4xx
    return isinstance(e, (requests.ConnectionError, requests.Timeout,
                          requests.exceptions.ChunkedEncodingError, ConnectionError, TimeoutError, OSError))


def with_retries(fn: Callable[[], T], label: str = "", attempts: int = ATTEMPTS,
                 wait_s: float = WAIT_S, sleep_fn: Optional[Callable[[float], None]] = None) -> T:
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — decide abaixo se tenta de novo
            if i == attempts or not is_transient(e):
                raise
            print(f"[http] {label or 'requisição'}: {type(e).__name__}; nova tentativa em {wait_s * i:.0f} s")
            (sleep_fn or time.sleep)(wait_s * i)
    raise AssertionError("inalcançável")


def _host(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.lower()


def guarded(url: str, fn: Callable[[], T]) -> T:
    """with_retries + desistência por servidor: se o servidor de `url` já não abriu conexão
    nesta execução, falha na hora; se não abrir agora (depois das tentativas), fica marcado."""
    host = _host(url)
    if host in _DEAD_HOSTS:
        raise ConnectionError(f"{host} não abriu conexão antes nesta execução")
    try:
        return with_retries(fn, url)
    except (requests.ConnectionError, requests.Timeout) as e:
        if isinstance(e, (requests.ConnectTimeout, requests.exceptions.ConnectionError)) and \
                not isinstance(e, requests.exceptions.ChunkedEncodingError):
            _DEAD_HOSTS.add(host)
        raise


def get_bytes_curl(url: str) -> bytes:
    """Via o binário `curl`: alguns portais (PBH) barram o handshake TLS do Python com 403
    qualquer que seja o User-Agent, mas deixam o curl passar."""
    import subprocess
    return with_retries(lambda: subprocess.run(
        ["curl", "-sSL", "--fail", "--connect-timeout", str(CONNECT_TIMEOUT), "-m", str(HTTP_TIMEOUT),
         "-A", "Mozilla/5.0", url],
        capture_output=True, check=True).stdout, url)


def _get(url: str, headers: Optional[dict], params: Optional[dict], timeout: float) -> requests.Response:
    r = session().get(url, headers=headers or UA, timeout=(CONNECT_TIMEOUT, timeout), params=params)
    r.raise_for_status()
    return r


def get_bytes(url: str, headers: Optional[dict] = None, timeout: float = HTTP_TIMEOUT) -> bytes:
    return guarded(url, lambda: _get(url, headers, None, timeout).content)


def get_text(url: str, headers: Optional[dict] = None, timeout: float = HTTP_TIMEOUT) -> str:
    return guarded(url, lambda: _get(url, headers, None, timeout).text)


def get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
             timeout: float = HTTP_TIMEOUT) -> dict:
    return guarded(url, lambda: _get(url, headers, params, timeout).json())


def ckan_resources(api_url: str, headers: Optional[dict] = None) -> List[dict]:
    return get_json(api_url, headers=headers)["result"]["resources"]


def newest(resources: List[dict], fmt: str, name_needle: str) -> dict:
    cands = [
        r for r in resources
        if (r.get("format") or "").upper() == fmt
        and name_needle.lower() in (r.get("name") or "").lower()
    ]
    if not cands:
        raise RuntimeError(f"nenhum recurso {fmt} '{name_needle}' no dataset")

    def key(r: dict):
        ts = r.get("last_modified") or r.get("created") or ""
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.min
        m = re.search(r"(\d+)[_-]\d{4}", (r.get("url") or ""))
        return (dt, int(m.group(1)) if m else 0)

    return max(cands, key=key)


def to_float(s) -> Optional[float]:
    try:
        return float(str(s).strip().replace(",", "."))
    except (ValueError, AttributeError, TypeError):
        return None


def in_br(lat: float, lng: float) -> bool:
    return BR_BBOX[0] <= lat <= BR_BBOX[2] and BR_BBOX[1] <= lng <= BR_BBOX[3]
