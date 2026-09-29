"""Organiza data/dist/ nas pastas do repositório (brasil/, estados/ e catalog.json).

    python scripts/publicar.py                        # só monta os arquivos na raiz do repositório
    python scripts/publicar.py --commit               # monta e faz commit
    python scripts/publicar.py --commit --push        # monta, commita e publica
    python scripts/publicar.py --aceitar-queda        # monta mesmo com queda grande (ver quedas())
    python scripts/publicar.py --aceitar-falhas       # monta mesmo com fonte que falhou no build

Estrutura gerada (na raiz, um nível acima de gerador/):
    catalog.json                 índice dos pacotes (caminhos já apontando para as pastas)
    brasil/radares.csv|gpx|kml|geojson, brasil/limites.csv, brasil/limites_estimados.csv[.gz],
    brasil/estruturas.csv
    estados/<UF>/radares.csv|gpx|kml|geojson, limites.csv, estruturas.csv

Confere o sha256 de cada arquivo do catálogo antes de copiar. Arquivo que não existe mais
no dist sai de brasil/ e estados/ também.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from typing import List, Optional

GERADOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(GERADOR)
_NAME = re.compile(r"^(radares|limites_estimados|limites|estruturas)_([A-Z]{2})\.(csv\.gz|csv|geojson|kml|gpx)$")
_MANAGED = ("brasil", "estados")

# Uma fonte que falha no build é pulada e o build termina bem; sem estas checagens, um portal fora
# do ar publicaria um estado com menos dados sem ninguém notar. Duas travas: a lista `falhas`
# que o build grava no catálogo (datakit/falhas.py) e a comparação com o catalog.json já
# publicado — queda de mais de MAX_QUEDA e de pelo menos MIN_QUEDA itens (radares, limites,
# estimados ou estruturas). A CET-SP fora do ar tirou 9% dos limites de SP; o OSM de um mês
# para o outro varia bem menos que isso.
MAX_QUEDA = 0.05
MIN_QUEDA = 50
_CONTAGENS = (("cameras", "radares"), ("limits", "limites"), ("limits_estimated", "limites estimados"),
              ("structs", "estruturas"))


def dest_for(name: str) -> Optional[str]:
    """'radares_GO.csv' -> 'estados/GO/radares.csv'; 'limites_BR.csv' -> 'brasil/limites.csv'."""
    m = _NAME.match(name)
    if not m:
        return None
    kind, area, ext = m.groups()
    folder = "brasil" if area == "BR" else f"estados/{area}"
    return f"{folder}/{kind}.{ext}"


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def quedas(publicado: dict, novo: dict, max_queda: float = MAX_QUEDA) -> List[str]:
    """Cada estado (e o Brasil) do catálogo publicado que sumiu do novo ou caiu mais que
    max_queda em alguma contagem. Vazio = pode publicar."""
    out = []
    novos = novo.get("ufs") or {}
    for uf, antigo in sorted((publicado.get("ufs") or {}).items()):
        atual = novos.get(uf)
        if atual is None:
            out.append(f"{uf}: sumiu do catálogo")
            continue
        for chave, rotulo in _CONTAGENS:
            a = (antigo.get("counts") or {}).get(chave) or 0
            b = (atual.get("counts") or {}).get(chave) or 0
            if a and b < a * (1 - max_queda) and a - b >= MIN_QUEDA:
                out.append(f"{uf}: {rotulo} {a} -> {b} ({(b - a) / a:+.0%})")
    return out


def rewrite_catalog(cat: dict) -> dict:
    """Mesmo catálogo, com cada `file` trocado pelo caminho dentro das pastas."""
    out = json.loads(json.dumps(cat))
    out.pop("falhas", None)   # só serve para barrar a publicação; não vai para o repositório
    for entry in out["ufs"].values():
        for key in ("radares", "limites", "limites_estimados", "estruturas"):
            meta = entry.get(key)
            if meta and dest_for(meta["file"]):
                meta["file"] = dest_for(meta["file"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO, help="raiz do repositório (padrão: pasta acima de gerador/)")
    ap.add_argument("--dist", default=os.path.join(GERADOR, "data", "dist"))
    ap.add_argument("--commit", action="store_true", help="faz commit no repositório")
    ap.add_argument("--push", action="store_true", help="publica (exige --commit)")
    ap.add_argument("--aceitar-queda", action="store_true",
                    help=f"monta mesmo se alguma contagem cair mais de {MAX_QUEDA:.0%} em relação ao publicado")
    ap.add_argument("--aceitar-falhas", action="store_true",
                    help="monta mesmo se alguma fonte falhou no build (campo falhas do catálogo)")
    args = ap.parse_args()

    cat = json.load(open(os.path.join(args.dist, "catalog.json"), encoding="utf-8"))
    for uf, entry in cat["ufs"].items():
        for key in ("radares", "limites", "limites_estimados", "estruturas"):
            meta = entry.get(key)
            if meta and meta.get("sha256") and _sha(os.path.join(args.dist, meta["file"])) != meta["sha256"]:
                sys.exit(f"sha256 não confere: {meta['file']} ({uf})")

    if cat.get("falhas") and not args.aceitar_falhas:
        sys.exit("fontes falharam no build; rode de novo ou use --aceitar-falhas:\n  " + "\n  ".join(cat["falhas"]))

    publicado = os.path.join(args.repo, "catalog.json")
    if os.path.exists(publicado):
        with open(publicado, encoding="utf-8") as f:
            problemas = quedas(json.load(f), cat)
        if problemas and not args.aceitar_queda:
            sys.exit("queda grande em relação ao que está publicado (fonte fora do ar no build?):\n  "
                     + "\n  ".join(problemas)
                     + "\nconfira as linhas FALHOU no log do build; para montar assim mesmo: --aceitar-queda")
        for p in problemas:
            print(f"aceito: {p}")

    wanted = {}
    for name in os.listdir(args.dist):
        rel = dest_for(name)
        if rel:
            wanted[rel] = os.path.join(args.dist, name)
    for top in _MANAGED:
        for dirpath, _, files in os.walk(os.path.join(args.repo, top)):
            for f in files:
                rel = os.path.relpath(os.path.join(dirpath, f), args.repo).replace(os.sep, "/")
                if rel not in wanted:
                    os.remove(os.path.join(dirpath, f))
    for rel, src in sorted(wanted.items()):
        dst = os.path.join(args.repo, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    with open(os.path.join(args.repo, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(rewrite_catalog(cat), f, ensure_ascii=False, indent=1)
    print(f"{len(wanted)} arquivos montados em {args.repo}")

    if not args.commit:
        return 0
    git = lambda *a: subprocess.run(["git", "-C", args.repo, *a], check=True)
    git("add", "-A")
    if subprocess.run(["git", "-C", args.repo, "diff", "--cached", "--quiet"]).returncode == 0:
        print("nada mudou")
        return 0
    git("commit", "-m", f"Dados gerados em {cat.get('built_at', '')}")
    if args.push:
        git("push")
        print("publicado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
