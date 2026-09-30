"""Belo Horizonte (BHTrans) — equipamentos de fiscalização eletrônica.

CKAN da PBH, dataset `fiscalizacao-eletronica`, CSV mensal (CC-BY). Geometria em WKT
"POINT (e n)" no SIRGAS 2000 / UTM 23S (EPSG:31983). Controlador de velocidade -> FIXED
(com VELOCIDADE_REGULAMENTAR), detector de avanço de semáforo -> RED_LIGHT; invasão de faixa
exclusiva e conversão proibida ficam de fora (não são aviso de velocidade nem de sinal).
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import List, Optional

from datakit.context import SourceData, BuildContext
from datakit.common.geo import utm_to_latlng
from datakit.common.model import Camera, CameraKind

from datakit.sources._http import get_bytes_curl, in_br, newest

CKAN = "https://dados.pbh.gov.br/api/3/action/package_show?id=fiscalizacao-eletronica"
_POINT = re.compile(r"POINT\s*\(\s*([\d.]+)\s+([\d.]+)\s*\)", re.I)


def _kind(kind_text: str) -> Optional[CameraKind]:
    t = kind_text.lower()
    if "velocidade" in t:
        return CameraKind.FIXED
    if "avan" in t and "sem" in t:
        return CameraKind.RED_LIGHT
    return None


def parse(text: str) -> List[Camera]:
    out: List[Camera] = []
    for r in csv.DictReader(io.StringIO(text), delimiter=";"):
        kind = _kind(r.get("DESC_TIPO_CONTROLADOR_TRANSITO") or "")
        m = _POINT.search(r.get("GEOMETRIA") or "")
        if kind is None or m is None:
            continue
        lat, lng = utm_to_latlng(float(m.group(1)), float(m.group(2)), 23, south=True)
        if not in_br(lat, lng):
            continue
        v = (r.get("VELOCIDADE_REGULAMENTAR") or "").strip()
        kmh = int(v) if v.isdigit() and 20 <= int(v) <= 130 and kind == CameraKind.FIXED else None
        out.append(Camera(round(lat, 6), round(lng, 6), kind, kmh, "BHTRANS", True))
    return out


def load(raw_dir: str, bbox=None):
    resources = json.loads(get_bytes_curl(CKAN))["result"]["resources"]
    res = newest(resources, "CSV", "fiscalizacao_eletronica")
    return parse(get_bytes_curl(res["url"]).decode("utf-8", "replace")), []


def fetch(ctx: BuildContext) -> SourceData:
    """Contrato das fontes (datakit/context.py)."""
    return SourceData(*load(ctx.raw_dir))
