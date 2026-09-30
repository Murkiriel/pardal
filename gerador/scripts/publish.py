"""Organiza data/dist/ nas pastas do repositório (brasil/, estados/ e catalogo.json).

    python scripts/publish.py                     # só monta os arquivos na raiz do repositório
    python scripts/publish.py --commit            # monta e faz commit
    python scripts/publish.py --commit --push     # monta, commita e publica
    python scripts/publish.py --accept-drop       # monta mesmo com queda grande (ver drops())
    python scripts/publish.py --accept-failures   # monta mesmo com fonte que falhou no build

Estrutura gerada (na raiz, um nível acima de gerador/):
    catalogo.json                 índice dos pacotes (caminhos já apontando para as pastas)
    brasil/radares.csv|gpx|kml|geojson, brasil/limites.csv, brasil/limites_estimados.csv[.gz],
    brasil/estruturas.csv
    estados/<UF>/radares.csv|gpx|kml|geojson, limites.csv, estruturas.csv

data/dist já tem essa estrutura (os mesmos nomes do pacote ao repositório): aqui só se copia.
Confere o sha256 de cada arquivo do catálogo antes de copiar. Arquivo que não existe mais no
dist sai de brasil/ e estados/ também.
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
from typing import List

GENERATOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(GENERATOR_DIR)
# Os mesmos de datakit/build_brazil.py (PUBLISHED_NAME, MANAGED_DIRS): este script não importa
# o datakit.
_PUBLISHED_NAME = re.compile(r"^(radares|limites_estimados|limites|estruturas)\.(csv\.gz|csv|geojson|kml|gpx)$")
_MANAGED = ("brasil", "estados")
CATALOG = "catalogo.json"
# Nome do catálogo até o schema 1: lido como o publicado anterior se o novo ainda não existir, e
# apagado do repositório na publicação (entra no commit como remoção).
OLD_CATALOG = "catalog.json"

# Uma fonte que falha no build é pulada e o build termina bem; sem estas checagens, um portal fora
# do ar publicaria um estado com menos dados sem ninguém notar. Duas travas: a lista `falhas`
# que o build grava no catálogo (datakit/failures.py) e a comparação com o catalogo.json já
# publicado — queda de mais de MAX_DROP e de pelo menos MIN_DROP itens (radares, limites,
# estimados ou estruturas). A CET-SP fora do ar tirou 9% dos limites de SP; o OSM de um mês
# para o outro varia bem menos que isso.
# O build grava esta marca em data/dist ao começar e apaga ao terminar (datakit/build.py,
# IN_PROGRESS_MARKER): se ela está lá, o build ainda roda ou caiu no meio.
IN_PROGRESS_MARKER = "BUILD_EM_ANDAMENTO"
# O GitHub recusa arquivo acima de 100 MB (o build já registra como falha; aqui é a última trava).
MAX_FILE_BYTES = 95_000_000
MAX_DROP = 0.05
MIN_DROP = 50
_COUNT_FIELDS = (("cameras", "radares"), ("limits", "limites"), ("limits_estimated", "limites estimados"),
                 ("structs", "estruturas"))


def published_files(dist: str) -> List[str]:
    """Caminhos (relativos a dist, com /) de tudo o que vai para o repositório."""
    out = []
    for top in _MANAGED:
        for d, _, fs in os.walk(os.path.join(dist, top)):
            out += [os.path.relpath(os.path.join(d, f), dist).replace(os.sep, "/")
                    for f in fs if _PUBLISHED_NAME.match(f)]
    return sorted(out)


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def drops(published: dict, new: dict, max_drop: float = MAX_DROP) -> List[str]:
    """Cada estado (e o Brasil) do catálogo publicado que sumiu do novo ou caiu mais que
    max_queda em alguma contagem. Vazio = pode publicar."""
    out = []
    new_ufs = new.get("ufs") or {}
    for uf, old in sorted((published.get("ufs") or {}).items()):
        current = new_ufs.get(uf)
        if current is None:
            out.append(f"{uf}: sumiu do catálogo")
            continue
        for key, label in _COUNT_FIELDS:
            a = (old.get("counts") or {}).get(key) or 0
            b = (current.get("counts") or {}).get(key) or 0
            if a and b < a * (1 - max_drop) and a - b >= MIN_DROP:
                out.append(f"{uf}: {label} {a} -> {b} ({(b - a) / a:+.0%})")
    return out


def rewrite_catalog(cat: dict) -> dict:
    """O catálogo publicado: o do build sem as listas de falhas e avisos (só servem para barrar
    ou avisar na publicação; não vão para o repositório)."""
    out = json.loads(json.dumps(cat))
    out.pop("failures", None)
    out.pop("warnings", None)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO, help="raiz do repositório (padrão: pasta acima de gerador/)")
    ap.add_argument("--dist", default=os.path.join(GENERATOR_DIR, "data", "dist"))
    ap.add_argument("--commit", action="store_true", help="faz commit no repositório")
    ap.add_argument("--push", action="store_true", help="publica (exige --commit)")
    ap.add_argument("--accept-drop", action="store_true",
                    help=f"monta mesmo se alguma contagem cair mais de {MAX_DROP * 100:.0f}%% em relação ao publicado")
    ap.add_argument("--accept-failures", action="store_true",
                    help="monta mesmo se alguma fonte falhou no build (campo failures do catálogo)")
    args = ap.parse_args(argv)

    marker = os.path.join(args.dist, IN_PROGRESS_MARKER)
    if os.path.exists(marker):
        with open(marker, encoding="utf-8") as f:
            since = f.read().strip()
        sys.exit(f"build em andamento ou interrompido ({marker}: {since}); espere terminar ou rode o build de novo")

    files = published_files(args.dist)
    large_files = [f"{n} ({os.path.getsize(os.path.join(args.dist, n)) / 1e6:.1f} MB)"
                   for n in files if os.path.getsize(os.path.join(args.dist, n)) > MAX_FILE_BYTES]
    if large_files:
        sys.exit(f"arquivo acima de {MAX_FILE_BYTES / 1e6:.0f} MB (o GitHub recusa):\n  " + "\n  ".join(large_files))

    with open(os.path.join(args.dist, "catalogo.json"), encoding="utf-8") as f:
        cat = json.load(f)
    for uf, entry in cat["ufs"].items():
        for key in ("cameras", "limits", "limits_estimated", "structs"):
            meta = entry.get(key)
            if meta and meta.get("sha256") and _sha(os.path.join(args.dist, meta["file"])) != meta["sha256"]:
                sys.exit(f"sha256 não confere: {meta['file']} ({uf})")

    for warning in cat.get("warnings") or []:
        print(f"aviso (não bloqueia): {warning}")
    if cat.get("failures") and not args.accept_failures:
        sys.exit("fontes falharam no build; rode de novo ou use --accept-failures:\n  " + "\n  ".join(cat["failures"]))

    published = os.path.join(args.repo, CATALOG)
    if not os.path.exists(published):
        published = os.path.join(args.repo, OLD_CATALOG)
    if os.path.exists(published):
        with open(published, encoding="utf-8") as f:
            problems = drops(json.load(f), cat)
        if problems and not args.accept_drop:
            sys.exit("queda grande em relação ao que está publicado (fonte fora do ar no build?):\n  "
                     + "\n  ".join(problems)
                     + "\nconfira as linhas FALHOU no log do build; para montar assim mesmo: --accept-drop")
        for p in problems:
            print(f"aceito: {p}")

    wanted = {rel: os.path.join(args.dist, *rel.split("/")) for rel in files}
    for top in _MANAGED:
        for dirpath, _, files in os.walk(os.path.join(args.repo, top)):
            for fname in files:
                rel = os.path.relpath(os.path.join(dirpath, fname), args.repo).replace(os.sep, "/")
                if rel not in wanted:
                    os.remove(os.path.join(dirpath, fname))
    for rel, src in sorted(wanted.items()):
        dst = os.path.join(args.repo, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    with open(os.path.join(args.repo, CATALOG), "w", encoding="utf-8") as f:
        json.dump(rewrite_catalog(cat), f, ensure_ascii=False, indent=1)
    if os.path.exists(os.path.join(args.repo, OLD_CATALOG)):
        os.remove(os.path.join(args.repo, OLD_CATALOG))
    print(f"{len(wanted)} arquivos montados em {args.repo}")

    if not args.commit:
        return 0
    def git(*a: str) -> None:
        subprocess.run(["git", "-C", args.repo, *a], check=True)

    # Só os dados: o commit nunca leva o que mais estiver pendente no repositório (código do
    # gerador ainda não revisado, arquivos soltos). Inclui caminhos que só existem no índice
    # (pasta apagada por inteiro), para a remoção também entrar.
    paths = [p for p in (*_MANAGED, CATALOG, OLD_CATALOG)
             if os.path.exists(os.path.join(args.repo, p)) or subprocess.run(
                 ["git", "-C", args.repo, "ls-files", "--", p], capture_output=True, text=True).stdout.strip()]
    git("add", "-A", "--", *paths)
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
