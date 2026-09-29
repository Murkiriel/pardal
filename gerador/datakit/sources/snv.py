"""DNIT — Sistema Nacional de Viação: rotas com km (PolylineM) e base geométrica.

Compartilhamento público do DNIT (Nextcloud), lido por WebDAV. Baixa só a versão mais
nova de cada pasta, e só se ainda não estiver em raw_dir.
"""
from __future__ import annotations

import os
import re
from typing import Tuple

import requests

from datakit.sources._http import UA, com_retentativa

SHARE = "https://servicos.dnit.gov.br/dnitcloud/public.php/webdav/"
TOKEN = "oTpPRmYs5AAdiNr"
ROTAS = "SNV Rotas (2015-Atual) (SHP)/"
BASES = "SNV Bases Geométricas (2013-Atual) (SHP)/"


def _latest(folder: str, pattern: str) -> str:
    return com_retentativa(lambda: _latest_once(folder, pattern), "SNV " + folder)


def _latest_once(folder: str, pattern: str) -> str:
    r = requests.request("PROPFIND", SHARE + requests.utils.quote(folder), auth=(TOKEN, ""),
                         headers={**UA, "Depth": "1"}, timeout=120)
    r.raise_for_status()
    names = re.findall(r"<d:href>[^<]*/([^/<]+\.zip)</d:href>", r.text)
    names = [requests.utils.unquote(n) for n in names if re.search(pattern, requests.utils.unquote(n))]
    if not names:
        raise RuntimeError(f"nenhum zip em {folder}")
    return max(names)


def _fetch(folder: str, name: str, dest: str) -> None:
    com_retentativa(lambda: _fetch_once(folder, name, dest), "SNV " + name)


def _fetch_once(folder: str, name: str, dest: str) -> None:
    with requests.get(SHARE + requests.utils.quote(folder + name), auth=(TOKEN, ""), headers=UA,
                      stream=True, timeout=600) as r:
        r.raise_for_status()
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        os.replace(tmp, dest)


_ROUTES: dict = {}


def routes(raw_dir: str):
    """Rotas do SNV (SnvRoutes), carregadas uma vez por execução; None se o SNV falhar."""
    if raw_dir not in _ROUTES:
        from datakit.common.lrs import SnvRoutes
        try:
            _ROUTES[raw_dir] = SnvRoutes.from_zip(ensure(raw_dir)[0])
        except Exception as e:  # noqa: BLE001 — sem SNV, só fica sem o sentido
            from datakit import falhas
            falhas.registrar("SNV (rotas)", e)
            _ROUTES[raw_dir] = None
    return _ROUTES[raw_dir]


def ensure(raw_dir: str) -> Tuple[str, str, str]:
    """(caminho do zip das rotas, caminho do zip da base, versão ex. '202607A')."""
    os.makedirs(raw_dir, exist_ok=True)
    rota = _latest(ROTAS, r"^rota_\d{6}[A-Z]\.zip$")
    version = rota[len("rota_"):-len(".zip")]
    base = _latest(BASES, r"^\d{6}[A-Z]\.zip$")
    paths = []
    for folder, name, local in ((ROTAS, rota, f"snv_rota_{version}.zip"),
                                (BASES, base, f"snv_base_{base[:-4]}.zip")):
        path = os.path.join(raw_dir, local)
        if not os.path.exists(path):
            print(f"[snv] baixando {name}")
            _fetch(folder, name, path)
        paths.append(path)
    return paths[0], paths[1], version
