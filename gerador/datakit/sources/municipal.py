"""Fiscalização eletrônica municipal das capitais que publicam com coordenada:
João Pessoa/PB (PMJP), Fortaleza/CE (PMF), Recife/PE (PCR). Tudo FIXED; `active` do
status quando existe.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import List, Optional, Tuple

from datakit.common import Camera, CameraKind, Limit, in_bbox
from datakit.sources._http import get_bytes, get_json, in_br, to_float

PMJP_FS = ("https://services7.arcgis.com/KTW4hifejtFfNNYu/arcgis/rest/services/"
           "MEDIDORES_DE_VELOCIDADE_24/FeatureServer/0/query")
FORTALEZA_GEOJSON = ("https://dados.fortaleza.ce.gov.br/dataset/c773f0cc-7ea0-44be-a6dc-41a4e485ce06/"
                     "resource/ab407f64-1635-480f-8bc5-bd8730896d11/download/"
                     "dadosabertos_equipfisceletronica.geojson")
RECIFE_CSV = ("https://dados.recife.pe.gov.br/dataset/9acffb30-ed06-4b16-84ff-710478f18760/"
              "resource/36c2b47b-f439-4895-8b65-3f3dda36a4a7/download/"
              "lista-de-equipamentos-de-fiscalizacao-de-transito.csv")

_SPEED = re.compile(r"(\d{2,3})")


def _kmh(raw) -> Optional[int]:
    m = _SPEED.search(str(raw or ""))
    return int(m.group(1)) if m and 20 <= int(m.group(1)) <= 130 else None


def _pmjp(bbox) -> List[Camera]:
    j = get_json(PMJP_FS, params={"where": "1=1", "outFields": "KM_H", "f": "json",
                                  "outSR": 4326, "resultRecordCount": 5000})
    out: List[Camera] = []
    for ft in j.get("features", []):
        g = ft.get("geometry") or {}
        lat, lng = g.get("y"), g.get("x")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        if not in_br(lat, lng) or not in_bbox(bbox, lat, lng):
            continue
        out.append(Camera(round(lat, 6), round(lng, 6), CameraKind.FIXED,
                          _kmh((ft.get("attributes") or {}).get("KM_H")), "PMJP", True))
    return out


def _fortaleza(bbox) -> List[Camera]:
    g = json.loads(get_bytes(FORTALEZA_GEOJSON).decode("utf-8", "replace"))
    out: List[Camera] = []
    for ft in g.get("features", []):
        p = ft.get("properties", {})
        c = (ft.get("geometry") or {}).get("coordinates") or []
        lng, lat = (list(c) + [None, None])[:2]
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        if not in_br(lat, lng) or not in_bbox(bbox, lat, lng):
            continue
        if str(p.get("Finalidade") or "").lower().startswith("contag"):
            continue
        active = p.get("DataDesativacao") in (None, "", 0) and str(p.get("Status") or "").lower() != "desativado"
        out.append(Camera(round(lat, 6), round(lng, 6), CameraKind.FIXED, None, "PMF", active))
    return out


def _recife(bbox) -> List[Camera]:
    text = get_bytes(RECIFE_CSV).decode("utf-8", "replace")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    h = [c.strip().lower() for c in next(reader)]
    la, lo = h.index("latitude"), h.index("longitude")
    vi = h.index("velocidade_fiscalizada") if "velocidade_fiscalizada" in h else None
    out: List[Camera] = []
    for row in reader:
        if len(row) <= max(la, lo):
            continue
        lat, lng = to_float(row[la]), to_float(row[lo])
        if lat is None or lng is None or not in_br(lat, lng) or not in_bbox(bbox, lat, lng):
            continue
        out.append(Camera(round(lat, 6), round(lng, 6), CameraKind.FIXED,
                          _kmh(row[vi]) if vi is not None and vi < len(row) else None, "PCR", True))
    return out


def load(raw_dir: str, bbox: Optional[Tuple[float, float, float, float]] = None
         ) -> Tuple[List[Camera], List[Limit]]:
    cams: List[Camera] = []
    for fn in (_pmjp, _fortaleza, _recife):
        try:
            cams += fn(bbox)
        except Exception as e:  # noqa: BLE001
            print(f"[municipal] {fn.__name__} falhou: {type(e).__name__}: {e}")
    return cams, []
