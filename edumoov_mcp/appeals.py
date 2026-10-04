"""Écriture de l'appel Edumoov (04/10/2026) — logique pure, sans réseau.

Méthodes lues dans le bundle front app.edumoov.com (entité `appeals`,
endpointName `pupilsappeals`, voir cartographie-edumoov.md §6.11) :

- `classroom.pupilsappeals.batchUpsert`, params `{classroom_id}`, payload
  `{data: [ligne par élève], globalData: {date, pm, classroom_id}}`. Une ligne
  porte `id` si elle existe déjà (mise à jour), sinon elle est créée. Format
  d'une ligne = `Appeal.cleanDataForPayload` du front : pupil_id, present, eat,
  eatalt, study, play, delay, arrival, departure, ignore, justification.
- `classroom.pupilsappeals.resetDay`, params `{classroom_id, date, am|pm: true}`
  : supprime l'appel d'une demi-journée.

Ce module ne fait que construire ces appels ; l'envoi passe par le garde-fou
(appeal_write_tools.py) ou par scripts/appel_lot.py (--confirm).
"""
from __future__ import annotations

import datetime
import re
import unicodedata
from typing import Any, Iterable

UPSERT_METHOD = "classroom.pupilsappeals.batchUpsert"
RESET_METHOD = "classroom.pupilsappeals.resetDay"

# Motifs d'absence proposés par le front (liste `tw`), dans l'ordre d'affichage.
JUSTIFICATIONS: dict[str, str] = {
    "pendingjustification": "À justifier",
    "unjustified": "Injustifiée",
    "justified": "Motif légitime",
    "justifiedbut": "Autre motif",
}

# Pointages d'activité d'une ligne d'appel (libellés du front, objet `SK`).
ACTIVITIES: dict[str, str] = {
    "eat": "Cantine",
    "eatalt": "Repas alternatif",
    "study": "Étude",
    "play": "Périscolaire",
}

_PERIODS = {
    "am": False,
    "matin": False,
    "pm": True,
    "aprem": True,
    "apresmidi": True,
    "après-midi": True,
    "apres-midi": True,
}


def parse_halfday(date: str, period: str, *, today: datetime.date | None = None) -> tuple[str, bool]:
    """Valide (date ISO, demi-journée) → (date, pm). Refuse une date future :
    on enregistre un appel constaté, pas un appel anticipé."""
    try:
        day = datetime.date.fromisoformat(str(date)[:10])
    except ValueError as exc:
        raise ValueError(f"Date invalide : {date!r} (attendu AAAA-MM-JJ).") from exc
    key = str(period).strip().lower()
    if key not in _PERIODS:
        raise ValueError("period doit être 'am' (matin) ou 'pm' (après-midi).")
    if day > (today or datetime.date.today()):
        raise ValueError(f"Date future refusée : {day.isoformat()}.")
    return day.isoformat(), _PERIODS[key]


def period_label(pm: bool) -> str:
    return "après-midi" if pm else "matin"


def ignored_pupils(appel_settings: Any) -> set[int]:
    """Élèves exclus de l'appel dans les réglages de classe (app Appel,
    `pupils.ignore`)."""
    if not isinstance(appel_settings, dict):
        return set()
    pupils = appel_settings.get("pupils") or {}
    return {int(x) for x in (pupils.get("ignore") or []) if str(x).isdigit()}


def build_rows(
    pupil_ids: Iterable[int],
    absent_ids: set[int],
    ignore_ids: set[int],
    existing: dict[int, dict[str, Any]],
    justification: str = "pendingjustification",
    activities: dict[str, set[int]] | None = None,
) -> list[dict[str, Any]]:
    """Lignes du payload batchUpsert pour une demi-journée.

    `activities` (optionnel) : {"eat" | "eatalt" | "study" | "play": élèves
    pointés}. Une activité fournie est fixée exactement (pointé = vrai, sinon
    faux) ; une activité non fournie garde la valeur déjà enregistrée. Dans
    Edumoov : eat = cantine (matin), study = étude, play = périscolaire
    (garderie du matin sur le matin, du soir sur l'après-midi).

    - Présent par défaut ; absent si dans `absent_ids` ; `ignore` si l'élève est
      exclu de l'appel dans les réglages de la classe.
    - Une ligne déjà enregistrée garde son `id` (mise à jour), ses pointages
      cantine/étude/périscolaire et, pour un présent, son retard et ses heures
      d'arrivée/départ ; pour un absent déjà absent, son motif.
    """
    if justification not in JUSTIFICATIONS:
        raise ValueError(f"Motif inconnu : {justification!r}. Connus : {list(JUSTIFICATIONS)}")
    activities = activities or {}
    bad = [k for k in activities if k not in ACTIVITIES]
    if bad:
        raise ValueError(f"Activité(s) inconnue(s) : {bad}. Connues : {list(ACTIVITIES)}")
    rows: list[dict[str, Any]] = []
    for pid in pupil_ids:
        pid = int(pid)
        ex = existing.get(pid) or {}
        ignore = pid in ignore_ids
        present = (not ignore) and pid not in absent_ids
        row: dict[str, Any] = {}
        if ex.get("id"):
            row["id"] = ex["id"]
        row.update(
            pupil_id=pid,
            present=present,
            **{
                act: (not ignore) and (pid in activities[act] if act in activities else bool(ex.get(act)))
                for act in ACTIVITIES
            },
            delay=int(ex.get("delay") or 0) if present else 0,
            arrival=ex.get("arrival") if present else None,
            departure=ex.get("departure") if present else None,
            ignore=ignore,
        )
        if ignore:
            row["justification"] = None
        elif not present:
            kept = ex.get("justification") if ex and not ex.get("present") else None
            row["justification"] = kept or justification
        rows.append(row)
    return rows


def upsert_call(classroom_id: int, date: str, pm: bool, rows: list[dict[str, Any]]) -> tuple[dict, dict]:
    """(params, payload) de classroom.pupilsappeals.batchUpsert, comme le front."""
    cid = int(classroom_id)
    return (
        {"classroom_id": cid},
        {"data": rows, "globalData": {"date": date, "pm": pm, "classroom_id": cid}},
    )


def reset_call(classroom_id: int, date: str, pm: bool) -> dict[str, Any]:
    """params de classroom.pupilsappeals.resetDay (payload vide)."""
    return {"classroom_id": int(classroom_id), "date": date, "pm" if pm else "am": True}


def norm_name(text: str) -> str:
    """Nom comparable : sans accents, majuscules, séparateurs réduits à un espace."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()


def match_pupils(
    names: Iterable[str], pupils: list[dict[str, Any]]
) -> tuple[dict[str, int], list[str], list[str]]:
    """Associe des noms « NOM Prénom » (ou « Prénom NOM ») aux élèves Edumoov.

    Correspondance exacte d'abord, puis par inclusion de mots (prénoms composés
    ou seconds prénoms présents d'un seul côté), retenue seulement si elle est
    unique. Renvoie (nom → pupil_id, introuvables, ambigus)."""
    index: list[tuple[int, str, str]] = []
    for p in pupils:
        nom, prenom = p.get("name") or "", p.get("firstname") or ""
        index.append((int(p["id"]), norm_name(f"{nom} {prenom}"), norm_name(f"{prenom} {nom}")))
    found: dict[str, int] = {}
    missing: list[str] = []
    ambiguous: list[str] = []
    for name in names:
        key = norm_name(name)
        exact = {pid for pid, a, b in index if key in (a, b)}
        if len(exact) == 1:
            found[name] = exact.pop()
            continue
        if len(exact) > 1:
            ambiguous.append(name)
            continue
        words = set(key.split())
        partial = {
            pid
            for pid, a, _ in index
            if a and (set(a.split()) <= words or words <= set(a.split()))
        }
        if len(partial) == 1:
            found[name] = partial.pop()
        elif partial:
            ambiguous.append(name)
        else:
            missing.append(name)
    return found, missing, ambiguous
