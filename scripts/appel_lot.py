"""Chargement d'un lot d'appels dans Edumoov (ex. un mois saisi sur papier).

Essai à blanc par défaut : rien n'est écrit, seuls des compteurs s'affichent.
--confirm écrit réellement (exige EDUMOOV_ENABLE_WRITES=1 et l'accord explicite
de la personne responsable de l'appel). Mêmes règles que l'outil MCP
edumoov_appeal_prepare_halfday (voir edumoov_mcp/appeals.py).

Usage :
  .venv/bin/python scripts/appel_lot.py lot.json [--confirm] [--remplacer]
      [--classe ID] [--date AAAA-MM-JJ] [--pause 1] [--journal chemin.jsonl]

lot.json (données réelles : à garder HORS du dépôt) :
  {"justification": "pendingjustification",          # optionnel
   "classes": {"<classroom_id Edumoov>": {
       "<AAAA-MM-JJ>": {"am": ["NOM Prénom", ...], "pm": [...]}}}}
Une demi-journée vaut soit la liste des absents, soit un objet avec les
pointages d'activité (listes de noms ; clé omise = valeur existante gardée) :
  {"absents": [...], "eat": [...], "study": [...], "play": [...]}
eat = cantine (matin), study = étude, play = périscolaire (garderie du matin
sur "am", du soir sur "pm").
Une demi-journée listée (même sans absent = tous présents) est enregistrée ;
une demi-journée absente du fichier est ignorée.

Une demi-journée déjà saisie dans Edumoov est laissée telle quelle, sauf
--remplacer (mise à jour : pointages cantine/étude/périscolaire conservés).
Un nom introuvable ou ambigu bloque sa demi-journée (rien n'est envoyé pour elle).
Sortie et journal : compteurs et identifiants seulement, jamais de nom d'élève.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import pathlib
import sys

from edumoov_mcp.appeals import (
    ACTIVITIES,
    JUSTIFICATIONS,
    UPSERT_METHOD,
    build_rows,
    ignored_pupils,
    match_pupils,
    parse_halfday,
    upsert_call,
)
from edumoov_mcp.client import EdumoovClient
from edumoov_mcp.config import SETTINGS


def _log(journal: pathlib.Path | None, entry: dict) -> None:
    if journal:
        with journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def run(args: argparse.Namespace) -> int:
    lot = json.loads(pathlib.Path(args.lot).read_text(encoding="utf-8"))
    justification = lot.get("justification", "pendingjustification")
    if justification not in JUSTIFICATIONS:
        raise SystemExit(f"Motif inconnu : {justification!r}")
    if args.confirm and not SETTINGS.enable_writes:
        raise SystemExit("--confirm refusé : EDUMOOV_ENABLE_WRITES n'est pas à 1.")
    sid = str(SETTINGS.default_school_id or "")
    if not sid:
        raise SystemExit("EDUMOOV_DEFAULT_SCHOOL_ID requis.")
    journal = pathlib.Path(args.journal).expanduser() if args.journal else None
    client = EdumoovClient()
    total = {"envoyees": 0, "a_envoyer": 0, "deja_faites": 0, "bloquees": 0, "erreurs": 0, "absences": 0}
    try:
        classrooms = {
            int(c["id"]): c.get("name")
            for c in await client.list_classrooms()
            if str(c.get("school_id")) == sid and not c.get("release") and not c.get("deleted")
        }
        for cid_s, days in lot["classes"].items():
            cid = int(cid_s)
            if args.classe and cid != int(args.classe):
                continue
            if cid not in classrooms:
                print(f"classe {cid} : inconnue pour l'école {sid}", file=sys.stderr)
                total["bloquees"] += sum(len(v) for v in days.values())
                continue
            pupils = await client.list_pupils(str(cid))
            pupil_ids = sorted(int(p["id"]) for p in pupils)
            ignore = ignored_pupils(await client.get_scope_settings("classroom", str(cid), "Appel"))
            dates = sorted(d for d in days if not args.date or d == args.date)
            if not dates:
                continue
            existing_all = await client.list_appeals(sid, start=dates[0], stop=dates[-1], classroom_id=str(cid))
            bilan = {"envoyees": 0, "deja_faites": 0, "bloquees": 0, "absences": 0}
            for date in dates:
                for period, spec in sorted(days[date].items()):
                    day, pm = parse_halfday(date, period)
                    if isinstance(spec, list):
                        spec = {"absents": spec}
                    groups = {k: spec.get(k) for k in ("absents", *ACTIVITIES) if spec.get(k) is not None}
                    found, missing, ambiguous = match_pupils(sorted({n for v in groups.values() for n in v}), pupils)
                    entry = {"t": datetime.datetime.now().isoformat(timespec="seconds"),
                             "classroom_id": cid, "date": day, "pm": pm}
                    if missing or ambiguous:
                        bilan["bloquees"] += 1
                        print(f"classe {cid} {day} {'pm' if pm else 'am'} : bloquée "
                              f"({len(missing)} introuvable(s), {len(ambiguous)} ambigu(s))", file=sys.stderr)
                        _log(journal, {**entry, "statut": "bloquee", "introuvables": len(missing), "ambigus": len(ambiguous)})
                        continue
                    existing = {int(r["pupil_id"]): r for r in existing_all
                                if str(r.get("date", ""))[:10] == day and bool(r.get("pm")) == pm}
                    if existing and not args.remplacer:
                        bilan["deja_faites"] += 1
                        continue
                    absent_ids = {found[n] for n in groups.get("absents", [])}
                    activities = {k: {found[n] for n in v} for k, v in groups.items() if k != "absents"}
                    clashes = sum(len(v & absent_ids) for v in activities.values())
                    if clashes:
                        print(f"classe {cid} {day} {'pm' if pm else 'am'} : {clashes} pointage(s) d'activité "
                              "pour un élève absent en classe", file=sys.stderr)
                    rows = build_rows(pupil_ids, absent_ids, ignore, existing, justification, activities)
                    n_abs = sum(1 for r in rows if not r["present"] and not r["ignore"])
                    if not args.confirm:
                        bilan["envoyees"] += 1
                        bilan["absences"] += n_abs
                        continue
                    params, payload = upsert_call(cid, day, pm, rows)
                    try:
                        await client.rpc_write(UPSERT_METHOD, params, payload)
                    except Exception as exc:  # noqa: BLE001 — on journalise et on continue
                        total["erreurs"] += 1
                        print(f"classe {cid} {day} {'pm' if pm else 'am'} : erreur {type(exc).__name__}", file=sys.stderr)
                        _log(journal, {**entry, "statut": "erreur", "erreur": str(exc)[:200]})
                        continue
                    bilan["envoyees"] += 1
                    bilan["absences"] += n_abs
                    _log(journal, {**entry, "statut": "ok", "eleves": len(rows), "absents": sorted(absent_ids),
                                   **{k: sorted(v) for k, v in activities.items()},
                                   "mode": "maj" if existing else "creation"})
                    await asyncio.sleep(args.pause)
            print(f"classe {cid} : {bilan}")
            for k in ("deja_faites", "bloquees", "absences"):
                total[k] += bilan[k]
            total["envoyees" if args.confirm else "a_envoyer"] += bilan["envoyees"]
    finally:
        await client.aclose()
    print(("ÉCRIT : " if args.confirm else "ESSAI À BLANC (rien écrit) : ") + json.dumps(total))
    return 1 if total["erreurs"] or total["bloquees"] else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lot")
    ap.add_argument("--confirm", action="store_true", help="écrire réellement dans Edumoov")
    ap.add_argument("--remplacer", action="store_true", help="mettre à jour les demi-journées déjà saisies")
    ap.add_argument("--classe", help="limiter à une classe (classroom_id Edumoov)")
    ap.add_argument("--date", help="limiter à une date (AAAA-MM-JJ)")
    ap.add_argument("--pause", type=float, default=1.0, help="secondes entre deux envois (défaut 1)")
    ap.add_argument("--journal", help="fichier .jsonl de journal (hors dépôt)")
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
