"""DER-SP — rodovia estadual de São Paulo. Duas bases:
  * Artesp "Recursos Operacionais" (concessões)          -> load_der_sp
  * DER-SP radares.xlsx (rede não concedida, Edital 145)  -> load_der_sp_own
source="DER-SP" nas duas. `active` vem do status/cancelamento.
Sentido: a Artesp dá o sentido nominal da rodovia (Norte/Sul/Leste/Oeste) e o DER-SP as faixas
por sentido ("N-1, S-1" = os dois; "O-1" = só oeste). Viram heading_hint, que o build converte
em direction_deg com a geometria do OSM (common/direction.py).
Nenhuma das duas dá o limite, mas as duas dão rodovia e km ("SP330" + "km 060+550" na Artesp,
"SP 008" + 96.86 no DER), o mesmo cadastro do Inmetro: vão em road_km, e por ele o radar ganha a
velocidade nominal do medidor válido em inmetro_status.py (a situação continua a da fonte).
"""
from __future__ import annotations

import io
import re
from typing import List, Optional, Tuple

import openpyxl

from datakit.context import SourceData, BuildContext
from datakit.common import Camera, CameraKind, Limit, in_bbox
from datakit.common.direction import hint_from_text
from datakit.common.lrs import parse_km, parse_road
from datakit.sources._http import get_bytes, in_br, to_float

ARTESP_XLSX = ("https://dadosabertos.artesp.sp.gov.br/dataset/491d79c5-ee09-4fe8-a3ce-1b425fe60bbe/"
               "resource/8b3212ff-1647-42ad-8a45-39e9934c92e1/download/recursos.xlsx")
DER_SP_OWN_XLSX = ("https://dersp.der.sp.gov.br/WebSite/Arquivos/DadosAbertos/AtivosRodoviarios/"
                   "Radar/radares.xlsx")

_ARTESP_RADAR = re.compile(r"medidor de velocidade|radar fixo|lombada eletr", re.I)
_ARTESP_DEAD = {"Inativo", "Desativado"}


def _artesp(bbox) -> List[Camera]:
    wb = openpyxl.load_workbook(io.BytesIO(get_bytes(ARTESP_XLSX)), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = ws.iter_rows(values_only=True)
    h = [str(c).strip() if c is not None else "" for c in next(rows)]
    di, si = h.index("Desc_Componente"), h.index("Cod_Status")
    la, lo = h.index("Latitude"), h.index("Longitude")
    dir_col = h.index("Sentido") if "Sentido" in h else None
    ri = h.index("Rodovia") if "Rodovia" in h else None
    ki = h.index("Localizacao") if "Localizacao" in h else None
    out: List[Camera] = []
    for row in rows:
        if not _ARTESP_RADAR.search(str(row[di] or "")):
            continue
        lat, lng = to_float(row[la]), to_float(row[lo])
        if lat is None or lng is None or not in_br(lat, lng) or not in_bbox(bbox, lat, lng):
            continue
        active = str(row[si] or "").strip() not in _ARTESP_DEAD
        hint = hint_from_text(row[dir_col]) if dir_col is not None else None
        rk = road_km(row[ri], row[ki]) if ri is not None and ki is not None else None
        out.append(Camera(round(lat, 6), round(lng, 6), CameraKind.FIXED, None, "DER-SP", active,
                          heading_hint=hint, road_km=rk))
    return out


def road_km(rodovia, km) -> Optional[Tuple[Tuple[str, int], float]]:
    """('SP330', 'km 060+550') -> (('SP', 330), 60.55); ('SP 008', 96.86) -> (('SP', 8), 96.86).
    Acesso ('SPA 009/010') ou km ilegível -> None."""
    road = parse_road(str(rodovia or ""))
    if isinstance(km, (int, float)):
        k: Optional[float] = float(km)
    else:
        text = str(km or "")
        k = parse_km(text) if re.search(r"km", text, re.I) else to_float(text)
    return (road, k) if road is not None and k is not None else None


def lanes_hint(faixas: Optional[str]) -> Optional[str]:
    """'O-1' / 'O-1, O-2' -> 'O'; 'N-1, S-1' (os dois sentidos) ou vazio -> None."""
    letters = set(re.findall(r"\b([NSLO])-\d", str(faixas or "")))
    return letters.pop() if len(letters) == 1 else None


def _der_sp_own(bbox) -> List[Camera]:
    wb = openpyxl.load_workbook(io.BytesIO(get_bytes(DER_SP_OWN_XLSX)), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = ws.iter_rows(values_only=True)
    h = [str(c).strip() if c is not None else "" for c in next(rows)]
    ci, cx = h.index("Coordenadas"), h.index("Cancelamento")
    fx = h.index("Faixas") if "Faixas" in h else None
    ri = h.index("Rodovia") if "Rodovia" in h else None
    ki = h.index("Km") if "Km" in h else None
    out: List[Camera] = []
    for row in rows:
        cell = str(row[ci] or "")
        if "," not in cell:
            continue
        a, _, b = cell.partition(",")
        lat, lng = to_float(a), to_float(b)
        if lat is None or lng is None or not in_br(lat, lng) or not in_bbox(bbox, lat, lng):
            continue
        active = row[cx] in (None, "")
        hint = lanes_hint(row[fx]) if fx is not None else None
        rk = road_km(row[ri], row[ki]) if ri is not None and ki is not None else None
        out.append(Camera(round(lat, 6), round(lng, 6), CameraKind.FIXED, None, "DER-SP", active,
                          heading_hint=hint, road_km=rk))
    return out


def load(raw_dir: str, bbox: Optional[Tuple[float, float, float, float]] = None
         ) -> Tuple[List[Camera], List[Limit]]:
    cams: List[Camera] = []
    for fn in (_artesp, _der_sp_own):
        try:
            cams += fn(bbox)
        except Exception as e:  # noqa: BLE001 — uma base fora não derruba a outra
            from datakit import failures
            failures.record(f"DER-SP ({fn.__name__})", e)
    return cams, []


def fetch(ctx: BuildContext) -> SourceData:
    """Contrato das fontes (datakit/context.py)."""
    return SourceData(*load(ctx.raw_dir))
