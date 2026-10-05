"""Referência linear: "rodovia + km" <-> coordenada.

`SnvRoutes`: rodovias federais, pelas "SNV Rotas" do DNIT (PolylineM com o km em cada
vértice). Medido contra os 1.774 radares do DNIT (coordenada + km): o km cadastrado cai a
226 m da coordenada na mediana (p75 873 m), então serve para CASAR registros com radares
já conhecidos (status do Inmetro), não para criar radar novo a partir de "rodovia + km".
Os marcos quilométricos do OSM foram testados também (mediana 83 m, mas 25% > 200 m e só
~100 casos fora de SC) e descartados — ver FONTES.md.

Tudo puro (sem rede); o download fica em `datakit.sources.snv`.
"""
from __future__ import annotations

import bisect
import io
import math
import re
import zipfile
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]  # (lat, lng)

# "KM 004" não é rodovia: no Inmetro de SP, "SPA-372/321 KM 004,000" (acesso) virava a rodovia KM-4.
_ROAD = re.compile(r"\b(?!KM)(BR|[A-Z]{2})\s*-?\s*(\d{2,3})\b")


def parse_road(text: str) -> Optional[Tuple[str, int]]:
    """'BR 060', 'BR-060', 'GO-469', 'SP280' -> ('BR', 60) / ('GO', 469). None se não achar."""
    m = _ROAD.search((text or "").upper())
    if not m:
        return None
    return m.group(1), int(m.group(2))


_KM = re.compile(r"KM\s*[:.]?\s*(\d{1,4})(?:\s*\+\s*(\d{1,3})\s*M?|[.,](\d{1,3}))?", re.I)


def parse_km(text: str) -> Optional[float]:
    """'KM 027+244M' -> 27.244, 'KM  181,000' -> 181.0, 'km 12,5' -> 12.5, 'KM 5' -> 5.0."""
    m = _KM.search(text or "")
    if not m:
        return None
    whole = int(m.group(1))
    if m.group(2) is not None:          # +metros
        return whole + int(m.group(2)) / 1000.0
    if m.group(3) is not None:          # fração decimal
        return float(f"{whole}.{m.group(3)}")
    return float(whole)


class MeasuredLine:
    """Polyline com um km (M) por vértice, crescente."""

    def __init__(self, pts: Sequence[Point], ms: Sequence[float]):
        self.pts = list(pts)
        self.ms = list(ms)
        lats = [p[0] for p in self.pts]
        lngs = [p[1] for p in self.pts]
        self.bbox = (min(lats), min(lngs), max(lats), max(lngs))

    def near_bbox(self, p: Point, max_m: float) -> bool:
        """p está a até max_m (metros) da caixa da linha?"""
        from datakit.common.spatial import lat_span_deg, lng_span_deg
        b = self.bbox
        my, mx = lat_span_deg(max_m), lng_span_deg(max_m, p[0])
        return b[0] - my <= p[0] <= b[2] + my and b[1] - mx <= p[1] <= b[3] + mx

    @property
    def m_min(self) -> float:
        return self.ms[0]

    @property
    def m_max(self) -> float:
        return self.ms[-1]

    def at(self, km: float) -> Optional[Point]:
        if not self.ms or km < self.ms[0] - 1e-6 or km > self.ms[-1] + 1e-6:
            return None
        i = bisect.bisect_left(self.ms, km)
        if i <= 0:
            return self.pts[0]
        if i >= len(self.ms):
            return self.pts[-1]
        m0, m1 = self.ms[i - 1], self.ms[i]
        t = 0.0 if m1 - m0 <= 1e-12 else (km - m0) / (m1 - m0)
        a, b = self.pts[i - 1], self.pts[i]
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    def project(self, p: Point, max_m: float = math.inf) -> Tuple[float, float]:
        """(km do ponto mais próximo sobre a linha, distância em metros). Com max_m, pula
        segmentos cuja caixa já está mais longe que isso (bem mais rápido em rota longa)."""
        best = (math.inf, 0.0)
        from datakit.common.spatial import lat_span_deg, lng_span_deg
        bounded = max_m != math.inf
        my, mx = (lat_span_deg(max_m), lng_span_deg(max_m, p[0])) if bounded else (0.0, 0.0)
        for i in range(1, len(self.pts)):
            a, b = self.pts[i - 1], self.pts[i]
            if bounded and not (
                min(a[0], b[0]) - my <= p[0] <= max(a[0], b[0]) + my
                and min(a[1], b[1]) - mx <= p[1] <= max(a[1], b[1]) + mx
            ):
                continue
            t, d = _proj_seg(p, a, b)
            if d < best[0]:
                best = (d, self.ms[i - 1] + (self.ms[i] - self.ms[i - 1]) * t)
        return best[1], best[0]

    def bearing_at(self, km: float, half_km: float = 0.05) -> Optional[float]:
        """Rumo do sentido CRESCENTE do km no ponto km (média de ±half_km, para um vértice
        torto não decidir sozinho). None fora da linha ou num trecho degenerado."""
        from datakit.common.geo import bearing_deg

        a = self.at(max(self.m_min, km - half_km))
        b = self.at(min(self.m_max, km + half_km))
        if a is None or b is None or (abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9):
            return None
        return bearing_deg(a, b)

    @staticmethod
    def from_extent(pts: Sequence[Point], km_start: float, km_end: float) -> "MeasuredLine":
        """Linha sem km por vértice, só com o km das pontas (desenhada de km_start para
        km_end): o km de cada vértice sai do comprimento acumulado."""
        from datakit.common.geo import haversine_m

        pts = list(pts)
        acc = [0.0]
        for p, q in zip(pts, pts[1:]):
            acc.append(acc[-1] + haversine_m(p, q))
        total = acc[-1] or 1.0
        ms = [km_start + (km_end - km_start) * a / total for a in acc]
        if km_end < km_start:
            return MeasuredLine(pts[::-1], ms[::-1])
        return MeasuredLine(pts, ms)

    def slice(self, km0: float, km1: float) -> List[Point]:
        """Geometria entre dois km (km0 < km1), vértices intermediários incluídos."""
        lo, hi = max(km0, self.m_min), min(km1, self.m_max)
        if hi <= lo:
            return []
        out = [self.at(lo)]
        for pt, m in zip(self.pts, self.ms):
            if lo < m < hi:
                out.append(pt)
        out.append(self.at(hi))
        return [p for p in out if p is not None]


def _proj_seg(p: Point, a: Point, b: Point) -> Tuple[float, float]:
    latr = math.radians((a[0] + b[0]) / 2)
    mx, my = 111_320.0 * math.cos(latr), 111_132.0
    bx, by = (b[1] - a[1]) * mx, (b[0] - a[0]) * my
    px, py = (p[1] - a[1]) * mx, (p[0] - a[0]) * my
    seg2 = bx * bx + by * by
    t = 0.0 if seg2 <= 1e-9 else max(0.0, min(1.0, (px * bx + py * by) / seg2))
    return t, math.hypot(px - bx * t, py - by * t)


class SnvRoutes:
    """Eixos principais (tipo 'B') das rotas do SNV, por (BR, UF)."""

    def __init__(self, lines: Dict[Tuple[int, str], List[MeasuredLine]]):
        self.lines = lines

    @staticmethod
    def from_zip(path: str) -> "SnvRoutes":
        import shapefile  # pyshp

        z = zipfile.ZipFile(path)
        base = next(n[:-4] for n in z.namelist() if n.lower().endswith(".shp"))
        r = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), shx=io.BytesIO(z.read(base + ".shx")),
                             dbf=io.BytesIO(z.read(base + ".dbf")), encoding="utf-8")
        code_field = next(f[0] for f in r.fields[1:] if f[0].startswith("vl_codigo"))
        lines: Dict[Tuple[int, str], List[MeasuredLine]] = defaultdict(list)
        for rec, shp in zip(r.iterRecords(), r.iterShapes()):
            key = snv_key(rec[code_field])
            if key is None or not getattr(shp, "m", None):
                continue
            parts = list(shp.parts) + [len(shp.points)]
            for a, b in zip(parts, parts[1:]):
                pts = [(y, x) for x, y in shp.points[a:b]]
                ms = [m if m is not None else math.nan for m in shp.m[a:b]]
                keep = [(p, m) for p, m in zip(pts, ms) if not math.isnan(m)]
                if len(keep) >= 2:
                    keep.sort(key=lambda pm: pm[1])
                    lines[key].append(MeasuredLine([p for p, _ in keep], [m for _, m in keep]))
        return SnvRoutes(dict(lines))

    def locate(self, br: int, uf: str, km: float) -> Optional[Point]:
        for line in self.lines.get((br, uf.upper()), []):
            p = line.at(km)
            if p is not None:
                return p
        return None

    def nearest(self, br: int, uf: str, p: Point, max_m: float = 200.0
                ) -> Optional[Tuple[MeasuredLine, float, float]]:
        """(linha, km, distância) da rota (br, uf) mais perto de p, se a menos de max_m."""
        best = None
        for line in self.lines.get((br, uf.upper()), []):
            if not line.near_bbox(p, max_m):
                continue
            km, d = line.project(p, max_m)
            if d <= max_m and (best is None or d < best[2]):
                best = (line, km, d)
        return best

    def nearest_any(self, uf: str, p: Point, max_m: float = 60.0
                    ) -> Optional[Tuple[int, MeasuredLine, float, float]]:
        """(br, linha, km, distância) da BR da UF mais perto de p, se a menos de max_m."""
        best = None
        uf = uf.upper()
        for (br, u), lines in self.lines.items():
            if u != uf:
                continue
            for line in lines:
                if not line.near_bbox(p, max_m):
                    continue
                km, d = line.project(p, max_m)
                if d <= max_m and (best is None or d < best[3]):
                    best = (br, line, km, d)
        return best


def load_concessions(base_zip: str) -> Dict[Tuple[int, str], List[Tuple[float, float]]]:
    """Trechos da base geométrica do SNV administrados por concessão federal:
    (BR, UF) -> [(km_ini, km_fim)]. Ali o km das placas/medidores é o da concessão
    (PNV antigo), não o do SNV atual — casar por km não vale."""
    import shapefile

    z = zipfile.ZipFile(base_zip)
    base = next(n[:-4] for n in z.namelist() if n.lower().endswith(".shp"))
    r = shapefile.Reader(dbf=io.BytesIO(z.read(base + ".dbf")), encoding="utf-8", encodingErrors="replace")
    out: Dict[Tuple[int, str], List[Tuple[float, float]]] = defaultdict(list)
    for rec in r.iterRecords():
        d = rec.as_dict()
        if "Concess" not in str(d.get("ds_tipo_ad", "")) or d.get("sg_tipo_tr") != "B":
            continue
        try:
            key = (int(d["vl_br"]), str(d["sg_uf"]).upper())
            out[key].append((float(d["vl_km_inic"]), float(d["vl_km_fina"])))
        except (KeyError, ValueError, TypeError):
            continue
    return dict(out)


def in_ranges(ranges: List[Tuple[float, float]], km: float) -> bool:
    return any(a - 1e-6 <= km <= b + 1e-6 for a, b in ranges)


def snv_key(code: str) -> Optional[Tuple[int, str]]:
    """'010BDF202607A' -> (10, 'DF'). Só eixo principal ('B')."""
    m = re.match(r"^(\d{3})B([A-Z]{2})", code or "")
    return (int(m.group(1)), m.group(2)) if m else None
