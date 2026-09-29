"""Bundle "Brasil" — junta todos os pacotes de UF num arquivo só.

    python -m datakit.build_brasil            # depois de datakit.build --all

Gera data/dist/radares_BR.csv, limites_BR.csv, estruturas_BR.csv e acrescenta a
entrada "BR" ao catalog.json ("Brasil (tudo)"). Dedupe nas divisas (coordenada a ~1 m).

Os limites estimados (estimated=1) saem em limites_estimados_BR.csv, separados dos
sinalizados: juntos passariam dos 100 MB por arquivo que o GitHub aceita. Se o arquivo dos
estimados passar de MAX_FILE_BYTES sozinho, sai compactado (.csv.gz). Nos pacotes por estado
os dois continuam no mesmo arquivo, com a coluna `estimated`.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timezone


MAX_FILE_BYTES = 95_000_000


def split_estimated(rows):
    """(sinalizados, estimados) — pela coluna `estimated`. Puro, testado."""
    signed = [r for r in rows if r.get("estimated") != "1"]
    est = [r for r in rows if r.get("estimated") == "1"]
    return signed, est


def _dedupe(rows, keycols):
    best = {}
    for r in rows:
        k = tuple(round(float(r[c]), 5) if c in ("lat", "lng") else r.get(c, "") for c in keycols)
        best[k] = r
    return list(best.values())


def _read(path):
    if not os.path.exists(path):
        return [], []
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        header = next(rd, [])
        return header, [dict(zip(header, row)) for row in rd if row]


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(h, "") for h in header])


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(packs_dir: str, dist_dir: str) -> dict:
    os.makedirs(dist_dir, exist_ok=True)
    ufs = sorted(d for d in os.listdir(packs_dir)
                 if os.path.isfile(os.path.join(packs_dir, d, "manifest.json")))
    if not ufs:
        raise RuntimeError(f"nenhum pacote em {packs_dir} — rode `datakit.build --all` antes")

    agg = {n: ([], None) for n in ("cameras.csv", "limits.csv", "structs.csv")}
    for uf in ufs:
        for fname in agg:
            header, rows = _read(os.path.join(packs_dir, uf, fname))
            cur_rows, cur_hdr = agg[fname]
            cur_rows.extend(rows)
            agg[fname] = (cur_rows, cur_hdr or header)

    out = {
        "cameras.csv": ("radares_BR.csv", ("lat", "lng", "kind", "source", "direction_deg"), "cameras"),
        "limits.csv": ("limites_BR.csv", ("lat", "lng", "limit_kmh", "source", "direction_deg"), "limits"),
        "structs.csv": ("estruturas_BR.csv", ("lat1", "lng1", "lat2", "lng2", "kind"), "structs"),
    }
    files_meta = {}
    counts = {}
    for src, (dst_name, keycols, count_key) in out.items():
        rows, header = agg[src]
        if not header:
            continue
        rows = _dedupe(rows, keycols)
        est = []
        if src == "limits.csv":
            rows, est = split_estimated(rows)
        dst = os.path.join(dist_dir, dst_name)
        _write(dst, header, rows)
        files_meta[dst_name] = {"file": dst_name, "bytes": os.path.getsize(dst),
                                "sha256": _sha256(dst), "count": len(rows)}
        counts[count_key] = len(rows)
        if est:
            name = "limites_estimados_BR.csv"
            path = os.path.join(dist_dir, name)
            _write(path, header, est)
            stale = path + ".gz"
            if os.path.getsize(path) > MAX_FILE_BYTES:
                with open(path, "rb") as fi, gzip.open(stale, "wb", compresslevel=9) as fo:
                    fo.writelines(fi)
                os.remove(path)
                name, path = name + ".gz", stale
            elif os.path.exists(stale):
                os.remove(stale)
            files_meta[name] = {"file": name, "bytes": os.path.getsize(path),
                                "sha256": _sha256(path), "count": len(est)}
            counts["limits_estimated"] = len(est)

    # acrescenta "BR" ao catalog.json
    cat_path = os.path.join(dist_dir, "catalog.json")
    catalog = json.load(open(cat_path, encoding="utf-8")) if os.path.exists(cat_path) else {"schema": 1, "ufs": {}}
    catalog["ufs"]["BR"] = {
        "nome": "Brasil (tudo)",
        "bbox": [-34.0, -74.5, 6.0, -32.0],
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cobertura": {"nivel": "bundle", "fontes": ["+".join(sorted(ufs))]},
        "radares": files_meta.get("radares_BR.csv"),
        "limites": files_meta.get("limites_BR.csv"),
        "limites_estimados": files_meta.get("limites_estimados_BR.csv")
        or files_meta.get("limites_estimados_BR.csv.gz"),
        "estruturas": files_meta.get("estruturas_BR.csv"),
        "counts": counts,
        "ufs_incluidas": ufs,
    }
    with open(cat_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
    return catalog["ufs"]["BR"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="datakit.build_brasil")
    ap.add_argument("--packs", default="data/packs")
    ap.add_argument("--dist", default="data/dist")
    args = ap.parse_args(argv)
    br = build(args.packs, args.dist)
    print(f"[brasil] {br['counts']} de {len(br['ufs_incluidas'])} UF(s) -> {args.dist}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
