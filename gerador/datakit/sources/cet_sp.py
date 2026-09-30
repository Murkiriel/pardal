"""São Paulo (capital) — CET.

Limites: classificação viária da CET, camada do GeoSampa. Via de Trânsito Rápido -> 70,
Arterial -> 50; coletoras e locais ficam de fora. Cada segmento vira pontos a cada ~150 m.

Radares: "Locais fiscalizados" da página Fiscalização Eletrônica do Trânsito da CET, um
relatório Power BI público atualizado todo dia (lido por datakit/sources/_powerbi.py). Só os
ativos (sem data de desativação) e só os que fiscalizam velocidade (código V -> FIXED, com o
limite de veículo leve) ou avanço de sinal (A -> RED_LIGHT); rodízio, faixa exclusiva,
conversão proibida etc. ficam de fora. Sentido: "(CENTRO/BAIRRO)" ou "(BAIRRO/CENTRO)" na
descrição vira o rumo que se afasta (ou se aproxima) do marco zero; sem isso (pares de lugares
como "RAPOSO/MARGINAL"), hint "*". Nos dois casos o build só publica o sentido quando o radar
está claramente sobre uma pista de mão única do OSM (osm_pbf.resolve_probes).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from datakit.contexto import Carga, Contexto
from datakit.common import Camera, CameraKind, in_bbox
from datakit.common.geo import bearing_deg, haversine_m, sample_polyline
from datakit.common.model import Limit
from datakit.sources import _powerbi
from datakit.sources._http import get_json, in_br, to_float

WFS = "http://wms.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/ows"
LAYER = "geoportal:classificacao_viaria_cet"
CLASS_LIMIT = {"VTR": 70, "Arterial": 50}
SAMPLE_M = 150.0
PAGE = 5000


def features_to_limits(features: list) -> List[Limit]:
    """GeoJSON (LineString/MultiLineString em lng,lat) -> pontos de limite. Puro, testado."""
    out: List[Limit] = []
    for f in features:
        kmh = CLASS_LIMIT.get(str((f.get("properties") or {}).get("dc_tipo_classificacao_viaria") or ""))
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


PBI_VIEW = ("https://app.powerbi.com/view?r=eyJrIjoiZDhiOTU2NmMtMjVlMS00ZjY2LWE1NGItYWUzYjAyYmRhNmU3IiwidCI6"
            "ImIwODI2Nzg2LTZmNjktNGVjZC1iZmEyLTYyMmRhYTJiMTlhZCJ9")
PBI_API = "https://wabi-brazil-south-b-primary-api.analysis.windows.net"
PBI_TABLE = "tblFiscalizacaoEletronica"
PBI_COLS = ["CÓDIGO LOCAL", "LATITUDE", "LONGITUDE", "DESCRIÇÃO DO LOCAL", "ENQUADRAMENTOS",
            "VELOCIDADE", "DESATIVAÇÃO"]
MARCO_ZERO = (-23.550333, -46.633944)   # Praça da Sé
RADIAL_MIN_M = 1500.0                     # perto do centro, "sair do centro" não define rumo
_CB = re.compile(r"\(\s*(CENTRO|C)\s*/\s*(BAIRRO|B)\s*\)", re.I)
_BC = re.compile(r"\(\s*(BAIRRO|B)\s*/\s*(CENTRO|C)\s*\)", re.I)


def centro_bairro_hint(desc: str, lat: float, lng: float) -> Optional[str]:
    """'(CENTRO/BAIRRO)' -> rumo que se afasta do marco zero; '(BAIRRO/CENTRO)' -> que se
    aproxima; como heading_hint '@<graus>'. None sem o par ou perto demais do centro."""
    out = _CB.search(desc or "")
    inn = _BC.search(desc or "")
    if bool(out) == bool(inn) or haversine_m(MARCO_ZERO, (lat, lng)) < RADIAL_MIN_M:
        return None
    b = bearing_deg(MARCO_ZERO, (lat, lng)) if out else bearing_deg((lat, lng), MARCO_ZERO)
    return f"@{b:.0f}"


def _enforces(row: Dict) -> Optional[CameraKind]:
    codes = {c.strip().upper() for c in str(row.get("ENQUADRAMENTOS") or "").split(",")}
    if "V" in codes:
        return CameraKind.FIXED
    if "A" in codes:
        return CameraKind.RED_LIGHT
    return None


def rows_to_deactivated(rows: List[Dict]) -> List[Tuple[float, float]]:
    """Locais desativados que fiscalizavam velocidade ou avanço de sinal. Puro, testado."""
    out = []
    for r in rows:
        lat, lng = to_float(r.get("LATITUDE")), to_float(r.get("LONGITUDE"))
        if r.get("DESATIVAÇÃO") and _enforces(r) and lat is not None and lng is not None and in_br(lat, lng):
            out.append((round(lat, 6), round(lng, 6)))
    return out


def rows_to_cameras(rows: List[Dict], bbox: Optional[Tuple[float, float, float, float]] = None
                    ) -> List[Camera]:
    """Linhas da tabela da CET -> radares ativos de velocidade/avanço de sinal. Puro, testado."""
    out: List[Camera] = []
    for r in rows:
        if r.get("DESATIVAÇÃO"):
            continue
        kind = _enforces(r)
        if kind is None:
            continue
        lat, lng = to_float(r.get("LATITUDE")), to_float(r.get("LONGITUDE"))
        if lat is None or lng is None or not in_br(lat, lng) or not in_bbox(bbox, lat, lng):
            continue
        m = re.match(r"\s*(\d{2,3})", str(r.get("VELOCIDADE") or ""))
        limit = int(m.group(1)) if kind == CameraKind.FIXED and m and 20 <= int(m.group(1)) <= 130 else None
        hint = centro_bairro_hint(r.get("DESCRIÇÃO DO LOCAL") or "", lat, lng) or "*"
        out.append(Camera(round(lat, 6), round(lng, 6), kind, limit, "CET-SP", True, heading_hint=hint))
    return out


CACHE_FILE = "cet_sp_locais.json"


def _rows_with_fallback(raw_dir: str) -> List[Dict]:
    """A tabela da CET pelo Power BI; guarda a leitura boa em data/raw/ e, se o Power BI falhar
    (API não documentada), usa essa cópia e avisa (falhas.avisar). Sem cópia, a falha sobe."""
    import json
    import os
    from datetime import date
    path = os.path.join(raw_dir, CACHE_FILE)
    try:
        key = _powerbi.resource_key(PBI_VIEW)
        rows = _powerbi.query_table(PBI_API, key, _powerbi.model_id(PBI_API, key), PBI_TABLE, PBI_COLS)
        if not rows:
            raise RuntimeError("tabela da CET veio vazia")
    except Exception as e:  # noqa: BLE001
        if not os.path.exists(path):
            raise
        from datakit import falhas
        quando = date.fromtimestamp(os.path.getmtime(path)).isoformat()
        falhas.avisar("CET-SP (radares)", f"Power BI falhou ({type(e).__name__}); usada a cópia de {quando}")
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    os.makedirs(raw_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    return rows


def load(raw_dir: str, bbox: Optional[Tuple[float, float, float, float]] = None
         ) -> Tuple[List[Camera], List[Limit]]:
    return rows_to_cameras(_rows_with_fallback(raw_dir), bbox), []


def carregar(ctx: Contexto) -> Carga:
    """Contrato das fontes (datakit/contexto.py). Vão junto os locais que a CET desativou: o
    build marca inativo o radar do OSM que ficou num deles (model.deactivate_near)."""
    rows = _rows_with_fallback(ctx.raw_dir)
    return Carga(rows_to_cameras(rows), [], rows_to_deactivated(rows))


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
