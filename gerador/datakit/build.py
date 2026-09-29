"""Orquestra a fase de construção: fontes -> merge -> tiles -> pacote(s) de UF -> catálogo.

    python -m datakit.build --uf GO
    python -m datakit.build --all                 # 27 UFs, 5 macrorregiões Geofabrik

Fontes ligadas: OSM (extrato Geofabrik) + DNIT + ANTT + DERs + capitais (JP, Fortaleza,
Recife, BH, Rio) + Detran-DF; limites oficiais das placas da ANTT e dos trechos do Rio (têm
prioridade sobre o OSM no mesmo lugar); situação ativo/inativo pelo Inmetro nas BRs (SNV).
Fonte que falha (rede/portal) é tratada como ausente. Recorte por polígono do IBGE quando `shapely` +
a malha estiverem disponíveis; senão, por bbox.
"""
from __future__ import annotations

import argparse
import os
import sys

from datakit.build_catalog import build as build_catalog
from datakit.build_pack import build as build_pack
from datakit.build_tiles import write_tiles
from datakit import falhas, inmetro_status
from datakit.common import merge_cameras, merge_limits
from datakit.common.lrs import SnvRoutes, load_concessions
from datakit.common.model import override_limits
from datakit.common.ufpoly import polygons as uf_polygons
from datakit.common.ufs import UF_BBOX, geofabrik_region, uf_bbox
from datakit.sources import osm_pbf
from datakit.sources import dnit, antt, der_go, der_sp, der_pe, municipal, bh, df_detran, rio
from datakit.sources import antt_placas, cet_sp, inmetro, snv

OFFICIAL = {
    "DNIT": dnit, "ANTT": antt,
    "DER-GO": der_go, "DER-SP": der_sp, "DER-PE": der_pe,
    "capitais": municipal, "BHTRANS": bh, "DETRAN-DF": df_detran, "RIO": rio,
}

_OSM_CACHE: dict = {}       # region -> (extract, osm_cams, osm_lims, osm_structs, probe_dirs)
_OFFICIAL_CACHE: dict = {}  # name -> list[Camera]  (carga nacional, bbox=None)


def _osm_for_region(region: str, raw_dir: str, probes=None, refresh: bool = True):
    """probes: radares oficiais com sentido nominal (heading_hint), resolvidos contra as vias."""
    if region not in _OSM_CACHE:
        _OSM_CACHE.clear()  # as UFs saem agrupadas por região: a anterior não volta a ser usada
        ex = osm_pbf.ensure_extract(region, raw_dir, refresh=refresh)
        print(f"[build] extrato {os.path.basename(ex.path)}  {ex.bytes/1e6:.1f} MB  ({ex.file_date})")
        probe_dirs: dict = {}
        cams, lims, structs = osm_pbf.load(ex.path, bbox=None, probes=probes, probe_out=probe_dirs)
        print(f"[build] OSM/{region}: {len(cams)} radares, {len(lims)} limites, "
              f"{len(structs)} pontes/túneis, {len(probe_dirs)} sentidos nominais resolvidos")
        _OSM_CACHE[region] = (ex, cams, lims, structs, probe_dirs)
    return _OSM_CACHE[region]


def _resolve_hints(cams, probe_dirs):
    from dataclasses import replace
    from datakit.common.model import to_dir
    out = []
    for c in cams:
        if c.heading_hint and c.direction_deg is None:
            b = probe_dirs.get((c.lat, c.lng, c.heading_hint))
            if b is not None:
                c = replace(c, direction_deg=to_dir(b))
        out.append(c)
    return out


def _official_national(raw_dir: str):
    for name, mod in OFFICIAL.items():
        if name in _OFFICIAL_CACHE:
            continue
        try:
            c, _ = mod.load(raw_dir, bbox=None)
            _OFFICIAL_CACHE[name] = c
            print(f"[build] {name}: {len(c)} radares (nacional)")
        except NotImplementedError as e:
            _OFFICIAL_CACHE[name] = []
            print(f"[build] {name}: pulado ({e})")
        except Exception as e:  # noqa: BLE001
            _OFFICIAL_CACHE[name] = []
            falhas.registrar(name, e)
    return _OFFICIAL_CACHE


_NATIONAL: dict = {}  # snv, concessions, snv_version, meters_idx, official_limits


def _try(label: str, fn, default):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        falhas.registrar(label, e)
        return default


def _national(raw_dir: str) -> dict:
    """Bases nacionais carregadas uma vez: SNV (rotas + concessões), Inmetro e os limites
    oficiais. Cada uma que falhar fica vazia — o pacote sai só sem aquela parte."""
    if _NATIONAL:
        return _NATIONAL
    paths = _try("SNV", lambda: snv.ensure(raw_dir), None)
    routes = snv.routes(raw_dir) if paths else None
    conc = _try("SNV concessões", lambda: load_concessions(paths[1]), {}) if paths else {}
    meters = _try("Inmetro", lambda: inmetro.load(raw_dir), [])
    print(f"[build] Inmetro: {len(meters)} medidores; SNV {paths[2] if paths else '—'}")
    antt_l = _try("ANTT placas", lambda: antt_placas.load(raw_dir, routes), []) if routes else []
    rio_l = _try("Rio trechos", rio.load_limits, [])
    cet_l = _try("CET-SP", cet_sp.load_limits, [])
    print(f"[build] limites oficiais: ANTT {len(antt_l)}, Rio {len(rio_l)}, CET-SP {len(cet_l)} pontos")
    _NATIONAL.update(snv=routes, concessions=conc, snv_version=paths[2] if paths else None,
                     meters=inmetro_status.index_meters(meters), n_meters=len(meters),
                     official_limits={"ANTT": antt_l, "RIO": rio_l, "CET-SP": cet_l})
    return _NATIONAL


def build_one(uf: str, args, poly=None) -> dict:
    uf = uf.upper()
    region = geofabrik_region(uf)
    official = _official_national(args.raw)
    probes = [c for src in official.values() for c in src if c.heading_hint]
    ex, osm_cams, osm_lims, osm_structs, probe_dirs = _osm_for_region(
        region, args.raw, probes, refresh=not getattr(args, "osm_local", False))
    nat = _national(args.raw)

    bbox = uf_bbox(uf)
    keep = _keeper(bbox, poly.get(uf) if poly else None)
    osm_c = [c for c in osm_cams if keep(c.lat, c.lng)]
    osm_l = [x for x in osm_lims if keep(x.lat, x.lng)]
    structs = [x for x in osm_structs if keep(x.lat1, x.lng1) or keep(x.lat2, x.lng2)]
    off_c = _resolve_hints([c for src in official.values() for c in src if keep(c.lat, c.lng)], probe_dirs)

    cams = merge_cameras(off_c, osm_c)   # oficiais primeiro -> ganham empate
    status = {}
    if nat["snv"] is not None and nat["meters"]:
        cams, status = inmetro_status.apply(cams, uf, nat["meters"], nat["snv"], nat["concessions"])
    off_l = [x for src in nat["official_limits"].values() for x in src if keep(x.lat, x.lng)]
    lims = merge_limits(override_limits(off_l, osm_l))

    sources = [{"name": f"OSM/Geofabrik {region}", "file_date": ex.file_date,
                "sha256": ex.sha256, "bytes": ex.bytes}]
    sources += [{"name": n, "count": len(official.get(n, []))} for n in OFFICIAL]
    sources += [{"name": f"{n} (limites)", "count": sum(1 for x in pts if keep(x.lat, x.lng))}
                for n, pts in nat["official_limits"].items()]
    if status:
        sources.append({"name": "Inmetro (situação)", "snv": nat["snv_version"], **status})

    index = write_tiles(
        cams, lims, args.tiles, sources=sources, sample_m=int(osm_pbf.LIMIT_MAX_GAP_M),
        ufs={uf: {"bbox": list(bbox), "cameras": len(cams), "limits": len(lims)}},
        structs=structs,
    )
    manifest = build_pack(uf, args.tiles, args.packs, index, poly=poly.get(uf) if poly else None)
    print(f"[build] pacote {uf}: {manifest['counts']}")
    return manifest


def _keeper(bbox, poly):
    if poly is None:
        from datakit.common import in_bbox
        return lambda lat, lng: in_bbox(bbox, lat, lng)
    from datakit.common import in_bbox
    from shapely.geometry import Point
    from shapely.prepared import prep
    pg = prep(poly)
    return lambda lat, lng: in_bbox(bbox, lat, lng) and pg.contains(Point(lng, lat))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="datakit.build")
    ap.add_argument("--uf", help="UF alvo, ex.: GO")
    ap.add_argument("--all", action="store_true", help="todas as 27 UFs")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--tiles", default="data/tiles")
    ap.add_argument("--packs", default="data/packs")
    ap.add_argument("--dist", default="data/dist")
    ap.add_argument("--no-polygon", action="store_true", help="recorte só por bbox (ignora IBGE)")
    ap.add_argument("--osm-local", action="store_true",
                    help="usa os extratos do OSM já baixados em data/raw/ sem conferir se há versão nova")
    args = ap.parse_args(argv)

    if not args.all and not args.uf:
        ap.error("informe --uf <UF> ou --all")

    poly = {} if args.no_polygon else uf_polygons(args.raw)
    targets = sorted(UF_BBOX) if args.all else [args.uf.upper()]

    # agrupa por região só pra ordem de download previsível
    targets.sort(key=lambda u: (geofabrik_region(u), u))
    ok = 0
    for uf in targets:
        try:
            build_one(uf, args, poly=poly or None)
            ok += 1
        except Exception as e:  # noqa: BLE001
            falhas.registrar(f"pacote {uf}", e)

    catalog = build_catalog(args.packs, args.dist)
    print(f"[build] catálogo: {len(catalog['ufs'])} UF(s), {ok}/{len(targets)} pacote(s) OK -> {args.dist}")

    if args.all and ok:
        from datakit.build_brasil import build as build_brasil
        br = build_brasil(args.packs, args.dist)
        print(f"[build] bundle Brasil: {br['counts']}")
        from datakit.build_formats import build as build_formats
        print(f"[build] formatos extras (GeoJSON/KML/GPX): {build_formats(args.dist)}")

    _record_failures(args.dist)
    return 0 if ok else 1


def _record_failures(dist_dir: str) -> None:
    """Grava as fontes que falharam no catalog.json de dist (o publicar.py barra se houver)."""
    import json
    path = os.path.join(dist_dir, "catalog.json")
    with open(path, encoding="utf-8") as f:
        cat = json.load(f)
    cat["falhas"] = falhas.lista()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=2)
    if cat["falhas"]:
        print("[build] ATENÇÃO — fontes que falharam (o publicar.py não monta sem --aceitar-falhas):")
        for x in cat["falhas"]:
            print(f"  - {x}")


if __name__ == "__main__":
    sys.exit(main())
