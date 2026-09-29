"""São Paulo (capital) — limites pela classificação viária da CET, camada do GeoSampa.

Via de Trânsito Rápido -> 70, Arterial -> 50; coletoras e locais ficam de fora. Cada
segmento vira pontos a cada ~150 m.
"""
from __future__ import annotations

from typing import List

from datakit.common.geo import sample_polyline
from datakit.common.model import Limit
from datakit.sources._http import get_json

WFS = "http://wms.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/ows"
LAYER = "geoportal:classificacao_viaria_cet"
CLASS_LIMIT = {"VTR": 70, "Arterial": 50}
SAMPLE_M = 150.0
PAGE = 5000


def features_to_limits(features: list) -> List[Limit]:
    """GeoJSON (LineString/MultiLineString em lng,lat) -> pontos de limite. Puro, testado."""
    out: List[Limit] = []
    for f in features:
        kmh = CLASS_LIMIT.get((f.get("properties") or {}).get("dc_tipo_classificacao_viaria"))
        geom = f.get("geometry") or {}
        if kmh is None:
            continue
        parts = geom.get("coordinates") or []
        if geom.get("type") == "LineString":
            parts = [parts]
        for line in parts:
            pts = [(lat, lng) for lng, lat in line]
            for lat, lng in sample_polyline(pts, SAMPLE_M):
                out.append(Limit(round(lat, 6), round(lng, 6), kmh, "CET-SP"))
    return out


def load_limits() -> List[Limit]:
    out: List[Limit] = []
    start = 0
    while True:
        gj = get_json(WFS, params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": LAYER, "outputFormat": "application/json", "srsName": "EPSG:4326",
            "count": PAGE, "startIndex": start,
            "CQL_FILTER": "dc_tipo_classificacao_viaria IN ('VTR','Arterial')",
        })
        feats = gj.get("features", [])
        out.extend(features_to_limits(feats))
        if len(feats) < PAGE:
            break
        start += PAGE
    return out
