#!/usr/bin/env python3
"""
Analyse une capture JSONL produite par capture_addon.py et produit :
- docs/api_inventory.md   : inventaire humain (endpoints dédupliqués)
- docs/api_inventory.json : version structurée (base pour un connecteur MCP)

Usage :
    python3 analyze_capture.py ../captures/edumoov_capture.jsonl
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ID_RE = re.compile(r"^\d+$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_path(path: str) -> str:
    parts = path.split("/")
    norm = []
    for p in parts:
        if ID_RE.match(p):
            norm.append("{id}")
        elif UUID_RE.match(p):
            norm.append("{uuid}")
        elif DATE_RE.match(p):
            norm.append("{date}")
        else:
            norm.append(p)
    return "/".join(norm)


def load_records(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def main():
    if len(sys.argv) < 2:
        print("Usage: analyze_capture.py <capture.jsonl>")
        sys.exit(1)
    cap_path = Path(sys.argv[1])
    out_dir = Path(__file__).resolve().parent.parent / "docs"
    out_dir.mkdir(exist_ok=True)

    groups = defaultdict(lambda: {
        "examples": [],
        "status_codes": set(),
        "query_params": set(),
        "hosts": set(),
        "count": 0,
    })

    total = 0
    for rec in load_records(cap_path):
        total += 1
        key = (rec["method"], normalize_path(rec["path"]))
        g = groups[key]
        g["count"] += 1
        g["hosts"].add(rec["host"])
        g["status_codes"].add(rec.get("status_code"))
        g["query_params"].update(rec.get("query", {}).keys())
        if len(g["examples"]) < 1:
            g["examples"].append(rec)

    # --- JSON output ---
    json_out = []
    for (method, path), g in sorted(groups.items()):
        json_out.append({
            "method": method,
            "path_pattern": path,
            "hosts": sorted(g["hosts"]),
            "occurrences": g["count"],
            "status_codes": sorted([s for s in g["status_codes"] if s is not None]),
            "query_params": sorted(g["query_params"]),
            "example": g["examples"][0] if g["examples"] else None,
        })
    (out_dir / "api_inventory.json").write_text(
        json.dumps(json_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # --- Markdown output ---
    lines = [
        "# Inventaire API Edumoov (capture automatique)",
        "",
        f"Requêtes totales capturées (hors assets statiques) : **{total}**",
        f"Endpoints uniques détectés : **{len(groups)}**",
        "",
        "| Méthode | Chemin (normalisé) | Host | Occurrences | Codes statut | Params query |",
        "|---|---|---|---|---|---|",
    ]
    for (method, path), g in sorted(groups.items()):
        hosts = ", ".join(sorted(g["hosts"]))
        codes = ", ".join(str(s) for s in sorted([s for s in g["status_codes"] if s is not None]))
        qp = ", ".join(sorted(g["query_params"])) or "—"
        lines.append(f"| {method} | `{path}` | {hosts} | {g['count']} | {codes} | {qp} |")

    lines.append("")
    lines.append("## Détail par endpoint")
    for (method, path), g in sorted(groups.items()):
        lines.append("")
        lines.append(f"### {method} `{path}`")
        lines.append(f"- Host(s) : {', '.join(sorted(g['hosts']))}")
        lines.append(f"- Occurrences dans la capture : {g['count']}")
        lines.append(f"- Codes statut observés : {', '.join(str(s) for s in sorted([s for s in g['status_codes'] if s is not None]))}")
        if g["query_params"]:
            lines.append(f"- Paramètres query observés : {', '.join(sorted(g['query_params']))}")
        ex = g["examples"][0] if g["examples"] else None
        if ex:
            lines.append("- Exemple de réponse (échantillonnée) :")
            lines.append("```json")
            lines.append(json.dumps(ex.get("response_body"), ensure_ascii=False, indent=2)[:3000])
            lines.append("```")

    (out_dir / "api_inventory.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"OK — {total} requêtes, {len(groups)} endpoints uniques.")
    print(f"-> {out_dir / 'api_inventory.md'}")
    print(f"-> {out_dir / 'api_inventory.json'}")


if __name__ == "__main__":
    main()
