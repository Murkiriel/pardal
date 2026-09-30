"""data/packs/<UF>/  ->  data/dist/  (arquivos publicáveis por estado + catalog.json).

Gera:
  data/dist/radares_<UF>.csv
  data/dist/limites_<UF>.csv
  data/dist/estruturas_<UF>.csv
  data/dist/catalog.json

Quem consome os pacotes lê o catalog.json da raiz do repositório e baixa os arquivos que ele
lista (scripts/publish.py reescreve os caminhos para as pastas estados/ e brasil/).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Optional

from datakit.common.ufs import uf_name

_FEDERAL = {"DNIT", "ANTT"}
_MUNICIPAL = {"PMF", "PCR", "PMJP", "CET-SP", "RIO", "BHTRANS"}


def _coverage(cameras_csv: str) -> dict:
    """Que tipo de fonte de radar entrou nesta UF — pra UI ser honesta."""
    srcs: set = set()
    try:
        with open(cameras_csv, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                srcs.update(p for p in (r.get("source") or "").split("+") if p)
    except OSError:
        pass
    has_federal = bool(srcs & _FEDERAL)
    has_state = any(s.startswith("DER-") for s in srcs)
    has_municipal = bool(srcs & _MUNICIPAL)
    partes = []
    if has_federal:
        partes.append("federal")
    if has_state:
        partes.append("estadual")
    if has_municipal:
        partes.append("municipal")
    if "OSM" in srcs and not partes:
        partes.append("osm")
    return {"nivel": "+".join(partes) or "nenhum", "fontes": sorted(srcs)}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_ref(path: str, count: Optional[int]) -> dict:
    return {
        "file": os.path.basename(path),
        "bytes": os.path.getsize(path),
        "sha256": _sha256(path),
        "count": count,
    }


def build(packs_dir: str, dist_dir: str, base_url: str = "") -> dict:
    os.makedirs(dist_dir, exist_ok=True)
    ufs: dict = {}
    for uf in sorted(os.listdir(packs_dir)):
        pdir = os.path.join(packs_dir, uf)
        man_path = os.path.join(pdir, "manifest.json")
        if not os.path.isfile(man_path):
            continue
        with open(man_path, encoding="utf-8") as f:
            man = json.load(f)

        rad_dst = os.path.join(dist_dir, f"radares_{uf}.csv")
        lim_dst = os.path.join(dist_dir, f"limites_{uf}.csv")
        shutil.copyfile(os.path.join(pdir, "cameras.csv"), rad_dst)
        shutil.copyfile(os.path.join(pdir, "limits.csv"), lim_dst)

        counts = man.get("counts", {})
        entry = {
            "nome": uf_name(uf),
            "bbox": man.get("bbox", []),
            "built_at": man.get("built_at"),
            "artifact_built_at": man.get("artifact_built_at"),
            "cobertura": _coverage(os.path.join(pdir, "cameras.csv")),
            "radares": _file_ref(rad_dst, counts.get("cameras")),
            "limites": _file_ref(lim_dst, counts.get("limits")),
            "counts": counts,
        }
        struct_src = os.path.join(pdir, "structs.csv")
        if os.path.exists(struct_src) and os.path.getsize(struct_src) > 40:  # > só o cabeçalho
            struct_dst = os.path.join(dist_dir, f"estruturas_{uf}.csv")
            shutil.copyfile(struct_src, struct_dst)
            entry["estruturas"] = _file_ref(struct_dst, counts.get("structs"))
        ufs[uf] = entry

    catalog = {
        "schema": 1,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_url": base_url,  # dica; quem consome pode sobrescrever
        "ufs": ufs,
    }
    with open(os.path.join(dist_dir, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
    return catalog


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="datakit.build_catalog")
    ap.add_argument("--packs", default="data/packs")
    ap.add_argument("--dist", default="data/dist")
    ap.add_argument("--base-url", default="", help="URL base pública dos arquivos (opcional)")
    args = ap.parse_args(argv)
    cat = build(args.packs, args.dist, args.base_url)
    tot_r = sum(u["radares"]["count"] or 0 for u in cat["ufs"].values())
    tot_l = sum(u["limites"]["count"] or 0 for u in cat["ufs"].values())
    print(f"[catalog] {len(cat['ufs'])} UF(s), {tot_r} radares, {tot_l} limites -> {args.dist}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
