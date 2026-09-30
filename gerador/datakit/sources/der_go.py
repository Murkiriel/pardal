"""DER-GO / GOINFRA — FeatureServer ArcGIS "Controle de Substituição de Radares"
(rodovia estadual de Goiás).

Já traz LATITUDE/LONGITUDE e VELOCIDADE ("60 km/h"). source="DER-GO".
SENTIDO (Crescente/Decrescente) vira direction_deg pela malha rodoviária estadual da GOINFRA
(trechos por SRE com km inicial/final, desenhados do inicial para o final: medido em 1.002
de 1.010 radares, o km do radar cai a 4 m da posição na mediana). Ver common/direction.py.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from datakit.context import SourceData, BuildContext
from datakit.common import Camera, CameraKind, Limit, in_bbox
from datakit.common.lrs import MeasuredLine, parse_km
from datakit.common.direction import direction_on, parse_increasing
from datakit.sources._http import in_br

_BASE = "https://services2.arcgis.com/7dQGISjrwMAayhWe/arcgis/rest/services/"
FS = _BASE + "Controle_de_Subst_de_Radares/FeatureServer/0/query"
NETWORK_URL = _BASE + "MalhaEstadual_gdb/FeatureServer/0/query"
SNAP_M = 150.0


def _pages(url: str, fields: str, geometry: bool) -> List[dict]:
    from datakit.sources._arcgis import query_all
    return query_all(url, {"where": "1=1", "outFields": fields, "outSR": 4326,
                           "returnGeometry": "true" if geometry else "false"})


def load_network() -> Dict[str, List[MeasuredLine]]:
    """Trechos da malha estadual por SRE (ex. '070EGO0170'), com km em cada vértice."""
    lines: Dict[str, List[MeasuredLine]] = defaultdict(list)
    for ft in _pages(NETWORK_URL, "sre,km_inicial,km_final", True):
        a = ft.get("attributes", {})
        k0, k1 = a.get("km_inicial"), a.get("km_final")
        if not isinstance(k0, (int, float)) or not isinstance(k1, (int, float)) or k0 == k1:
            continue
        for path in (ft.get("geometry") or {}).get("paths", []):
            pts = [(p[1], p[0]) for p in path]
            if len(pts) >= 2:
                lines[str(a.get("sre") or "").strip()].append(MeasuredLine.from_extent(pts, k0, k1))
    return dict(lines)


def direction(network: Dict[str, List[MeasuredLine]], sre: str, direction_text: str, km_text: str,
              lat: float, lng: float) -> Optional[int]:
    increasing = parse_increasing(direction_text)
    if increasing is None:
        return None
    best = None
    for line in network.get((sre or "").strip(), []):
        if not line.near_bbox((lat, lng), SNAP_M):
            continue
        km, d = line.project((lat, lng), SNAP_M)
        if d <= SNAP_M and (best is None or d < best[2]):
            best = (line, km, d)
    if best is None:
        return None
    line, km, _ = best
    # O km impresso no radar confirma o sentido em que o trecho foi desenhado; se ele cair
    # do outro lado (trecho desenhado ao contrário), inverte.
    printed = parse_km(km_text or "")
    if printed is not None:
        mirrored = line.m_min + line.m_max - km
        if abs(mirrored - printed) + 0.5 < abs(km - printed):
            increasing = not increasing
    return direction_on(line, km, increasing)


def load(raw_dir: str, bbox: Optional[Tuple[float, float, float, float]] = None
         ) -> Tuple[List[Camera], List[Limit]]:
    try:
        network = load_network()
    except Exception as e:  # noqa: BLE001 — sem a malha, os radares saem sem sentido
        from datakit import failures
        failures.record("DER-GO (malha estadual, sentido)", e)
        network = {}
    out: List[Camera] = []
    for ft in _pages(FS, "LATITUDE,LONGITUDE,VELOCIDADE,SRE,SENTIDO,COMPLEMENT", False):
        a = ft.get("attributes", {})
        lat, lng = a.get("LATITUDE"), a.get("LONGITUDE")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        if not in_br(lat, lng) or not in_bbox(bbox, lat, lng):
            continue
        m = re.search(r"(\d{2,3})", str(a.get("VELOCIDADE") or ""))
        limit = int(m.group(1)) if m and 20 <= int(m.group(1)) <= 130 else None
        d = direction(network, a.get("SRE"), a.get("SENTIDO"), a.get("COMPLEMENT"), lat, lng)
        out.append(Camera(round(lat, 6), round(lng, 6), CameraKind.FIXED, limit, "DER-GO", True,
                          direction_deg=d))
    return out, []


def fetch(ctx: BuildContext) -> SourceData:
    """Contrato das fontes (datakit/context.py)."""
    return SourceData(*load(ctx.raw_dir))
