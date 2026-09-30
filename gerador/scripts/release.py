"""Release de uma geração dos dados: pacotes zip + nota com as contagens, no GitHub.

    python scripts/release.py --anterior catalogo_antes.json            # só monta (em data/release/)
    python scripts/release.py --anterior catalogo_antes.json --criar    # monta e cria a release (gh)

Roda depois do publicar.py --commit: os pacotes saem dos arquivos já publicados (brasil/,
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

GERADOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(GERADOR)
RADARES = ("radares.csv", "radares.geojson", "radares.kml", "radares.gpx")


def _fmt(n: Optional[int]) -> str:
    return "—" if n is None else f"{n:,}".replace(",", ".")


def _delta(novo: Optional[int], antes: Optional[int]) -> str:
    if novo is None or antes is None or novo == antes:
        return ""
    d = novo - antes
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


def pacotes(repo: str, out: str) -> List[str]:
    """Monta os zips em out/ a partir de brasil/ e estados/ do repositório. Devolve os anexos."""
    os.makedirs(out, exist_ok=True)
    anexos: List[str] = []
    est = os.path.join(repo, "estados")
    for uf in sorted(os.listdir(est)):
        d = os.path.join(est, uf)
        files = sorted(os.path.join(d, f) for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)))
        if files:
            anexos.append(os.path.join(out, f"pardal-{uf}.zip"))
            _zip(anexos[-1], files, uf)
    br = os.path.join(repo, "brasil")
    files = sorted(os.path.join(br, f) for f in os.listdir(br) if os.path.isfile(os.path.join(br, f)))
    anexos.append(os.path.join(out, "pardal-brasil.zip"))
    _zip(anexos[-1], files, "brasil")
    anexos.append(os.path.join(out, "pardal-brasil-radares.zip"))
    _zip(anexos[-1], [f for f in files if os.path.basename(f) in RADARES], "brasil")
    cat = os.path.join(out, "catalog.json")
    with open(os.path.join(repo, "catalog.json"), "rb") as fi, open(cat, "wb") as fo:
        fo.write(fi.read())
    anexos.append(cat)
    sums = os.path.join(out, "SHA256SUMS.txt")
    with open(sums, "w", encoding="utf-8", newline="\n") as f:
        for a in anexos:
            f.write(f"{_sha(a)}  {os.path.basename(a)}\n")
    anexos.append(sums)
    return anexos


def etiqueta(cat: dict) -> str:
    return "dados-" + str(cat.get("built_at", ""))[:10]


def nota(cat: dict, anterior: Optional[dict], avisos: List[str], repo_url: str) -> str:
    """Texto da release (Markdown): resumo do Brasil, downloads, tabela por estado com a
    diferença para a geração anterior, avisos. Puro, testado."""
    d = str(cat.get("built_at", ""))
    data = f"{d[8:10]}/{d[5:7]}/{d[0:4]}"
    br, br0 = _counts(cat, "BR"), _counts(anterior, "BR")
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
    tem_ant = bool(anterior)
    L += ["| UF | Radares | Pontos de limite | Pontes e túneis |", "|---|---|---|---|"]
    for uf in sorted(u for u in cat.get("ufs", {}) if u != "BR"):
        c, c0 = _counts(cat, uf), _counts(anterior, uf)
        L.append(f"| {uf} | {_fmt(c.get('cameras'))}{_delta(c.get('cameras'), c0.get('cameras'))} "
                 f"| {_fmt(c.get('limits'))}{_delta(c.get('limits'), c0.get('limits'))} "
                 f"| {_fmt(c.get('structs'))}{_delta(c.get('structs'), c0.get('structs'))} |")
    if tem_ant:
        L += ["", "Entre parênteses, a diferença para a geração anterior."]
    if avisos:
        L += ["", "## Avisos", "",
              "Fontes que falharam nesta geração e foram lidas da cópia anterior guardada:", ""]
        L += [f"- {a}" for a in avisos]
    L += ["", "Dados sob a licença ODbL 1.0 (citar as fontes, manter a licença em bases derivadas); "
          "fontes e colunas no README do repositório."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--anterior", help="catalog.json publicado antes desta geração (para as diferenças)")
    ap.add_argument("--avisos", help="catalog.json do build (data/dist), com a lista de avisos")
    ap.add_argument("--out", default=os.path.join(GERADOR, "data", "release"))
    ap.add_argument("--criar", action="store_true", help="cria a release no GitHub (gh)")
    ap.add_argument("--repo-url", default="https://github.com/Murkiriel/pardal")
    args = ap.parse_args(argv)

    with open(os.path.join(args.repo, "catalog.json"), encoding="utf-8") as f:
        cat = json.load(f)
    anterior = None
    if args.anterior and os.path.exists(args.anterior):
        with open(args.anterior, encoding="utf-8-sig") as f:
            anterior = json.load(f)
    avisos: List[str] = []
    if args.avisos and os.path.exists(args.avisos):
        with open(args.avisos, encoding="utf-8-sig") as f:
            avisos = json.load(f).get("avisos") or []

    anexos = pacotes(args.repo, args.out)
    notas = os.path.join(args.out, "NOTAS.md")
    with open(notas, "w", encoding="utf-8", newline="\n") as f:
        f.write(nota(cat, anterior, avisos, args.repo_url))
    tag = etiqueta(cat)
    d = str(cat.get("built_at", ""))
    titulo = f"Dados de {d[8:10]}/{d[5:7]}/{d[0:4]}"
    print(f"{len(anexos)} anexos e a nota em {args.out}; etiqueta {tag}")
    if not args.criar:
        return 0

    def gh(*a: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["gh", *a], cwd=args.repo, check=check, capture_output=True, text=True)

    if gh("release", "view", tag, check=False).returncode == 0:   # segunda geração no mesmo dia
        tag = f"{tag}-{d[11:13]}{d[14:16]}"
        titulo += f" ({d[11:16]} UTC)"
    sha = subprocess.run(["git", "-C", args.repo, "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True).stdout.strip()
    gh("release", "create", tag, *anexos, "--title", titulo, "--notes-file", notas,
       "--target", sha, "--latest")
    print(f"release {tag} criada ({sha[:7]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
