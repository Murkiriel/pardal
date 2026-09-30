"""data/packs/<UF>/  ->  data/dist/  (arquivos publicáveis por estado + catalog.json).

data/dist tem a mesma estrutura do que é publicado na raiz do repositório:
  data/dist/estados/<UF>/radares.csv, limites.csv, estruturas.csv
  data/dist/catalog.json            (com os caminhos já como no repositório)

Quem consome os pacotes lê o catalog.json da raiz do repositório e baixa os arquivos que ele
lista; scripts/publish.py só copia data/dist para lá.
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

from datakit.build_pack import CAMERAS_FILE, LIMITS_FILE, MANIFEST_FILE, STRUCTS_FILE
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


def _file_ref(path: str, count: Optional[int], dist_dir: str) -> dict:
    return {
        "file": os.path.relpath(path, dist_dir).replace(os.sep, "/"),
        "bytes": os.path.getsize(path),
        "sha256": _sha256(path),
        "count": count,
    }


def build(packs_dir: str, dist_dir: str, base_url: str = "") -> dict:
    os.makedirs(dist_dir, exist_ok=True)
    states_dir = os.path.join(dist_dir, "estados")
    shutil.rmtree(states_dir, ignore_errors=True)   # refeito inteiro: nada de um build anterior fica
    ufs: dict = {}
    for uf in sorted(os.listdir(packs_dir)):
        pdir = os.path.join(packs_dir, uf)
        man_path = os.path.join(pdir, MANIFEST_FILE)
        if not os.path.isfile(man_path):
            continue
        with open(man_path, encoding="utf-8") as f:
            man = json.load(f)

        out = os.path.join(states_dir, uf)
        os.makedirs(out, exist_ok=True)
        rad_dst = os.path.join(out, CAMERAS_FILE)
        lim_dst = os.path.join(out, LIMITS_FILE)
        shutil.copyfile(os.path.join(pdir, CAMERAS_FILE), rad_dst)
        shutil.copyfile(os.path.join(pdir, LIMITS_FILE), lim_dst)

        counts = man.get("counts", {})
        entry = {
            "nome": uf_name(uf),
            "bbox": man.get("bbox", []),
            "built_at": man.get("built_at"),
            "artifact_built_at": man.get("artifact_built_at"),
            "cobertura": _coverage(os.path.join(pdir, CAMERAS_FILE)),
            "radares": _file_ref(rad_dst, counts.get("cameras"), dist_dir),
            "limites": _file_ref(lim_dst, counts.get("limits"), dist_dir),
            "counts": counts,
        }
        struct_src = os.path.join(pdir, STRUCTS_FILE)
        if os.path.exists(struct_src) and os.path.getsize(struct_src) > 40:  # > só o cabeçalho
            struct_dst = os.path.join(out, STRUCTS_FILE)
            shutil.copyfile(struct_src, struct_dst)
            entry["estruturas"] = _file_ref(struct_dst, counts.get("structs"), dist_dir)
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
