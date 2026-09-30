"""DER-PE — tabela HTML "Equipamentos fiscalização de velocidade" (rodovia estadual PE).
Sem limite; source="DER-PE".
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from datakit.contexto import Carga, Contexto
from datakit.common import Camera, CameraKind, Limit, in_bbox
from datakit.sources._http import get_bytes, in_br, to_float

URL = ("https://www.der.pe.gov.br/transito/recursos-de-infracao/"
       "35-transito/1392-equipamentos-velocidade")


def load(raw_dir: str, bbox: Optional[Tuple[float, float, float, float]] = None
         ) -> Tuple[List[Camera], List[Limit]]:
    html = get_bytes(URL).decode("utf-8", "replace")
    out: List[Camera] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if len(cells) < 3:
            continue
        m = re.search(r"(-?\d{1,2}[.,]\d{3,})\s*,\s*(-?\d{1,2}[.,]\d{3,})", cells[-1])
        if not m:
            continue
        lat, lng = to_float(m.group(1)), to_float(m.group(2))
        if lat is None or lng is None or not in_br(lat, lng) or not in_bbox(bbox, lat, lng):
            continue
        out.append(Camera(round(lat, 6), round(lng, 6), CameraKind.FIXED, None, "DER-PE", True))
    return out, []


def carregar(ctx: Contexto) -> Carga:
    """Contrato das fontes (datakit/contexto.py)."""
    return Carga(*load(ctx.raw_dir))
