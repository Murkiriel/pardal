"""Orquestra a fase de construção: fontes -> merge -> pacote(s) de UF -> catálogo.

    python -m datakit.build --uf GO
    python -m datakit.build --uf GO,RJ,SC         # algumas UFs num build só
    python -m datakit.build --all                 # 27 UFs, 5 macrorregiões Geofabrik

Fontes ligadas: OSM (extrato Geofabrik) + DNIT + ANTT + DERs + capitais (JP, Fortaleza,
Recife, BH, Rio) + Detran-DF; limites oficiais das placas da ANTT e dos trechos do Rio (têm
prioridade sobre o OSM no mesmo lugar); situação ativo/inativo pelo Inmetro nas BRs (SNV).
Fonte que falha (rede/portal) é tratada como ausente. Cada ponto vai para exatamente uma UF pelo
polígono do IBGE (ou o mais perto a até 5 km: pontes, orla, ilhas — common/ufassign.py) quando
`shapely` + a malha estiverem disponíveis; senão, recorte por bbox.
"""
from __future__ import annotations

import argparse
import os
import sys

from datakit.build_catalog import build as build_catalog
from datakit.build_pack import build as build_pack
from datakit import accident_spots, failures, inmetro_addresses, inmetro_status, potholes, radar_zones
from datakit.common import merge_cameras, merge_limits
from datakit.common.model import merge_tolls
from datakit.common.model import (absorb_osm, collapse_osm, deactivate_near, merge_cross_agency,
                                  override_limits)
from datakit.common.geo import in_bbox
from datakit.common.ufassign import UfAssigner
from datakit.common.ufs import SOURCE_HOME_UF, UF_BBOX, geofabrik_region, uf_bbox
from datakit.context import BuildContext
from datakit.sources import dnit, antt, der_go, der_sp, der_pe, der_mg, municipal, bh, df_detran, rio, cet_sp, eptc
from datakit.sources import prf_concessions

# Fontes oficiais de radares, na ordem do manifest. Cada módulo expõe fetch(ctx) -> SourceData
# (datakit/context.py).
OFFICIAL = {
    "DNIT": dnit, "ANTT": antt,
    "DER-GO": der_go, "DER-SP": der_sp, "DER-PE": der_pe, "DER-MG": der_mg,
    "capitais": municipal, "BHTRANS": bh, "DETRAN-DF": df_detran, "RIO": rio, "CET-SP": cet_sp, "EPTC": eptc,
}

# Gravado em data/dist no início do build e apagado só no fim: enquanto existir (build rodando,
# ou interrompido no meio), data/dist mistura pacotes de dois builds e o publish.py não monta.
IN_PROGRESS_MARKER = "BUILD_EM_ANDAMENTO"


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


def _drop_far_from_home(uf: str, cams: list, assigner: "UfAssigner | None") -> list:
    """Radar de órgão estadual ou municipal que caiu noutra UF: fica se estiver colado na UF do
    órgão (até NEAR_M, a mesma folga da divisa em common/ufassign.py); mais longe que isso é
    coordenada errada na fonte e sai. Medido em 01/10/2026 (FONTES.md): 1 do Detran-DF a 0,1 km
    do DF (fica); 3 do DER-SP a 10, 16 e 380 km de SP (saem)."""
    if assigner is None:
        return cams
    out = []
    for c in cams:
        home = SOURCE_HOME_UF.get(c.source)
        if home and home != uf and home in assigner.ufs:
            d = assigner.distance_m(home, c.lat, c.lng)
            if d > assigner.near_m:
                print(f"[build] {uf}: radar de {c.source} em {c.lat:.5f},{c.lng:.5f}, a {d / 1000:.0f} km "
                      f"de {home}: coordenada errada na fonte, descartado")
                continue
        out.append(c)
    return out


class _Split:
    """Quais itens de uma lista ficam com a UF. Com polígonos, cada ponto tem exatamente uma UF
    (common/ufassign.py), calculada uma vez por lista e guardada (a mesma lista serve às várias
    UFs da região); sem polígonos (fallback), toda UF cuja bbox contém o ponto."""

    def __init__(self, assigner: "UfAssigner | None" = None):
        self.assigner = assigner
        self._memo: dict = {}   # chave -> (lista, UFs); trocar a lista da chave libera a anterior

    def _ufs(self, key, items, pos):
        got = self._memo.get(key)
        if got is None or got[0] is not items:
            pts = [pos(x) for x in items]
            got = (items, self.assigner.assign([p[0] for p in pts], [p[1] for p in pts]))
            self._memo[key] = got
        return got[1]

    def points(self, uf: str, key, items: list) -> list:
        if self.assigner is None:
            bbox = uf_bbox(uf)
            return [x for x in items if in_bbox(bbox, x.lat, x.lng)]
        return [x for x, u in zip(items, self._ufs(key, items, lambda x: (x.lat, x.lng))) if u == uf]

    def structs(self, uf: str, key, items: list) -> list:
        """Ponte/túnel fica com a UF de qualquer uma das duas pontas (a divisa às vezes é o rio)."""
        if self.assigner is None:
            bbox = uf_bbox(uf)
            return [s for s in items if in_bbox(bbox, s.lat1, s.lng1) or in_bbox(bbox, s.lat2, s.lng2)]
        u1 = self._ufs((key, 1), items, lambda s: (s.lat1, s.lng1))
        u2 = self._ufs((key, 2), items, lambda s: (s.lat2, s.lng2))
        return [s for s, a, b in zip(items, u1, u2) if uf in (a, b)]


def build_one(uf: str, ctx: BuildContext, packs_dir: str, split: "_Split | None" = None) -> dict:
    uf = uf.upper()
    split = split or _Split()
    region = geofabrik_region(uf)
    official = ctx.official()
    probes = [c for loaded in official.values() for c in loaded.cameras if c.heading_hint]
    ex, osm_cams, osm_lims, osm_structs, probe_dirs = ctx.osm(region, probes)
    nat = ctx.national()

    osm_c = split.points(uf, "osm-radares", osm_cams)
    osm_l = split.points(uf, "osm-limites", osm_lims)
    structs = split.structs(uf, "osm-estruturas", osm_structs)
    bumps = split.points(uf, "osm-lombadas", ctx.osm_bumps(region))
    tolls = merge_tolls(split.points(uf, "antt-pedagios", ctx.antt_tolls()), split.points(uf, "osm-pedagios", ctx.osm_tolls(region)))
    off_c = _resolve_hints([c for name, loaded in official.items()
                            for c in split.points(uf, ("oficial", name), loaded.cameras)], probe_dirs)
    off_c = _drop_far_from_home(uf, off_c, split.assigner)

    # mesmo radar em dois órgãos, no oficial e no OSM, ou duas vezes no OSM: um só (common/model.py)
    audit: list = []
    off_c, n_cross = merge_cross_agency(off_c, audit=audit)
    off_c, osm_c, n_joined = absorb_osm(off_c, osm_c, audit=audit)
    osm_c, n_osm = collapse_osm(osm_c, audit=audit)
    _write_audit(ctx.raw_dir, uf, audit)
    # radar do OSM num local que o órgão desativou (a CET), sem local ativo por perto: inativo
    dead = [p for loaded in official.values() for p in loaded.deactivated]
    osm_c, n_dead = deactivate_near(osm_c, dead, off_c)
    print(f"[build] {uf}: juntados {n_cross} entre órgãos, {n_joined} OSM a oficiais, {n_osm} OSM a OSM; "
          f"{n_dead} marcados inativos (CET)")
    cams = merge_cameras(off_c, osm_c)   # oficiais primeiro -> ganham empate
    status: dict = {}
    if nat["snv"] is not None and nat["meters"]:
        cams, status = inmetro_status.apply(cams, uf, nat["meters"], nat["snv"], nat["concessions"])
    # medidores do Inmetro só com endereço: confirmam o radar que já existe ali, ou viram radar novo
    by_address = split.points(uf, ("inmetro-enderecos", "GO" if uf == "DF" else uf), ctx.inmetro_points(uf))
    cams, addressed = inmetro_addresses.apply(cams, by_address)
    print(f"[build] {uf}: Inmetro por endereço: {addressed['confirmed']} radares confirmados "
          f"({addressed['limits_filled']} ganharam limite, {addressed['reactivated']} voltaram a ativo), "
          f"{addressed['new']} novos")
    # a planilha das concessões que a PRF mostra: inoperante ou vencido sai inativo; ativo que falta entra
    conc = [r for r in ctx.prf_concessions() if r.uf == uf]
    cams, conc_stats = prf_concessions.apply(cams, conc, ctx.snv_routes() if conc else None)
    print(f"[build] {uf}: PRF (concessões): {conc_stats['retired']} radares inativos, {conc_stats['added']} novos")
    off_by_src = {n: split.points(uf, ("limites", n), pts) for n, pts in nat["official_limits"].items()}
    off_l = [x for pts in off_by_src.values() for x in pts]
    lims = merge_limits(override_limits(off_l, osm_l))

    sources = [{"name": f"OSM/Geofabrik {region}", "file_date": ex.file_date,
                "sha256": ex.sha256, "bytes": ex.bytes}]
    sources += [{"name": n, "count": len(official[n].cameras) if n in official else 0} for n in ctx.sources]
    sources += [{"name": f"{n} (limites)", "count": len(pts)} for n, pts in off_by_src.items()]
    if status:
        sources.append({"name": "Inmetro (situação)", "snv": nat["snv_version"], **status})
    sources.append({"name": "PRF (radares das concessões)", **conc_stats})
    sources.append({"name": "Inmetro (endereços, CNEFE)", "count": len(by_address), **addressed})
    valid_from, stretches = ctx.prf_portable_radar()
    mine = [s for s in stretches if s.uf == uf]
    zones, unplaced = radar_zones.place(uf, valid_from, mine, ctx.snv_routes() if mine else None,
                                        ctx.prf_speed_fines() if mine else None)
    sources.append({"name": "PRF (trechos aptos ao radar portátil)", "valid_from": valid_from, "count": len(zones),
                    "unplaced": unplaced})
    if uf == "SC":   # o radar portátil das rodovias estaduais de SC, pelos marcos do OSM do extrato da região
        sc_zones, sc_out = ctx.sc_portable_radar(ex.path)
        zones = zones + sc_zones
        sources.append({"name": "SIE-SC (radar portátil)", "count": len(sc_zones), "unplaced": sc_out})
    month, rough = ctx.dnit_potholes()
    rough_here = [r for r in rough if r.uf == uf]
    holes, holes_out = potholes.place(uf, month, rough_here, ctx.snv_routes() if rough_here else None)
    sources.append({"name": "DNIT (buracos, ICM)", "month": month, "count": len(holes), "unplaced": holes_out})
    period, hot = ctx.prf_moto_accidents()
    hot_here = [h for h in hot if h.uf == uf]
    accidents, accidents_out = accident_spots.place(uf, period, hot_here, ctx.snv_routes() if hot_here else None)
    sources.append({"name": "PRF (acidentes com moto)", "months": f"{period[0]}/{period[1]}", "count": len(accidents),
                    "unplaced": accidents_out})

    manifest = build_pack(uf, packs_dir, cams, lims, structs, sources, bumps=bumps, tolls=tolls, radar_zones=zones,
                          potholes=holes, moto_accidents=accidents)
    print(f"[build] pacote {uf}: {manifest['counts']}")
    return manifest


def _write_audit(raw_dir: str, uf: str, rows: list) -> None:
    """data/audit/juncoes_<UF>.csv: cada junção de radares deste build (não é publicado)."""
    import csv
    from datakit.common.model import AUDIT_HEADER
    d = os.path.join(os.path.dirname(os.path.abspath(raw_dir)), "audit")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"juncoes_{uf}.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=AUDIT_HEADER)
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="datakit.build")
    ap.add_argument("--uf", help="UF alvo, ex.: GO (ou várias: GO,RJ,SC)")
    ap.add_argument("--all", action="store_true", help="todas as 27 UFs")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--packs", default="data/packs")
    ap.add_argument("--dist", default="data/dist")
    ap.add_argument("--no-polygon", action="store_true", help="recorte só por bbox (ignora IBGE)")
    ap.add_argument("--osm-local", action="store_true",
                    help="usa os extratos do OSM já baixados em data/raw/ sem conferir se há versão nova")
    args = ap.parse_args(argv)

    if not args.all and not args.uf:
        ap.error("informe --uf <UF> ou --all")

    marker = os.path.join(args.dist, IN_PROGRESS_MARKER)
    os.makedirs(args.dist, exist_ok=True)
    with open(marker, "w", encoding="utf-8") as f:
        from datetime import datetime, timezone
        f.write(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {' '.join(argv or sys.argv[1:])}\n")

    ctx = BuildContext(args.raw, refresh_osm=not args.osm_local, sources=OFFICIAL)
    poly = {} if args.no_polygon else ctx.polygons()
    split = _Split(UfAssigner(poly) if poly else None)
    targets = sorted(UF_BBOX) if args.all else [u.strip().upper() for u in args.uf.split(",") if u.strip()]
    unknown = [u for u in targets if u not in UF_BBOX]
    if unknown:
        ap.error(f"UF desconhecida: {', '.join(unknown)}")

    # agrupa por região só pra ordem de download previsível
    targets.sort(key=lambda u: (geofabrik_region(u), u))
    ok = 0
    for uf in targets:
        try:
            build_one(uf, ctx, args.packs, split)
            ok += 1
        except Exception as e:  # noqa: BLE001
            failures.record(f"pacote {uf}", e)

    catalog = build_catalog(args.packs, args.dist)
    print(f"[build] catálogo: {len(catalog['ufs'])} UF(s), {ok}/{len(targets)} pacote(s) OK -> {args.dist}")

    if args.all and ok:
        from datakit.build_brazil import build as build_brazil
        br = build_brazil(args.packs, args.dist)
        print(f"[build] bundle Brasil: {br['counts']}")
        from datakit.build_formats import build as build_formats
        print(f"[build] formatos extras (GeoJSON/KML/GPX): {build_formats(args.dist)}")

    from datakit.build_brazil import MAX_FILE_BYTES, oversized
    for name, size in oversized(args.dist):
        failures.record(f"tamanho {name}", ValueError(
            f"{size / 1e6:.1f} MB passa do teto de {MAX_FILE_BYTES / 1e6:.0f} MB por arquivo"))

    _record_failures(args.dist)
    os.remove(marker)
    return 0 if ok else 1


def _record_failures(dist_dir: str) -> None:
    """Grava as fontes que falharam no catalogo.json de dist (o publish.py barra se houver)."""
    import json
    path = os.path.join(dist_dir, "catalogo.json")
    with open(path, encoding="utf-8") as f:
        cat = json.load(f)
    cat["failures"] = failures.recorded()
    cat["warnings"] = failures.warnings()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=2)
    for x in cat["warnings"]:
        print(f"[build] aviso: {x}")
    if cat["failures"]:
        print("[build] ATENÇÃO — fontes que falharam (o publish.py não monta sem --accept-failures):")
        for x in cat["failures"]:
            print(f"  - {x}")


if __name__ == "__main__":
    sys.exit(main())
