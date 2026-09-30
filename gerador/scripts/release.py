"""Release de uma geração dos dados: pacotes zip + nota com as contagens, no GitHub.

    python scripts/release.py --previous catalogo_antes.json            # só monta (em data/release/)
    python scripts/release.py --previous catalogo_antes.json --create    # monta e cria a release (gh)

Roda depois do publish.py --commit: os pacotes saem dos arquivos já publicados (brasil/,
estados/, catalog.json na raiz do repositório). Uma release por geração, com a etiqueta
dados-AAAA-MM-DD (data de geração do catalog.json, UTC) apontando para o commit dos dados.

Anexos:
    pardal-<UF>.zip               um estado: radares (CSV, GeoJSON, KML, GPX), limites, estruturas
    pardal-brasil.zip             o Brasil inteiro (os mesmos arquivos de brasil/)
    pardal-brasil-radares.zip     só os radares do Brasil, nos quatro formatos (para GPS e apps)
    catalog.json                  o índice dos pacotes
    SHA256SUMS.txt                sha256 de cada anexo
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from typing import Dict, List, Optional

GENERATOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(GENERATOR_DIR)
CAMERA_FILES = ("radares.csv", "radares.geojson", "radares.kml", "radares.gpx")


def _fmt(n: Optional[int]) -> str:
    return "—" if n is None else f"{n:,}".replace(",", ".")


def _delta(new: Optional[int], before: Optional[int]) -> str:
    if new is None or before is None or new == before:
        return ""
    d = new - before
    return f" ({'+' if d > 0 else '−'}{_fmt(abs(d))})"


def _counts(cat: Optional[dict], uf: str) -> Dict[str, int]:
    return ((cat or {}).get("ufs", {}).get(uf) or {}).get("counts") or {}


def _zip(path: str, files: List[str], arcdir: str) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            z.write(f, f"{arcdir}/{os.path.basename(f)}")


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_assets(repo: str, out: str) -> List[str]:
    """Monta os zips em out/ a partir de brasil/ e estados/ do repositório. Devolve os anexos."""
    os.makedirs(out, exist_ok=True)
    assets: List[str] = []
    est = os.path.join(repo, "estados")
    for uf in sorted(os.listdir(est)):
        d = os.path.join(est, uf)
        files = sorted(os.path.join(d, f) for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
        if files:
            assets.append(os.path.join(out, f"pardal-{uf}.zip"))
            _zip(assets[-1], files, uf)
    br = os.path.join(repo, "brasil")
    files = sorted(os.path.join(br, f) for f in os.listdir(br) if os.path.isfile(os.path.join(br, f)))
    assets.append(os.path.join(out, "pardal-brasil.zip"))
    _zip(assets[-1], files, "brasil")
    assets.append(os.path.join(out, "pardal-brasil-radares.zip"))
    _zip(assets[-1], [f for f in files if os.path.basename(f) in CAMERA_FILES], "brasil")
    cat = os.path.join(out, "catalog.json")
    with open(os.path.join(repo, "catalog.json"), "rb") as fi, open(cat, "wb") as fo:
        fo.write(fi.read())
    assets.append(cat)
    sums = os.path.join(out, "SHA256SUMS.txt")
    with open(sums, "w", encoding="utf-8", newline="\n") as f:
        for a in assets:
            f.write(f"{_sha(a)}  {os.path.basename(a)}\n")
    assets.append(sums)
    return assets


def tag_for(cat: dict) -> str:
    return "dados-" + str(cat.get("built_at", ""))[:10]


def release_notes(cat: dict, previous: Optional[dict], warnings: List[str], repo_url: str) -> str:
    """Texto da release (Markdown): resumo do Brasil, downloads, tabela por estado com a
    diferença para a geração anterior, avisos. Puro, testado."""
    d = str(cat.get("built_at", ""))
    data = f"{d[8:10]}/{d[5:7]}/{d[0:4]}"
    br, br0 = _counts(cat, "BR"), _counts(previous, "BR")
    L = [f"Dados gerados em {data} ({d.replace('T', ' ').replace('Z', ' UTC')}) a partir das fontes "
         "oficiais e do OpenStreetMap. Os mesmos arquivos estão em `brasil/`, `estados/` e "
         "`catalog.json` no commit desta versão.", "",
         "## Brasil", "",
         "| | Nesta versão |", "|---|---|"]
    for k, rot in (("cameras", "Radares"), ("limits", "Pontos de limite sinalizado"),
                   ("limits_estimated", "Pontos de limite estimado"), ("structs", "Pontes e túneis")):
        L.append(f"| {rot} | {_fmt(br.get(k))}{_delta(br.get(k), br0.get(k))} |")
    L += ["", "## Downloads", "",
          "- `pardal-<UF>.zip` — um estado: radares (CSV, GeoJSON, KML, GPX), limites e estruturas",
          "- `pardal-brasil.zip` — o Brasil inteiro",
          "- `pardal-brasil-radares.zip` — só os radares do Brasil, nos quatro formatos (GPS e apps)",
          "- `catalog.json` — índice dos pacotes; `SHA256SUMS.txt` — para conferir os arquivos",
          "",
          f"Link fixo para a versão mais recente: `{repo_url}/releases/latest/download/pardal-SP.zip` "
          "(troque a sigla do estado, ou use `pardal-brasil.zip`).",
          "", "## Por estado", ""]
    has_previous = bool(previous)
    L += ["| UF | Radares | Pontos de limite | Pontes e túneis |", "|---|---|---|---|"]
    for uf in sorted(u for u in cat.get("ufs", {}) if u != "BR"):
        c, c0 = _counts(cat, uf), _counts(previous, uf)
        L.append(f"| {uf} | {_fmt(c.get('cameras'))}{_delta(c.get('cameras'), c0.get('cameras'))} "
                 f"| {_fmt(c.get('limits'))}{_delta(c.get('limits'), c0.get('limits'))} "
                 f"| {_fmt(c.get('structs'))}{_delta(c.get('structs'), c0.get('structs'))} |")
    if has_previous:
        L += ["", "Entre parênteses, a diferença para a geração anterior."]
    if warnings:
        L += ["", "## Avisos", "",
              "Fontes que falharam nesta geração e foram lidas da cópia anterior guardada:", ""]
        L += [f"- {a}" for a in warnings]
    L += ["", "Dados sob a licença ODbL 1.0 (citar as fontes, manter a licença em bases derivadas); "
          "fontes e colunas no README do repositório."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--previous", help="catalog.json publicado antes desta geração (para as diferenças)")
    ap.add_argument("--warnings", help="catalog.json do build (data/dist), com a lista de avisos")
    ap.add_argument("--out", default=os.path.join(GENERATOR_DIR, "data", "release"))
    ap.add_argument("--create", action="store_true", help="cria a release no GitHub (gh)")
    ap.add_argument("--repo-url", default="https://github.com/Murkiriel/pardal")
    args = ap.parse_args(argv)

    with open(os.path.join(args.repo, "catalog.json"), encoding="utf-8") as f:
        cat = json.load(f)
    previous = None
    if args.previous and os.path.exists(args.previous):
        with open(args.previous, encoding="utf-8-sig") as f:
            previous = json.load(f)
    warnings: List[str] = []
    if args.warnings and os.path.exists(args.warnings):
        with open(args.warnings, encoding="utf-8-sig") as f:
            warnings = json.load(f).get("warnings") or []

    assets = build_assets(args.repo, args.out)
    notes_path = os.path.join(args.out, "NOTAS.md")
    with open(notes_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(release_notes(cat, previous, warnings, args.repo_url))
    tag = tag_for(cat)
    d = str(cat.get("built_at", ""))
    title = f"Dados de {d[8:10]}/{d[5:7]}/{d[0:4]}"
    print(f"{len(assets)} anexos e a nota em {args.out}; etiqueta {tag}")
    if not args.create:
        return 0

    def gh(*a: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["gh", *a], cwd=args.repo, check=check, capture_output=True, text=True)

    if gh("release", "view", tag, check=False).returncode == 0:   # segunda geração no mesmo dia
        tag = f"{tag}-{d[11:13]}{d[14:16]}"
        title += f" ({d[11:16]} UTC)"
    sha = subprocess.run(["git", "-C", args.repo, "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True).stdout.strip()
    gh("release", "create", tag, *assets, "--title", title, "--notes-file", notes_path,
       "--target", sha, "--latest")
    print(f"release {tag} criada ({sha[:7]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
