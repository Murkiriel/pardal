"""Fiscalização eletrônica municipal das capitais que publicam com coordenada:
João Pessoa/PB (PMJP), Fortaleza/CE (PMF), Recife/PE (PCR), Curitiba/PR (CURITIBA, desde
2026-10-05: a página de fiscalização eletrônica da Setran, que traz a coordenada de cada ponto).
FIXED, e RED_LIGHT no ponto de Curitiba que só fiscaliza o sinal; `active` do status quando existe.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import List, Optional, Tuple

from datakit.context import SourceData, BuildContext
from datakit.common import Camera, CameraKind, Limit, in_bbox
from datakit.sources._http import get_bytes, in_br, to_float

PMJP_FS = ("https://services7.arcgis.com/KTW4hifejtFfNNYu/arcgis/rest/services/"
           "MEDIDORES_DE_VELOCIDADE_24/FeatureServer/0/query")
FORTALEZA_GEOJSON = ("https://dados.fortaleza.ce.gov.br/dataset/c773f0cc-7ea0-44be-a6dc-41a4e485ce06/"
                     "resource/ab407f64-1635-480f-8bc5-bd8730896d11/download/"
                     "dadosabertos_equipfisceletronica.geojson")
RECIFE_CSV = ("https://dados.recife.pe.gov.br/dataset/9acffb30-ed06-4b16-84ff-710478f18760/"
              "resource/36c2b47b-f439-4895-8b65-3f3dda36a4a7/download/"
              "lista-de-equipamentos-de-fiscalizacao-de-transito.csv")

CURITIBA_PAGE = "https://transito.curitiba.pr.gov.br/fiscalizacaoeletronica"
_CWB_ITEM = re.compile(r'<div class="item')
_CWB_POINT = re.compile(r"verNoMapa\(&#39;(-?[\d.]+)&#39;,\s*&#39;(-?[\d.]+)&#39;\)")
_CWB_TITLE = re.compile(r"title='([^']+)'")
_CWB_LIMIT = re.compile(r"icn-velocidade-(\d{2,3})")

_SPEED = re.compile(r"(\d{2,3})")


def _kmh(raw) -> Optional[int]:
    m = _SPEED.search(str(raw or ""))
    return int(m.group(1)) if m and 20 <= int(m.group(1)) <= 130 else None


def _pmjp(bbox) -> List[Camera]:
    from datakit.sources._arcgis import query_all
    out: List[Camera] = []
    for ft in query_all(PMJP_FS, {"where": "1=1", "outFields": "KM_H", "outSR": 4326}):
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


def parse_curitiba(html: str, bbox=None) -> List[Camera]:
    """Itens da página da Setran: o ponto vem no `verNoMapa('lat','lng')` de cada um, o que ele
    fiscaliza nos títulos dos ícones e o limite na classe `icn-velocidade-NN`. Velocidade controlada
    -> FIXED; só avanço de sinal -> RED_LIGHT; o resto (conversão proibida, faixa exclusiva sozinha)
    fica de fora, como no BH. A página só lista equipamento em operação."""
    out: List[Camera] = []
    for item in _CWB_ITEM.split(html)[1:]:
        m = _CWB_POINT.search(item)
        if m is None:
            continue
        lat, lng = to_float(m.group(1)), to_float(m.group(2))
        if lat is None or lng is None or not in_br(lat, lng) or not in_bbox(bbox, lat, lng):
            continue
        does = set(_CWB_TITLE.findall(item))
        if "Velocidade controlada" in does:
            lim = _CWB_LIMIT.search(item)
            out.append(Camera(round(lat, 6), round(lng, 6), CameraKind.FIXED,
                              _kmh(lim.group(1)) if lim else None, "CURITIBA", True))
        elif "Avanço de sinal" in does:
            out.append(Camera(round(lat, 6), round(lng, 6), CameraKind.RED_LIGHT, None, "CURITIBA", True))
    return out


def _curitiba(bbox) -> List[Camera]:
    return parse_curitiba(get_bytes(CURITIBA_PAGE).decode("utf-8", "replace"), bbox)


def load(raw_dir: str, bbox: Optional[Tuple[float, float, float, float]] = None
         ) -> Tuple[List[Camera], List[Limit]]:
    cams: List[Camera] = []
    for city, fn in (("João Pessoa", _pmjp), ("Fortaleza", _fortaleza), ("Recife", _recife),
                     ("Curitiba", _curitiba)):
        try:
            cams += fn(bbox)
        except Exception as e:  # noqa: BLE001 — uma cidade fora não derruba as outras
            from datakit import failures
            failures.record(f"municipal ({city})", e)
    return cams, []


def fetch(ctx: BuildContext) -> SourceData:
    """Contrato das fontes (datakit/context.py)."""
    return SourceData(*load(ctx.raw_dir))
