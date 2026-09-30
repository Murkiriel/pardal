"""Bundle "Brasil" — junta todos os pacotes de UF num arquivo só.

    python -m datakit.build_brasil            # depois de datakit.build --all

Gera data/dist/radares_BR.csv, limites_BR.csv, estruturas_BR.csv e acrescenta a
entrada "BR" ao catalog.json ("Brasil (tudo)"). Dedupe nas divisas (coordenada a ~1 m).

Os limites estimados (estimated=1) saem em limites_estimados_BR.csv, separados dos
sinalizados: juntos passariam dos 100 MB por arquivo que o GitHub aceita. Se o arquivo dos
estimados passar de MAX_FILE_BYTES sozinho, sai compactado (.csv.gz). Nos pacotes por estado
os dois continuam no mesmo arquivo, com a coluna `estimated`.

Em fluxo: as linhas são lidas dos pacotes e escritas direto no arquivo do Brasil; para a
deduplicação fica em memória só um hash de 128 bits da chave de cada linha e a posição dela (o
país inteiro são ~3 milhões de linhas de limite: guardar as linhas passava de alguns GB).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import sys
from array import array
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Sequence, Tuple


# Teto por arquivo publicado: o GitHub recusa arquivos acima de 100 MB.
MAX_FILE_BYTES = 95_000_000
_PUBLISHED = re.compile(r"^(radares|limites_estimados|limites|estruturas)_([A-Z]{2})\.(csv\.gz|csv|geojson|kml|gpx)$")


def oversized(dist_dir: str, limit: int = MAX_FILE_BYTES) -> List[Tuple[str, int]]:
    """(nome, bytes) de cada arquivo que vai ser publicado e passa do teto."""
    out = []
    for name in sorted(os.listdir(dist_dir)):
        path = os.path.join(dist_dir, name)
        if _PUBLISHED.match(name) and os.path.getsize(path) > limit:
            out.append((name, os.path.getsize(path)))
    return out


def split_estimated(rows):
    """(sinalizados, estimados) — pela coluna `estimated`. Puro, testado."""
    signed = [r for r in rows if r.get("estimated") != "1"]
    est = [r for r in rows if r.get("estimated") == "1"]
    return signed, est


def _key(r: Dict[str, str], keycols: Sequence[str]) -> Tuple[int, int]:
    """Hash de 128 bits da chave de deduplicação (a coordenada a 5 casas, ~1 m)."""
    k = tuple(round(float(r[c]), 5) if c in ("lat", "lng") else r.get(c, "") for c in keycols)
    d = hashlib.blake2b(repr(k).encode("utf-8"), digest_size=16).digest()
    return int.from_bytes(d[:8], "little"), int.from_bytes(d[8:], "little")


def _rows(paths: List[str]) -> Iterator[Dict[str, str]]:
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8") as f:
            rd = csv.reader(f)
            header = next(rd, [])
            for row in rd:
                if row:
                    yield dict(zip(header, row))


def _header(paths: List[str]) -> List[str]:
    for path in paths:
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                header = next(csv.reader(f), [])
            if header:
                return header
    return []


def _winners(paths: List[str], keycols: Sequence[str]):
    """Para cada linha, na ordem de leitura: a posição da linha cujo conteúdo vai para a saída
    naquele lugar, ou -1 se ela sai (repetida). Fica o lugar da primeira ocorrência da chave e o
    conteúdo da última — o mesmo que um dict {chave: linha} preenchido em ordem."""
    import numpy as np

    hi, lo = array("Q"), array("Q")
    for r in _rows(paths):
        a, b = _key(r, keycols)
        hi.append(a)
        lo.append(b)
    n = len(hi)
    win = np.full(n, -1, dtype=np.int64)
    if not n:
        return win
    h1, h2 = np.frombuffer(hi, dtype=np.uint64), np.frombuffer(lo, dtype=np.uint64)
    order = np.lexsort((h2, h1))                     # estável: dentro da chave, na ordem de leitura
    hs, ls = h1[order], h2[order]
    starts = np.concatenate(([True], (hs[1:] != hs[:-1]) | (ls[1:] != ls[:-1])))
    first = order[starts]
    last = order[np.concatenate((np.nonzero(starts)[0][1:] - 1, [n - 1]))]
    win[first] = last
    return win


def _write_merged(paths, header, keycols, dst, est_dst=None) -> Tuple[int, int]:
    """Escreve as linhas sem repetição em dst (e as estimadas em est_dst, se dado).
    Devolve (linhas em dst, linhas em est_dst)."""
    win = _winners(paths, keycols)
    ahead = {int(w) for g, w in enumerate(win) if w >= 0 and w != g}   # conteúdo que vem depois
    fetched: Dict[int, Dict[str, str]] = {}
    if ahead:
        for g, r in enumerate(_rows(paths)):
            if g in ahead:
                fetched[g] = r
    counts = [0, 0]
    with open(dst, "w", newline="", encoding="utf-8") as f, \
            (open(est_dst, "w", newline="", encoding="utf-8") if est_dst else open(os.devnull, "w")) as fe:
        w, we = csv.writer(f, lineterminator="\n"), csv.writer(fe, lineterminator="\n")
        w.writerow(header)
        if est_dst:
            we.writerow(header)
        for g, r in enumerate(_rows(paths)):
            src = int(win[g])
            if src < 0:
                continue
            row = r if src == g else fetched[src]
            is_est = est_dst is not None and row.get("estimated") == "1"
            (we if is_est else w).writerow([row.get(h, "") for h in header])
            counts[1 if is_est else 0] += 1
    return counts[0], counts[1]


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

    out = {
        "cameras.csv": ("radares_BR.csv", ("lat", "lng", "kind", "source", "direction_deg"), "cameras"),
        "limits.csv": ("limites_BR.csv", ("lat", "lng", "limit_kmh", "source", "direction_deg"), "limits"),
        "structs.csv": ("estruturas_BR.csv", ("lat1", "lng1", "lat2", "lng2", "kind"), "structs"),
    }
    files_meta = {}
    counts = {}
    for src, (dst_name, keycols, count_key) in out.items():
        paths = [os.path.join(packs_dir, uf, src) for uf in ufs]
        header = _header(paths)
        if not header:
            continue
        dst = os.path.join(dist_dir, dst_name)
        name = "limites_estimados_BR.csv"
        path = os.path.join(dist_dir, name)
        n, n_est = _write_merged(paths, header, keycols, dst, path if src == "limits.csv" else None)
        files_meta[dst_name] = {"file": dst_name, "bytes": os.path.getsize(dst),
                                "sha256": _sha256(dst), "count": n}
        counts[count_key] = n
        if src == "limits.csv" and not n_est:
            os.remove(path)
        if n_est:
            stale = path + ".gz"
            if os.path.getsize(path) > MAX_FILE_BYTES:
                with open(path, "rb") as fi, gzip.open(stale, "wb", compresslevel=9) as fo:
                    fo.writelines(fi)
                os.remove(path)
                name, path = name + ".gz", stale
            elif os.path.exists(stale):
                os.remove(stale)
            files_meta[name] = {"file": name, "bytes": os.path.getsize(path),
                                "sha256": _sha256(path), "count": n_est}
            counts["limits_estimated"] = n_est

    # acrescenta "BR" ao catalog.json
    cat_path = os.path.join(dist_dir, "catalog.json")
    catalog: dict = {"schema": 1, "ufs": {}}
    if os.path.exists(cat_path):
        with open(cat_path, encoding="utf-8") as f:
            catalog = json.load(f)
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
