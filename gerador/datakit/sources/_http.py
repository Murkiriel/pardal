"""HTTP comum às fontes: download, descoberta do recurso mais novo num CKAN, conversão de
número com vírgula e filtro de coordenada dentro do Brasil.
"""
from __future__ import annotations

import io
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
# cada requisição tenta TENTATIVAS vezes, esperando ESPERA_S, 2×ESPERA_S... entre elas. Erro
# que não passa com nova tentativa (404, 403, formato mudou) sobe na hora.
TENTATIVAS = 3
ESPERA_S = 5.0

T = TypeVar("T")


def transitorio(e: BaseException) -> bool:
    """Falha que vale tentar de novo: rede, tempo esgotado, 429 ou 5xx, curl sem resposta."""
    import subprocess
    if isinstance(e, requests.HTTPError):
        code = e.response.status_code if e.response is not None else 0
        return code == 429 or code >= 500
    if isinstance(e, subprocess.CalledProcessError):
        return e.returncode != 22   # 22 = curl --fail com HTTP 4xx
    return isinstance(e, (requests.ConnectionError, requests.Timeout,
                          requests.exceptions.ChunkedEncodingError, ConnectionError, TimeoutError, OSError))


def com_retentativa(fn: Callable[[], T], rotulo: str = "", tentativas: int = TENTATIVAS,
                    espera_s: float = ESPERA_S, dormir: Callable[[float], None] = time.sleep) -> T:
    for i in range(1, tentativas + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — decide abaixo se tenta de novo
            if i == tentativas or not transitorio(e):
                raise
            print(f"[http] {rotulo or 'requisição'}: {type(e).__name__}; nova tentativa em {espera_s * i:.0f} s")
            dormir(espera_s * i)
    raise AssertionError("inalcançável")


def get_bytes_curl(url: str) -> bytes:
    """Via o binário `curl`: alguns portais (PBH) barram o handshake TLS do Python com 403
    qualquer que seja o User-Agent, mas deixam o curl passar."""
    import subprocess
    return com_retentativa(lambda: subprocess.run(
        ["curl", "-sSL", "--fail", "-m", str(HTTP_TIMEOUT), "-A", "Mozilla/5.0", url],
        capture_output=True, check=True).stdout, url)


def _get(url: str, headers: Optional[dict], params: Optional[dict], timeout: float) -> requests.Response:
    r = requests.get(url, headers=headers or UA, timeout=timeout, params=params)
    r.raise_for_status()
    return r


def get_bytes(url: str, headers: Optional[dict] = None, timeout: float = HTTP_TIMEOUT) -> bytes:
    return com_retentativa(lambda: _get(url, headers, None, timeout).content, url)


def get_text(url: str, headers: Optional[dict] = None, timeout: float = HTTP_TIMEOUT) -> str:
    return com_retentativa(lambda: _get(url, headers, None, timeout).text, url)


def get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
             timeout: float = HTTP_TIMEOUT) -> dict:
    return com_retentativa(lambda: _get(url, headers, params, timeout).json(), url)


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
