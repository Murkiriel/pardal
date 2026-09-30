"""Distrito Federal — Detran-DF, camada ArcGIS "Base_DETRAN" (radares e lombadas eletrônicas).

Pública, com lat/lng e velocidade ("30 kmh@30"), mas a última edição é de 2019: por isso o
status passa pelo Inmetro (`inmetro_status`) como as outras fontes sem situação própria.
O cadastro atual do DF está no portal dados.df.gov.br, que bloqueia download automático.
"""
from __future__ import annotations

import re
from typing import List, Optional

from datakit.contexto import Carga, Contexto
from datakit.common.model import Camera, CameraKind
from datakit.sources._arcgis import query_all
from datakit.sources._http import in_br

BASE = "https://services.arcgis.com/4CZwpdWHGNPLU7QQ/arcgis/rest/services/Base_DETRAN/FeatureServer"
LAYERS = (1, 2)  # 1 = radares, 2 = lombadas eletrônicas


def _kmh(raw) -> Optional[int]:
    m = re.search(r"(\d{2,3})", str(raw or ""))
    return int(m.group(1)) if m and 20 <= int(m.group(1)) <= 130 else None


def parse(features: list) -> List[Camera]:
    out: List[Camera] = []
    for ft in features:
        g = ft.get("geometry") or {}
        lat, lng = g.get("y"), g.get("x")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)) or not in_br(lat, lng):
            continue
        a = ft.get("attributes") or {}
        out.append(Camera(round(lat, 6), round(lng, 6), CameraKind.FIXED, _kmh(a.get("Velocidade")),
                          "DETRAN-DF", True))
    return out


def load(raw_dir: str, bbox=None):
    out: List[Camera] = []
    for layer in LAYERS:
        out.extend(parse(query_all(f"{BASE}/{layer}/query",
                                   {"where": "1=1", "outFields": "Velocidade", "outSR": 4326})))
    return out, []


def carregar(ctx: Contexto) -> Carga:
    """Contrato das fontes (datakit/contexto.py)."""
    return Carga(*load(ctx.raw_dir))
