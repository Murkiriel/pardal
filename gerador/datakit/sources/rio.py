"""Prefeitura do Rio de Janeiro — limites por trecho de rua e radares da cidade.

* Limites: camada "Trechos de Logradouros" (IPP, CC-BY 4.0) com `velocidade_regulamentada`
  em ~85% dos 132 mil trechos. Cada trecho vira pontos nos vértices, com um ponto extra a
  cada 150 m em trecho longo, igual ao que o OSM faz.
* Radares: lista da SMTR/CET-Rio em PDF (endereço, código, limite), geocodificada pelo
  geocodificador de número de porta da própria prefeitura. Endereço sem número ("ACESSO AO
  MERGULHÃO…") não é geocodificado: sem número, o ponto cairia no meio da rua inteira.
"""
from __future__ import annotations

import io
import re
from typing import Iterable, List, Optional, Tuple

from datakit.context import SourceData, BuildContext
from datakit.common.geo import rdp, sample_polyline
from datakit.common.model import Camera, CameraKind, Limit
from datakit.sources._http import get_bytes, get_json, get_text

SEGMENTS_URL = ("https://pgeo3.rio.rj.gov.br/arcgis/rest/services/CadLog/"
                "Trechos_Logradouros/MapServer/0/query")
GEOCODE = ("https://pgeo3.rio.rj.gov.br/arcgis/rest/services/Geocode/Geocode_NP/"
           "GeocodeServer/findAddressCandidates")
LIST_PAGE = "https://cetrio.prefeitura.rio/fiscalizacao-eletronica/"
PAGE = 2000
MAX_GAP_M = 150.0
MIN_SCORE = 95.0


# ── limites ──────────────────────────────────────────────────────────────────

def segment_points(paths: List[List[List[float]]], kmh: int) -> List[Limit]:
    """Geometria ArcGIS (paths de [lng, lat]) -> pontos de limite. Puro, testado."""
    out: List[Limit] = []
    for path in paths:
        pts = [(y, x) for x, y in path]
        if len(pts) < 2:
            continue
        for lat, lng in sample_polyline(rdp(pts, 10.0), MAX_GAP_M) if len(pts) > 1 else pts:
            out.append(Limit(round(lat, 6), round(lng, 6), kmh, "RIO"))
    return out


def load_limits() -> List[Limit]:
    from datakit.sources._arcgis import query_all
    out: List[Limit] = []
    for ft in query_all(SEGMENTS_URL, {"where": "velocidade_regulamentada > 0",
                                       "outFields": "velocidade_regulamentada", "outSR": 4326},
                        page=PAGE, order_by="objectid"):
        kmh = (ft.get("attributes") or {}).get("velocidade_regulamentada")
        paths = (ft.get("geometry") or {}).get("paths") or []
        if isinstance(kmh, int) and 10 <= kmh <= 130:
            out.extend(segment_points(paths, kmh))
    return out


# ── radares ──────────────────────────────────────────────────────────────────

_SECTIONS = (
    ("lombada eletr", CameraKind.FIXED),
    ("medidor velocidade", CameraKind.FIXED),
    ("avanço de sinal", CameraKind.RED_LIGHT),
)
_LINE = re.compile(r"^(?P<addr>.+?)\s*\((?P<code>\d{6,})\)\s*(?:(?P<kmh>\d{2,3})\s+(?!HORAS))?")
_HOURS = re.compile(r"^(\d{1,2}H\b|AS \d|DIAS UTEIS|DE \d|SABADO|DOMINGO|E \d)")
_LABELS = ("sexta", "segunda", "terça", "quarta", "quinta", "sábado", "domingo",
           "pontos fiscalizados", "permitida", "horário", "funcionamento", "velocidade")


def parse_list(lines: Iterable[str]) -> List[Tuple[str, CameraKind, Optional[int]]]:
    """Texto do PDF -> (endereço, tipo, limite). Títulos de seção são as únicas linhas fora
    de maiúsculas; seções que não são de velocidade nem de avanço de sinal (faixa exclusiva,
    carga, conversão…) ficam de fora. Linha de endereço quebrada é juntada até o
    "(código)"; sobras de horário ("21H, SABADO 6H") são ignoradas. Puro, testado."""
    out = []
    kind: Optional[CameraKind] = None
    buf = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if line != line.upper():
            if not low.startswith(_LABELS):
                kind = next((k for n, k in _SECTIONS if low.startswith(n)), None)
                buf = ""
            continue
        if not buf and _HOURS.match(line):
            continue
        buf = f"{buf} {line}".strip()
        m = _LINE.match(buf)
        if m is None:
            if len(buf) > 400:
                buf = ""
            continue
        if kind is not None:
            kmh = int(m.group("kmh")) if m.group("kmh") else None
            out.append((m.group("addr").strip(), kind, kmh if kmh and 20 <= kmh <= 130 else None))
        buf = ""
    return out


def geocode_query(addr: str) -> Optional[str]:
    """'AVENIDA AREIA BRANCA,PROXIMO AO Nº 1672 - SENTIDO X' -> 'AVENIDA AREIA BRANCA 1672'.
    None se não houver número de porta. Puro, testado."""
    street = addr.split(",")[0].strip()
    m = re.search(r"N[º°O]?\.?\s*(\d{1,5})", addr, re.I)
    if not street or not m:
        return None
    return f"{street} {m.group(1)}"


def _geocode(q: str) -> Optional[Tuple[float, float]]:
    j = get_json(GEOCODE, params={"SingleLine": q, "outSR": 4326, "f": "json", "maxLocations": 1}, timeout=60)
    cands = j.get("candidates") or []
    if not cands or cands[0].get("score", 0) < MIN_SCORE:
        return None
    loc = cands[0]["location"]
    return loc["y"], loc["x"]


def _latest_pdf_url() -> str:
    html = get_text(LIST_PAGE, timeout=60)
    urls = re.findall(r"https?://[^\"'<> ]+ListagemSMTR\.pdf", html)
    if not urls:
        raise RuntimeError("lista de radares do Rio não encontrada")
    return max(urls)


def load_cameras() -> List[Camera]:
    import pypdf

    pdf = pypdf.PdfReader(io.BytesIO(get_bytes(_latest_pdf_url())))
    lines = [ln for page in pdf.pages for ln in (page.extract_text() or "").splitlines()]
    out: List[Camera] = []
    seen = {}
    for addr, kind, kmh in parse_list(lines):
        q = geocode_query(addr)
        if q is None:
            continue
        if q not in seen:
            seen[q] = _geocode(q)
        p = seen[q]
        if p is not None:
            out.append(Camera(round(p[0], 6), round(p[1], 6), kind, kmh, "RIO", True))
    return out


def load(raw_dir: str, bbox=None):
    """Interface das fontes oficiais de radar (build.OFFICIAL)."""
    return load_cameras(), []


def fetch(ctx: BuildContext) -> SourceData:
    """Contrato das fontes (datakit/context.py)."""
    return SourceData(*load(ctx.raw_dir))
