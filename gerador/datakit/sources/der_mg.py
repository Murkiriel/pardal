"""DER-MG — "Localização dos radares fixos em operação nas rodovias do Estado de Minas Gerais" (tabela HTML no site do
DER-MG, atualizada pelo órgão; 710 radares em 08/10/2026).

A tabela dá rodovia (MG050, MGC120, LMG800, AMG0150, BR356), km, município e a velocidade ("60 km/h"; com duas, a maior
é a dos veículos leves). Não dá coordenada nem sentido. O km vira ponto:
- BR (delegada ao estado): pelo SNV do DNIT (lrs.SnvRoutes), como as outras fontes;
- MG: pela malha estadual da IDE-Sisema (`ide_0401_mg_rodovias_lin`: segmentos com o código SRE, "050EMG0010", e o km
  inicial e final; da compilação do DNIT para o SNV);
- MGC (estadual sobre trecho de BR planejada): pela linha da BR na mesma malha (segmentos "B") e, sem ela, pelo SNV;
- LMG e AMG (ligações e acessos): não estão em malha nenhuma e ficam de fora.
Todo ponto é conferido pelo município que a tabela informa (malha municipal do IBGE): fora dele por mais de TOWN_GAP_KM,
ou num município que a malha não tem, o radar fica de fora. Medido em 08/10/2026: 600 dos 710 postos na rodovia, 535
dentro ou a até 2 km do município (os de fora são, em boa parte, MGC cujo km não bate com o da BR).
"""
from __future__ import annotations

import html
import io
import os
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from datakit.common import Camera, CameraKind
from datakit.common.lrs import MeasuredLine
from datakit.context import BuildContext, SourceData
from datakit.sources._http import get_bytes, get_json, get_text

PAGE = "https://www.der.mg.gov.br/transportes/localizacao-de-radares-fixos-e-portateis"
NETWORK_URL = ("https://geoserver.meioambiente.mg.gov.br/IDE/ows?service=WFS&version=1.0.0&request=GetFeature"
               "&typeName=IDE%3Aide_0401_mg_rodovias_lin&maxFeatures=10000000&outputFormat=SHAPE-ZIP")
NETWORK_FILE = "ide_mg_rodovias.zip"
TOWNS_URL = ("https://servicodados.ibge.gov.br/api/v3/malhas/estados/31?formato=application/vnd.geo%2Bjson"
             "&intrarregiao=municipio&qualidade=intermediaria")
TOWN_NAMES_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/31/municipios"
SOURCE = "DER-MG"
TOWN_GAP_KM = 2.0
_KM_PER_DEG = 111.0

# Prefixo da tabela -> a letra do código SRE dos segmentos da malha onde ele está.
_NETWORK_KIND = {"MG": "E", "MGC": "B"}
_ROAD = re.compile(r"^(BR|MGC|MG|LMG|AMG)(\d+)$")


@dataclass(frozen=True)
class Row:
    prefix: str        # BR, MG, MGC, LMG, AMG
    number: int
    km: float
    town: str          # dobrado: sem acento, minúsculo
    limit: Optional[int]


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.replace("\xa0", " ")).encode("ascii", "ignore").decode()
    return " ".join(plain.lower().split())


def _cells(row: str) -> List[str]:
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ").strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]


def parse(page: str) -> List[Row]:
    """As linhas da tabela dos radares fixos (a primeira; a dos portáteis vem depois e fica de fora)."""
    table = next((t for t in re.findall(r"<table.*?</table>", page, re.S | re.I) if "RADARES FIXOS" in t.upper()), "")
    out: List[Row] = []
    for tr in re.findall(r"<tr.*?</tr>", table, re.S | re.I):
        c = _cells(tr)
        m = _ROAD.match(c[1].replace(" ", "").upper()) if len(c) >= 5 else None
        if not m:
            continue
        try:
            km = float(c[2].replace(",", "."))
        except ValueError:
            continue
        speeds = [int(v) for v in re.findall(r"(\d{2,3})\s*km/h", c[4]) if 20 <= int(v) <= 130]
        out.append(Row(m.group(1), int(m.group(2)), km, fold(c[3]), max(speeds) if speeds else None))
    return out


def load_network(path: str) -> Dict[Tuple[str, int], List[MeasuredLine]]:
    """Os segmentos da malha da IDE-Sisema por (letra do SRE, número da rodovia), com o km em cada vértice."""
    import shapefile  # pyshp

    z = zipfile.ZipFile(path)
    base = next(n[:-4] for n in z.namelist() if n.lower().endswith(".shp"))
    r = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), shx=io.BytesIO(z.read(base + ".shx")),
                         dbf=io.BytesIO(z.read(base + ".dbf")), encoding="latin-1")
    lines: Dict[Tuple[str, int], List[MeasuredLine]] = defaultdict(list)
    for rec, shp in zip(r.iterRecords(), r.iterShapes()):
        d = rec.as_dict()
        code = str(d.get("codigo_sre") or d.get("codigo_snv") or "").strip()
        m = re.match(r"^(\d{3})([A-Z])MG", code)
        k0, k1 = d.get("quilometra"), d.get("quilometr0")
        if not m or not isinstance(k0, (int, float)) or not isinstance(k1, (int, float)) or k0 == k1:
            continue
        parts = list(shp.parts) + [len(shp.points)]
        for a, b in zip(parts, parts[1:]):
            pts = [(y, x) for x, y in shp.points[a:b]]
            if len(pts) >= 2:
                lines[(m.group(2), int(m.group(1)))].append(MeasuredLine.from_extent(pts, k0, k1))
    return dict(lines)


def _locate(row: Row, network, routes) -> Optional[Tuple[float, float]]:
    if row.prefix == "BR":
        return routes.locate(row.number, "MG", row.km) if routes is not None else None
    for line in network.get((_NETWORK_KIND.get(row.prefix, ""), row.number), []):
        p = line.at(row.km)
        if p is not None:
            return p
    if row.prefix == "MGC" and routes is not None:
        return routes.locate(row.number, "MG", row.km)
    return None


def place(rows: List[Row], network, routes, towns: Dict[str, object]) -> Tuple[List[Camera], Dict[str, int]]:
    """(radares, quantos ficaram de fora por motivo: "no road", "town not found", "out of town")."""
    from shapely.geometry import Point

    out: List[Camera] = []
    dropped: Counter = Counter()
    for row in rows:
        p = _locate(row, network, routes)
        if p is None:
            dropped["no road"] += 1
            continue
        town = towns.get(row.town)
        if town is None:
            dropped["town not found"] += 1
            continue
        if town.distance(Point(p[1], p[0])) * _KM_PER_DEG > TOWN_GAP_KM:  # type: ignore[attr-defined]
            dropped["out of town"] += 1
            continue
        out.append(Camera(round(p[0], 6), round(p[1], 6), CameraKind.FIXED, row.limit, SOURCE, True))
    return out, dict(dropped)


def _ensure_network(raw_dir: str) -> str:
    path = os.path.join(raw_dir, NETWORK_FILE)
    if not os.path.exists(path):
        os.makedirs(raw_dir, exist_ok=True)
        data = get_bytes(NETWORK_URL)
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
    return path


def load_towns() -> Dict[str, object]:
    """{nome dobrado: polígono} dos municípios de MG (malha e nomes do IBGE)."""
    from shapely.geometry import shape

    names = {str(m["id"]): fold(m["nome"]) for m in get_json(TOWN_NAMES_URL)}
    mesh = get_json(TOWNS_URL)
    return {names[str(f["properties"]["codarea"])]: shape(f["geometry"]).buffer(0)
            for f in mesh.get("features", []) if str(f.get("properties", {}).get("codarea")) in names}


def fetch(ctx: BuildContext) -> SourceData:
    """Contrato das fontes (datakit/context.py)."""
    rows = parse(get_text(PAGE))
    cams, dropped = place(rows, load_network(_ensure_network(ctx.raw_dir)), ctx.snv_routes(), load_towns())
    print(f"[der_mg] {len(rows)} radares na tabela, {len(cams)} postos na rodovia; de fora: {dropped}")
    return SourceData(cams)
