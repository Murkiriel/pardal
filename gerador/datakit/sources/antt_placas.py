"""ANTT — placas de velocidade máxima das rodovias federais concedidas (dataset "sinalizacao").

KMZ com ~14,7 mil placas: rodovia, UF, sentido (Crescente/Decrescente), lat/lng, velocidade
para leves e pesados, situação. CC-BY. A placa vira limite ao longo da rodovia: projetada na
rota do SNV da própria BR, vale até a próxima placa do mesmo sentido (no máximo CAP_KM), e o
trecho é amostrado a cada STEP_KM. O km impresso na placa é o da concessão (PNV antigo, até
~7 km do SNV atual) — por isso a posição sai da coordenada, não do km.

Onde os dois sentidos têm o mesmo limite, sai um ponto sem sentido. Onde diferem, ou só um
sentido tem placa, sai um ponto por sentido com direction_deg (rumo do trânsito, pela
geometria do SNV; ver common/sentido.py).
"""
from __future__ import annotations

import bisect
import io
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from datakit.common.lrs import SnvRoutes
from datakit.common.model import Limit
from datakit.common.sentido import direction_on
from datakit.sources._http import ckan_resources, get_bytes, in_br, to_float

CKAN = "https://dados.antt.gov.br/api/3/action/package_show?id=sinalizacao"
CAP_KM = 10.0
STEP_KM = 0.2
SNAP_M = 150.0


@dataclass(frozen=True)
class Sign:
    br: int
    uf: str
    increasing: bool
    lat: float
    lng: float
    kmh: int


_CELL = re.compile(r"<td>([^<]*)</td>\s*<td>([^<]*)</td>")


def parse_kml(kml: str) -> List[Sign]:
    out: List[Sign] = []
    for pm in re.findall(r"<Placemark.*?</Placemark>", kml, re.S):
        f = {k.strip().lower(): v.strip() for k, v in _CELL.findall(pm)}
        if f.get("situacao", "").lower() != "ativo":
            continue
        m = re.search(r"(\d{2,3})", f.get("rodovia", ""))
        lat, lng = to_float(f.get("latitude")), to_float(f.get("longitude"))
        kmh = re.sub(r"\D", "", f.get("velocidade", ""))
        sentido = f.get("sentido", "").lower()
        if not m or lat is None or lng is None or not kmh or not in_br(lat, lng):
            continue
        if not (20 <= int(kmh) <= 130) or sentido not in ("crescente", "decrescente"):
            continue
        out.append(Sign(int(m.group(1)), f.get("uf", "").upper(), sentido == "crescente", lat, lng, int(kmh)))
    return out


def load_signs(raw_dir: str) -> List[Sign]:
    res = next(r for r in ckan_resources(CKAN) if (r.get("format") or "").upper() == "KMZ")
    z = zipfile.ZipFile(io.BytesIO(get_bytes(res["url"])))
    kml = z.read(next(n for n in z.namelist() if n.lower().endswith(".kml"))).decode("utf-8", "replace")
    return parse_kml(kml)


def _intervals(kms_vals: List[Tuple[float, int]], increasing: bool) -> List[Tuple[float, float, int]]:
    """Placas de um sentido -> trechos (km_ini, km_fim, limite) onde cada uma vale."""
    s = sorted(kms_vals)
    out = []
    for i, (k, v) in enumerate(s):
        if increasing:
            end = s[i + 1][0] if i + 1 < len(s) else k + CAP_KM
            out.append((k, min(end, k + CAP_KM), v))
        else:
            start = s[i - 1][0] if i > 0 else k - CAP_KM
            out.append((max(start, k - CAP_KM), k, v))
    return out


def _value_at(ivs: List[Tuple[float, float, int]], starts: List[float], km: float) -> Optional[int]:
    """Limite do trecho que contém km (trechos de um sentido não se sobrepõem)."""
    i = bisect.bisect_right(starts, km + 1e-9) - 1
    if i >= 0 and km <= ivs[i][1] + 1e-9:
        return ivs[i][2]
    return None


def limits_from_signs(signs: List[Sign], snv: SnvRoutes) -> List[Limit]:
    per_line: Dict[int, dict] = {}
    for s in signs:
        hit = snv.nearest(s.br, s.uf, (s.lat, s.lng), SNAP_M)
        if hit is None:
            continue
        line, km, _ = hit
        slot = per_line.setdefault(id(line), {"line": line, True: [], False: []})
        slot[s.increasing].append((km, s.kmh))

    out: List[Limit] = []
    for slot in per_line.values():
        line = slot["line"]
        up, down = _intervals(slot[True], True), _intervals(slot[False], False)
        up.sort(), down.sort()
        up_s, down_s = [a for a, _, _ in up], [a for a, _, _ in down]
        spans = [(a, b) for a, b, _ in up + down]
        lo, hi = max(line.m_min, min(a for a, _ in spans)), min(line.m_max, max(b for _, b in spans))
        k = lo
        while k <= hi + 1e-9:
            vu, vd = _value_at(up, up_s, k), _value_at(down, down_s, k)
            p = line.at(k)
            if p is not None and (vu is not None or vd is not None):
                lat, lng = round(p[0], 6), round(p[1], 6)
                if vu is not None and vu == vd:
                    out.append(Limit(lat, lng, vu, "ANTT"))
                else:
                    for v, increasing in ((vu, True), (vd, False)):
                        d = direction_on(line, k, increasing) if v is not None else None
                        if d is not None:
                            out.append(Limit(lat, lng, v, "ANTT", direction_deg=d))
            k += STEP_KM
    return out


def load(raw_dir: str, snv: SnvRoutes) -> List[Limit]:
    return limits_from_signs(load_signs(raw_dir), snv)
