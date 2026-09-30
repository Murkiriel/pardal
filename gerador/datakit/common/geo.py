"""Geometria pura — sem dependência de OSM/rede."""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

Point = Tuple[float, float]  # (lat, lng)

_EARTH_R = 6_371_000.0


def haversine_m(a: Point, b: Point) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_R * math.asin(math.sqrt(h))


def bearing_deg(a: Point, b: Point) -> float:
    """Rumo de a para b, em graus a partir do norte (0-360, sentido horário)."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angle_diff(a: float, b: float) -> float:
    """Diferença entre dois rumos, 0-180."""
    d = abs(a - b) % 360.0
    return 360.0 - d if d > 180.0 else d


def sample_polyline(pts: List[Point], step_m: float) -> List[Point]:
    """Pontos ao longo da polyline a cada ~step_m, incluindo o primeiro e o último.
    Usado para transformar um *way* do OSM (limite por segmento) em pontos, do mesmo
    jeito que build_limits_municipal.py faz com a classe viária da CET."""
    pts = [p for p in pts if p is not None]
    if len(pts) < 2:
        return list(pts)
    out: List[Point] = [pts[0]]
    carry = 0.0
    for a, b in zip(pts, pts[1:]):
        seg = haversine_m(a, b)
        if seg <= 1e-6:
            continue
        d = step_m - carry
        while d < seg:
            t = d / seg
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            d += step_m
        carry = seg - (d - step_m)
    if haversine_m(out[-1], pts[-1]) > step_m * 0.25:
        out.append(pts[-1])
    return out


def _perp_dist_m(p: Point, a: Point, b: Point) -> float:
    """Distância perpendicular (m) de p à reta a-b, aproximação local equirretangular."""
    latr = math.radians((a[0] + b[0]) / 2)
    mx = 111_320.0 * math.cos(latr)
    my = 111_132.0
    ax, ay = 0.0, 0.0
    bx, by = (b[1] - a[1]) * mx, (b[0] - a[0]) * my
    px, py = (p[1] - a[1]) * mx, (p[0] - a[0]) * my
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 <= 1e-9:
        return math.hypot(px - ax, py - ay)
    cross = abs(dx * (ay - py) - (ax - px) * dy)
    return cross / math.sqrt(seg2)


def rdp(points: List[Point], epsilon_m: float) -> List[Point]:
    """Ramer–Douglas–Peucker: descarta vértices que ficam a menos de epsilon_m da reta que
    liga os extremos do trecho. Reduz o ruído da geometria de um way antes de amostrar."""
    if len(points) < 3:
        return list(points)
    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        d = _perp_dist_m(points[i], points[0], points[-1])
        if d > dmax:
            dmax, idx = d, i
    if dmax <= epsilon_m:
        return [points[0], points[-1]]
    left = rdp(points[: idx + 1], epsilon_m)
    right = rdp(points[idx:], epsilon_m)
    return left[:-1] + right


def in_bbox(bbox: Optional[Tuple[float, float, float, float]], lat: float, lng: float) -> bool:
    """bbox = (min_lat, min_lng, max_lat, max_lng). None => aceita tudo."""
    if bbox is None:
        return True
    mnla, mnlo, mxla, mxlo = bbox
    return mnla <= lat <= mxla and mnlo <= lng <= mxlo


_MPH = 1.609_344


def parse_maxspeed(raw: Optional[str]) -> Optional[int]:
    """Lê o `maxspeed` do OSM: tira prefixo condicional ';'/'@', converte mph,
    exige faixa 5..200. Devolve None para 'none'/'signals'/texto sem número."""
    if not raw:
        return None
    s = str(raw).strip().lower()
    for sep in (";", "@", ","):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
    if s in ("none", "signals", "variable", "walk"):
        return 5 if s == "walk" else None
    mph = "mph" in s
    num = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        elif num:
            break
    if not num:
        return None
    v = int(num)
    if mph:
        v = round(v * _MPH)
    return v if 5 <= v <= 200 else None


def utm_to_latlng(easting: float, northing: float, zone: int, south: bool = True) -> Point:
    """UTM (SIRGAS 2000 / GRS80, ~ WGS84) -> (lat, lng). Fórmula de Krüger até a 4ª ordem —
    erro sub-métrico dentro da zona, suficiente para um radar."""
    a, f = 6378137.0, 1 / 298.257222101
    k0, e0 = 0.9996, 500000.0
    n0 = 10000000.0 if south else 0.0
    n = f / (2 - f)
    A = a / (1 + n) * (1 + n * n / 4 + n ** 4 / 64)
    beta = (n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96,
            n ** 2 / 48 + n ** 3 / 15,
            17 * n ** 3 / 480)
    delta = (2 * n - 2 * n ** 2 / 3 - 2 * n ** 3,
             7 * n ** 2 / 3 - 8 * n ** 3 / 5,
             56 * n ** 3 / 15)
    xi = (northing - n0) / (k0 * A)
    eta = (easting - e0) / (k0 * A)
    xi_p, eta_p = xi, eta
    for j, b in enumerate(beta, start=1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    lat = chi
    for j, d in enumerate(delta, start=1):
        lat += d * math.sin(2 * j * chi)
    lng0 = math.radians(zone * 6 - 183)
    lng = lng0 + math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(lat), math.degrees(lng)
