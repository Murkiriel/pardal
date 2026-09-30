"""Extrato regional do OSM (Geofabrik) -> radares + limites.

Extrato inteiro da região baixado de uma vez e filtrado localmente com pyosmium (dispensa o
binário `osmium`). PBF do OSM entra, linhas mescláveis saem.

    from datakit.sources import osm_pbf
    pbf = osm_pbf.ensure_extract("centro-oeste", "data/raw")
    cams, lims = osm_pbf.load(pbf.path, bbox=uf_bbox("GO"))

O que captura:
  * nós  highway=speed_camera  ou  enforcement=*      -> Camera (FIXED/RED_LIGHT)
  * ways com maxspeed                                 -> Limit (geometria amostrada + RDP)
  * relações type=enforcement:
      enforcement=maxspeed|average_speed com from+to  -> SECTION (radar de trecho)
      enforcement=traffic_signals                     -> RED_LIGHT (avanço de sinal)
    Os nós from/to costumam ser marcadores sem tag -> resolvidos numa 2ª passada por id.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple
from urllib.request import urlopen, Request

import osmium
import osmium.filter

from datakit.common.geo import angle_diff, bearing_deg
from datakit.common.infer import class_limit, is_yes, zone_limit
from datakit.common.lrs import _proj_seg
from datakit.common.model import to_dir
from datakit.common.sentido import orient_to_hint
from datakit.common.spatial import lat_span_deg, lng_span_deg
from datakit.sources._http import HTTP_TIMEOUT, UA, com_retentativa
from datakit.common import (
    Camera, CameraKind, Limit, Struct,
    parse_maxspeed, in_bbox, haversine_m, rdp, sample_polyline,
)

GEOFABRIK_BASE = "https://download.geofabrik.de/south-america/brazil"
SAMPLE_M = 0            # legado (sem reamostragem fixa; ver _limit_points). Mantido p/ index.sample_m
RDP_EPSILON_M = 15.0    # simplifica a geometria do way (Douglas-Peucker)
LIMIT_MAX_GAP_M = 1500.0  # só preenche vértices do RDP se ficarem mais longe que isso

# Limite estimado (estimated=1) para vias sem maxspeed — ver common/infer.py. Só a rede
# principal (autoestrada até terciária). `unclassified` (estrada rural, quase sempre de terra)
# ficou de fora: em Goiás eram 75% dos pontos estimados (135 mil km) e é onde 40/60 km/h mais
# se parece com chute. Ruas residenciais, de serviço e living_street também não entram.
_INFER_CLASSES = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link",
}
_MAIN_ROADS = {"trunk", "primary", "secondary"}
_URBAN_WEIGHT = {"residential": 2, "living_street": 2, "tertiary": 2, "tertiary_link": 2, "service": 1}
ESTIMATE_STEP_M = 500.0
URBAN_CELL_DEG = 0.002
URBAN_THRESHOLD = 8

# Sentido dos radares que só têm o sentido nominal (heading_hint) ou nenhum ("*"): pela posição
# do radar sobre uma pista de mão única do OSM (ver resolve_probes).
PROBE_M = 40.0
_PROBE_CELL = 0.002
CONFLICT_M = 6.0
# True: só publica o sentido confirmado pela posição (radar claramente sobre pista de mão única).
# O sentido nominal de SP erra em ~13% dos casos que a posição consegue conferir, e em via de
# mão dupla nada confere; sentido errado faz o radar sumir para quem passa, sentido vazio só
# avisa nos dois sentidos. False volta a aceitar o nominal em via de mão dupla.
POSITION_CONFIRMS = True

# O `direction` dos radares do OSM NÃO entra no Pardal: medido nas BRs (radar a > 15 m do eixo
# do SNV), o lado da pista confirma o sentido em só 38% dos 88 casos (DNIT 97%, ANTT 89%), e a
# menos de 40 m de um radar oficial com sentido, 1 em cada 3 aponta o contrário. Muitos
# mapeadores marcam para onde a câmera olha, não o sentido do trânsito. Ver FONTES.md.
USE_OSM_DIRECTION = False

# Limite SINALIZADO: qualquer via drivable com maxspeed explícito é dado real, incluindo
# rua local. O estimado por classe fica só na rede principal (_INFER_CLASSES, acima).
_LIMIT_CLASSES = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link",
    "unclassified", "residential", "living_street", "service", "road",
}


@dataclass(frozen=True)
class Extract:
    path: str
    region: str
    file_date: str   # Last-Modified da Geofabrik
    sha256: str
    bytes: int


def ensure_extract(region: str, raw_dir: str, refresh: bool = True) -> Extract:
    """<region>-latest.osm.pbf em raw_dir, na versão atual da Geofabrik: com refresh, pergunta
    a data da versão publicada (HEAD) e baixa de novo se for mais nova que a guardada. Sem
    refresh (build --osm-local), usa o arquivo guardado se existir. Se a consulta falhar, usa
    o guardado e registra a falha (o publicar.py barra: os dados podem estar velhos)."""
    os.makedirs(raw_dir, exist_ok=True)
    path = os.path.join(raw_dir, f"{region}-latest.osm.pbf")
    stamp = os.path.join(raw_dir, f"{region}.last-modified")
    url = f"{GEOFABRIK_BASE}/{region}-latest.osm.pbf"
    download = not os.path.exists(path)
    if not download and refresh:
        try:
            remote = com_retentativa(lambda: _remote_last_modified(url), url)
            download = needs_update(_read_or(stamp, ""), remote)
            if not download:
                print(f"[osm_pbf] {region}: versão guardada é a atual ({remote})")
        except Exception as e:  # noqa: BLE001
            from datakit import falhas
            falhas.registrar(f"OSM {region} (conferir versão; usado o arquivo guardado)", e)
    if download:
        print(f"[osm_pbf] baixando {url}")
        last_mod = com_retentativa(lambda: _download(url, path), url)
        with open(stamp, "w") as m:
            m.write(last_mod)
    last_mod = _read_or(stamp, "")
    return Extract(
        path=path, region=region, file_date=last_mod,
        sha256=_sha256(path), bytes=os.path.getsize(path),
    )


def load(pbf_path: str, bbox: Optional[Tuple[float, float, float, float]] = None,
         probes: Optional[List[Camera]] = None, probe_out: Optional[Dict] = None,
         ) -> Tuple[List[Camera], List[Limit], List[Struct]]:
    """probes: radares de outras fontes com heading_hint; para cada um que casar com uma via,
    probe_out[(lat, lng, hint)] = rumo do trânsito (graus)."""
    probe_grid = _probe_grid(probes or [])
    probe_found: Dict[Tuple[float, float, str], List[Tuple[float, float, bool]]] = {}
    fb: Dict[int, Tuple[int, bool]] = {}   # nid do radar 'forward'/'backward' -> (índice, forward?)
    cams: List[Camera] = []
    lims: List[Limit] = []
    structs: List[Struct] = []
    node_pt: dict = {}            # nid -> (lat,lng) de todo nó tagueado (resolve membros de relação)
    cam_idx_by_nid: dict = {}     # nid -> índice em cams (para o 'from' virar SECTION no lugar)
    enf_rels: list = []           # (enforcement, from_nid, to_nid, device_nid, limit)
    wanted: set = set()           # nids de from/to/device p/ resolver coords na 2ª passada
    urban: dict = {}              # célula da grade -> peso das ruas em volta (ver _is_urban)
    pending_main: list = []       # (highway, verts, pts) de tronco/primária/secundária sem lit

    fp = (osmium.FileProcessor(pbf_path)
          .with_locations()
          .with_filter(osmium.filter.KeyFilter("highway", "enforcement", "maxspeed")))

    for o in fp:
        tags = o.tags
        if o.is_node():
            if not (tags.get("highway") == "speed_camera" or "enforcement" in tags):
                continue
            loc = o.location
            if not loc.valid():
                continue
            lat, lng = loc.lat, loc.lon
            node_pt[o.id] = (lat, lng)
            if not in_bbox(bbox, lat, lng):
                continue
            cam_idx_by_nid[o.id] = len(cams)
            dv = (tags.get("direction") or "").strip().lower()
            if USE_OSM_DIRECTION and dv in ("forward", "backward"):
                fb[o.id] = (len(cams), dv == "forward")
            cams.append(Camera(
                lat=lat, lng=lng, kind=_kind(tags), limit_kmh=_cam_limit(tags),
                source="OSM", active=tags.get("disused") != "yes" and tags.get("abandoned") != "yes",
                direction_deg=_cam_direction(tags) if USE_OSM_DIRECTION else None,
            ))
        elif o.is_way():
            hw = tags.get("highway")
            if hw not in _LIMIT_CLASSES:
                continue
            if fb:
                _apply_way_direction(o, fb, cams)
            pts = [(n.location.lat, n.location.lon) for n in o.nodes if n.location.valid()]
            if len(pts) < 2:
                continue
            if probe_grid and hw in _INFER_CLASSES:
                ow = tags.get("oneway", "")
                if ow == "-1":
                    _probe_way(probe_grid, pts[::-1], True, probe_found)
                else:
                    _probe_way(probe_grid, pts, ow in ("yes", "true", "1"), probe_found)
            verts = rdp(pts, RDP_EPSILON_M)

            # ponte / túnel (aviso de viaduto/subpassagem, agora offline)
            struct_kind = _struct_kind(tags)
            if struct_kind is not None:
                a, b = pts[0], pts[-1]
                if in_bbox(bbox, a[0], a[1]) or in_bbox(bbox, b[0], b[1]):
                    structs.append(Struct(a[0], a[1], b[0], b[1], struct_kind))

            w = _URBAN_WEIGHT.get(hw)
            if w:
                for cell in {_cell(lat, lng) for lat, lng in pts}:
                    urban[cell] = urban.get(cell, 0) + w

            lim = parse_maxspeed(tags.get("maxspeed")) if "maxspeed" in tags else None
            fw, bw = _side_limit(tags, "forward", lim), _side_limit(tags, "backward", lim)
            if fw != bw:
                # limite diferente em cada sentido (maxspeed:forward/backward): um ponto por
                # sentido, com o rumo do trânsito. Com um sentido só sinalizado e sem maxspeed,
                # o outro fica com a estimativa de sempre (abaixo).
                for kmh, reverse in ((fw, False), (bw, True)):
                    if kmh is not None:
                        _emit_directed(lims, verts, bbox, kmh, reverse)
                if fw is not None and bw is not None:
                    continue
            elif fw is not None:
                _emit(lims, verts, bbox, Limit(0, 0, fw, "OSM"))
                continue
            zone = _zone_limit(tags)
            if zone is not None:
                _emit(lims, verts, bbox, Limit(0, 0, zone[0], "OSM:zone", True, zone[1]))
            elif hw in _INFER_CLASSES:
                if hw in _MAIN_ROADS and not tags.get("lit"):
                    pending_main.append((hw, verts, pts))   # urbano ou rural: decide no fim
                else:
                    est = class_limit(hw, is_yes(tags.get("lit")))
                    if est is not None:
                        _emit(lims, verts, bbox, Limit(0, 0, est[0], "OSM:classe", True, est[1]))
        elif o.is_relation():
            if tags.get("type") != "enforcement":
                continue
            enf = tags.get("enforcement", "")
            # No BR o radar de trecho é type=enforcement + enforcement=maxspeed (NÃO
            # average_speed), com nós de papel from/to/device. traffic_signals = avanço de sinal.
            if enf not in ("average_speed", "maxspeed", "traffic_signals"):
                continue
            frm = to = dev = None
            for m in o.members:
                try:
                    if m.type != "n":
                        continue
                    if m.role == "from" and frm is None:
                        frm = m.ref
                    elif m.role == "to" and to is None:
                        to = m.ref
                    elif m.role == "device" and dev is None:
                        dev = m.ref
                except AttributeError:
                    continue
            if frm is None and dev is None:
                continue
            for nid in (frm, to, dev):
                if nid is not None:
                    wanted.add(nid)
            enf_rels.append((enf, frm, to, dev, _cam_limit(tags)))

    # 2ª passada: os nós from/to costumam ser marcadores sem tag -> não passaram no
    # KeyFilter. Resolve as coordenadas que faltam por id.
    for hw, verts, pts in pending_main:
        est = class_limit(hw, _is_urban(pts, urban))
        if est is not None:
            _emit(lims, verts, bbox, Limit(0, 0, est[0], "OSM:classe", True, est[1]))

    missing = wanted - node_pt.keys()
    if missing:
        idf = osmium.filter.IdFilter(missing)
        idf.enable_for(osmium.osm.osm_entity_bits.NODE)
        for o in osmium.FileProcessor(pbf_path).with_filter(idf):
            if o.is_node() and o.location.valid():
                node_pt[o.id] = (o.location.lat, o.location.lon)

    _apply_enforcement_relations(enf_rels, node_pt, cam_idx_by_nid, cams, bbox)
    if probe_out is not None:
        probe_out.update(resolve_probes(probe_found))
    return cams, lims, structs


def _probe_grid(probes: List[Camera]) -> Dict[Tuple[int, int], List[Tuple[float, float, str]]]:
    grid: Dict[Tuple[int, int], List[Tuple[float, float, str]]] = {}
    for c in probes:
        if c.heading_hint:
            grid.setdefault((int(c.lat // _PROBE_CELL), int(c.lng // _PROBE_CELL)), []).append(
                (c.lat, c.lng, c.heading_hint))
    return grid


def _probe_way(grid, pts, oneway: bool, found) -> None:
    """Guarda, para cada radar-sonda, os trechos de via a até PROBE_M:
    found[(lat, lng, hint)] += (distância, rumo do trecho, mão única?)."""
    my = lat_span_deg(PROBE_M)
    for a, b in zip(pts, pts[1:]):
        mx = lng_span_deg(PROBE_M, max(abs(a[0]), abs(b[0])))
        y0, y1 = int((min(a[0], b[0]) - my) // _PROBE_CELL), int((max(a[0], b[0]) + my) // _PROBE_CELL)
        x0, x1 = int((min(a[1], b[1]) - mx) // _PROBE_CELL), int((max(a[1], b[1]) + mx) // _PROBE_CELL)
        brg = None
        for cy in range(y0, y1 + 1):
            for cx in range(x0, x1 + 1):
                for lat, lng, hint in grid.get((cy, cx), ()):
                    _, d = _proj_seg((lat, lng), a, b)
                    if d > PROBE_M:
                        continue
                    if brg is None:
                        brg = bearing_deg(a, b)
                    found.setdefault((lat, lng, hint), []).append((d, brg, oneway))


def resolve_probes(found, position_confirms: bool = POSITION_CONFIRMS) -> Dict[Tuple[float, float, str], float]:
    """Rumo de cada sonda.

    Radar claramente sobre uma pista de mão única (a mais perto é de mão única e a do sentido
    oposto fica CONFLICT_M ou mais longe): o sentido é o da pista. Com sentido nominal, só se
    não contrariar; se contrariar, fica sem sentido (não dá para saber se erra a posição ou o
    nominal). Com hint "*" (fonte sem sentido, ex. CET com par de lugares), vale a pista.
    Medido em SP: 87% (DER-SP/Artesp) e 93% (CET) dos radares sobre pista de mão única
    inequívoca concordavam com o nominal.

    Em via de mão dupla ou com as duas pistas coladas, só com position_confirms=False: a
    orientação da via mais perto que combine com o nominal."""
    out = {}
    for key, segs in found.items():
        hint = key[2]
        segs = sorted(segs)
        d0, b0, ow0 = segs[0]
        if ow0:
            opposite = [s for s in segs[1:] if s[2] and angle_diff(s[1], b0) > 120]
            if not opposite or opposite[0][0] - d0 >= CONFLICT_M:
                if hint == "*" or orient_to_hint(b0, True, hint) is not None:
                    out[key] = b0
                continue
        if hint == "*" or position_confirms:
            continue
        for _d, b, ow in segs:
            oriented = orient_to_hint(b, ow, hint)
            if oriented is not None:
                out[key] = oriented
                break
    return out


def _apply_way_direction(way, fb, cams) -> None:
    """Radar com direction=forward/backward num nó do way: o rumo do way naquele nó."""
    nodes = list(way.nodes)
    for i, n in enumerate(nodes):
        hit = fb.get(n.ref)
        if hit is None:
            continue
        a, b = nodes[max(0, i - 1)], nodes[min(len(nodes) - 1, i + 1)]
        if a is b or not (a.location.valid() and b.location.valid()):
            continue
        brg = bearing_deg((a.location.lat, a.location.lon), (b.location.lat, b.location.lon))
        idx, forward = hit
        cams[idx] = replace(cams[idx], direction_deg=int(round(brg if forward else brg + 180.0)) % 360)
        del fb[n.ref]


def _emit(lims, verts, bbox, proto: Limit) -> None:
    """Pontos de limite ao longo do way, com o valor/fonte/flag de proto. Estimado não muda
    ao longo do way, então vai um ponto a cada ESTIMATE_STEP_M em vez de um por vértice
    (estrada de terra sinuosa geraria dez vezes mais pontos para o mesmo valor)."""
    pts = sample_polyline(verts, ESTIMATE_STEP_M) if proto.estimated else _limit_points(verts, LIMIT_MAX_GAP_M)
    for lat, lng in pts:
        if in_bbox(bbox, lat, lng):
            lims.append(Limit(lat, lng, proto.limit_kmh, proto.source, proto.estimated, proto.low_kmh))


def _side_limit(tags, side: str, default: Optional[int]) -> Optional[int]:
    """maxspeed:<side> (forward = sentido do desenho do way); sem a tag, o maxspeed da via."""
    v = parse_maxspeed(tags.get(f"maxspeed:{side}")) if f"maxspeed:{side}" in tags else None
    return v if v is not None else default


def _emit_directed(lims, verts, bbox, kmh: int, reverse: bool) -> None:
    """Pontos de limite de um sentido só (placa de um lado da via): direction_deg = rumo de cada
    trecho do way (o contrário, se reverse). Mesmos pontos de _limit_points."""
    for i, (a, b) in enumerate(zip(verts, verts[1:])):
        d = to_dir(bearing_deg(a, b) + (180.0 if reverse else 0.0))
        pts = _limit_points([a, b], LIMIT_MAX_GAP_M)
        for lat, lng in (pts if i == 0 else pts[1:]):   # o vértice a já saiu no trecho anterior
            if in_bbox(bbox, lat, lng):
                lims.append(Limit(lat, lng, kmh, "OSM", direction_deg=d))


def _cell(lat: float, lng: float) -> int:
    return int((lat + 90) // URBAN_CELL_DEG) * 1_000_003 + int((lng + 180) // URBAN_CELL_DEG)


def _is_urban(pts, urban: dict) -> bool:
    """Tronco/primária/secundária sem `lit` passando no meio de ruas = avenida (urbana).
    Ruas residenciais/terciárias em volta pesam 2 e as de serviço 1, numa
    grade mais grossa para caber o país inteiro na memória: soma das células vizinhas (3x3) de
    qualquer nó do way >= URBAN_THRESHOLD. Cada way conta uma vez por célula que toca."""
    for lat, lng in pts:
        cy, cx = int((lat + 90) // URBAN_CELL_DEG), int((lng + 180) // URBAN_CELL_DEG)
        total = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                total += urban.get((cy + dy) * 1_000_003 + (cx + dx), 0)
        if total >= URBAN_THRESHOLD:
            return True
    return False


def _limit_points(verts, max_gap):
    """Emite os vértices do RDP; só insere pontos entre dois vértices se o vão for maior
    que max_gap (garante uma âncora a cada ~1,5 km numa reta longa, sem reamostrar tudo)."""
    if len(verts) < 2:
        return list(verts)
    out = [verts[0]]
    for a, b in zip(verts, verts[1:]):
        d = haversine_m(a, b)
        if d > max_gap:
            n = int(d // max_gap)
            for k in range(1, n + 1):
                t = k / (n + 1)
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        out.append(b)
    return out


def _apply_enforcement_relations(rels, node_pt, cam_idx_by_nid, cams, bbox) -> None:
    """Relação type=enforcement:
      * enforcement=maxspeed|average_speed com 'from' E 'to'  -> SECTION (from=alerta, to=fim)
      * enforcement=traffic_signals                           -> RED_LIGHT no 'from'/'device'
      * só 'from'/'device', sem 'to'                          -> não muda o tipo, só o limite
    Se o ponto de alerta já saiu como nó, é promovido no lugar em vez de duplicar."""
    for enf, frm, to, dev, lim in rels:
        anchor = frm if frm is not None else dev
        start = node_pt.get(anchor)
        if start is None or not in_bbox(bbox, start[0], start[1]):
            continue
        end = node_pt.get(to) if to is not None else None
        if enf == "traffic_signals":
            kind, end = CameraKind.RED_LIGHT, None
        elif end is not None:
            kind = CameraKind.SECTION
        else:
            kind = None  # ponto simples
        idx = cam_idx_by_nid.get(anchor)
        if idx is not None:
            c = cams[idx]
            cams[idx] = replace(   # mantém o resto (sentido, dica de sentido)
                c, kind=kind or c.kind, limit_kmh=c.limit_kmh or lim, source="OSM",
                end_lat=end[0] if end else c.end_lat, end_lng=end[1] if end else c.end_lng,
            )
        elif kind is not None:
            cams.append(Camera(
                lat=start[0], lng=start[1], kind=kind, limit_kmh=lim, source="OSM", active=True,
                end_lat=end[0] if end else None, end_lng=end[1] if end else None,
            ))


def _kind(tags) -> CameraKind:
    # Só o valor de enforcement classifica. Um nó highway=speed_camera sem enforcement
    # é FIXED. Radar de trecho vem das relações type=enforcement
    # (_apply_enforcement_relations).
    enf = tags.get("enforcement", "")
    if "average_speed" in enf:
        return CameraKind.SECTION
    if enf in ("traffic_signals", "red_light"):
        return CameraKind.RED_LIGHT
    return CameraKind.FIXED


def _zone_limit(tags) -> Optional[Tuple[int, int]]:
    """(típico, lado baixo) pela ZONA marcada sem número (zone:maxspeed / maxspeed:type /
    source:maxspeed). Estimado: é a regra geral do CTB para aquele tipo
    de via, não um número sinalizado."""
    for k in ("zone:maxspeed", "maxspeed:type", "source:maxspeed", "zone:traffic"):
        pair = zone_limit(tags.get(k))
        if pair is not None:
            return pair
    return None


def _struct_kind(tags) -> Optional[str]:
    b, t = tags.get("bridge"), tags.get("tunnel")
    if t and t not in ("no", "false"):
        return "TUNNEL"
    if b and b not in ("no", "false"):
        return "BRIDGE"
    return None


_COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def _cam_direction(tags) -> Optional[int]:
    """`direction` do radar: graus (0-360) ou ponto cardeal. 'forward'/'backward' dependem do
    sentido da via e ficam vazios, como 'both' e valores inválidos."""
    v = (tags.get("direction") or "").strip().upper()
    if not v:
        return None
    try:
        deg = float(v)
        return int(round(deg)) % 360 if 0.0 <= deg <= 360.0 else None
    except ValueError:
        pass
    return int(_COMPASS.index(v) * 22.5) if v in _COMPASS else None


def _cam_limit(tags) -> Optional[int]:
    for k in ("maxspeed", "maxspeed:enforced", "enforcement:maxspeed"):
        v = parse_maxspeed(tags.get(k))
        if v is not None:
            return v
    return None


def needs_update(stored: str, remote: str) -> bool:
    """Last-Modified guardado x o da Geofabrik: baixa de novo se o remoto for mais novo, ou se
    não der para comparar (sem data guardada ou data ilegível)."""
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(remote) > parsedate_to_datetime(stored)
    except (TypeError, ValueError, IndexError):
        return True


def _remote_last_modified(url: str) -> str:
    from datakit.sources._http import session
    r = session().head(url, headers=UA, timeout=HTTP_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r.headers.get("Last-Modified", "")


def _download(url: str, path: str) -> str:
    """Baixa para path (via .part, para um download interrompido não parecer completo);
    devolve o Last-Modified."""
    tmp = path + ".part"
    with urlopen(Request(url, headers=UA), timeout=HTTP_TIMEOUT) as r, open(tmp, "wb") as f:
        last_mod = r.headers.get("Last-Modified", "")
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, path)
    return last_mod


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_or(path: str, default: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default
