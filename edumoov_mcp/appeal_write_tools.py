"""Outils MCP d'écriture de l'appel Edumoov (04/10/2026) — en deux temps.

L'appel a valeur de registre : chaque outil ne fait que PRÉPARER l'écriture
(aperçu + jeton, voir writes.py) ; elle n'a lieu qu'avec edumoov_write_confirm,
après accord explicite. Pour un lot (un mois d'appels), voir
scripts/appel_lot.py (essai à blanc par défaut, --confirm pour écrire).
"""
from __future__ import annotations

import datetime
from typing import Any

# server d'abord : il importe en fin de module les outils d'écriture (dont
# write_tools), ce qui évite un import circulaire si ce module est chargé seul.
from .server import _get_client, mcp  # isort: skip
from . import write_tools  # isort: skip
from .appeals import (
    ACTIVITIES,
    JUSTIFICATIONS,
    RESET_METHOD,
    UPSERT_METHOD,
    build_rows,
    ignored_pupils,
    parse_halfday,
    period_label,
    reset_call,
    upsert_call,
)

REGISTER_WARNING = (
    "L'appel Edumoov a valeur de registre d'appel : vérifier la classe, la date "
    "et la liste des absents avant de confirmer."
)


async def _classroom(sid: str, classroom_id: str) -> tuple[int, str]:
    classrooms = await write_tools._school_classrooms(sid)
    cid = int(classroom_id)
    if cid not in classrooms:
        raise ValueError(
            f"Classe {classroom_id} inconnue pour l'école {sid} (ou archivée). "
            "Utiliser edumoov_classrooms_list pour les identifiants."
        )
    return cid, classrooms[cid]


async def _existing_rows(sid: str, cid: int, date: str, pm: bool) -> dict[int, dict[str, Any]]:
    rows = await _get_client().list_appeals(sid, start=date, stop=date, classroom_id=str(cid))
    return {
        int(r["pupil_id"]): r
        for r in rows
        if str(r.get("date", ""))[:10] == date and bool(r.get("pm")) == pm and r.get("pupil_id") is not None
    }


def _day_warnings(date: str) -> list[str]:
    weekday = datetime.date.fromisoformat(date).weekday()
    if weekday == 2:
        return ["Mercredi : pas de classe en temps normal à l'école."]
    if weekday >= 5:
        return ["Samedi ou dimanche."]
    return []


@mcp.tool()
async def edumoov_appeal_prepare_halfday(
    classroom_id: str,
    date: str,
    period: str,
    absent_pupil_ids: list[str] | None = None,
    justification: str = "pendingjustification",
    eat_pupil_ids: list[str] | None = None,
    study_pupil_ids: list[str] | None = None,
    play_pupil_ids: list[str] | None = None,
    school_id: str | None = None,
) -> Any:
    """PRÉPARE l'appel d'une classe pour une demi-journée (rien n'est écrit) :
    tous les élèves présents sauf `absent_pupil_ids` (identifiants Edumoov, voir
    edumoov_classroom_pupils_list). `period` : "am" (matin) ou "pm"
    (après-midi) ; date au format AAAA-MM-JJ, passée ou du jour.
    `justification` des nouveaux absents : pendingjustification (À justifier,
    défaut), unjustified, justified (Motif légitime), justifiedbut (Autre motif).
    Si l'appel existe déjà, il est mis à jour (pointages cantine/étude/
    périscolaire, retards et motifs déjà saisis conservés). Les élèves exclus
    de l'appel dans les réglages de la classe restent exclus.
    Pointages d'activité (identifiants Edumoov ; None = garder l'existant, liste
    = exactement ces élèves) : `eat_pupil_ids` cantine (matin),
    `study_pupil_ids` étude, `play_pupil_ids` périscolaire (garderie du matin
    sur "am", du soir sur "pm"). Renvoie un aperçu et un jeton pour
    edumoov_write_confirm."""
    write_tools._gate.ensure_enabled()
    sid = write_tools._school_id(school_id)
    day, pm = parse_halfday(date, period)
    if justification not in JUSTIFICATIONS:
        raise ValueError(f"Motif inconnu : {justification!r}. Connus : {list(JUSTIFICATIONS)}")
    cid, cname = await _classroom(sid, classroom_id)
    client = _get_client()
    pupils = await client.list_pupils(str(cid))
    names = {int(p["id"]): f"{p.get('name', '')} {p.get('firstname', '')}".strip() for p in pupils}
    absent = {int(x) for x in (absent_pupil_ids or [])}
    activities = {
        act: {int(x) for x in ids}
        for act, ids in (("eat", eat_pupil_ids), ("study", study_pupil_ids), ("play", play_pupil_ids))
        if ids is not None
    }
    unknown = sorted((absent.union(*activities.values()) if activities else absent) - set(names))
    if unknown:
        raise ValueError(f"Élève(s) absent(s) inconnu(s) dans la classe {cid} : {unknown}.")
    settings = await client.get_scope_settings("classroom", str(cid), "Appel")
    ignore = ignored_pupils(settings)
    existing = await _existing_rows(sid, cid, day, pm)
    rows = build_rows(sorted(names), absent, ignore, existing, justification, activities)
    params, payload = upsert_call(cid, day, pm, rows)

    warnings = [REGISTER_WARNING, *_day_warnings(day)]
    ignored_absents = sorted(absent & ignore)
    if ignored_absents:
        warnings.append(f"{len(ignored_absents)} absent(s) indiqué(s) sont exclus de l'appel de la classe : laissés exclus.")
    for act, ids in activities.items():
        clash = ids & absent
        if clash:
            warnings.append(f"{ACTIVITIES[act]} : {len(clash)} élève(s) pointé(s) mais absent(s) en classe.")
    gone = sorted(set(existing) - set(names))
    if gone:
        warnings.append(f"{len(gone)} ligne(s) existante(s) d'élèves qui ne sont plus dans la classe : non modifiées.")
    before_absent = sorted(n for n, r in existing.items() if n in names and not r.get("present") and not r.get("ignore"))
    preview = {
        "classroom": cname,
        "date": day,
        "period": period_label(pm),
        "mode": "mise à jour" if existing else "création",
        "pupils": len(rows),
        "present": sum(1 for r in rows if r["present"]),
        "absent": [names[r["pupil_id"]] for r in rows if not r["present"] and not r["ignore"]],
        "ignored": sum(1 for r in rows if r["ignore"]),
        "justification_new_absents": JUSTIFICATIONS[justification],
        "activities": {
            ACTIVITIES[a]: sum(1 for r in rows if r[a]) for a in ("eat", "study", "play")
        },
    }
    if existing:
        preview["absent_before"] = [names[n] for n in before_absent]
    return write_tools._gate.prepare(
        UPSERT_METHOD,
        params,
        payload,
        summary=(
            f"Appel {cname} — {day} {period_label(pm)} : {preview['present']} présent(s), "
            f"{len(preview['absent'])} absent(s) ({preview['mode']})"
        ),
        preview=preview,
        warnings=warnings,
    )


@mcp.tool()
async def edumoov_appeal_prepare_reset(
    classroom_id: str, date: str, period: str, school_id: str | None = None
) -> Any:
    """PRÉPARE la suppression de l'appel d'une classe pour une demi-journée
    (bouton « Supprimer l'appel » d'Edumoov) — rien n'est écrit. `period` : "am"
    ou "pm". Renvoie un aperçu et un jeton pour edumoov_write_confirm."""
    write_tools._gate.ensure_enabled()
    sid = write_tools._school_id(school_id)
    day, pm = parse_halfday(date, period)
    cid, cname = await _classroom(sid, classroom_id)
    existing = await _existing_rows(sid, cid, day, pm)
    if not existing:
        raise ValueError(f"Aucun appel enregistré pour {cname} le {day} ({period_label(pm)}).")
    return write_tools._gate.prepare(
        RESET_METHOD,
        reset_call(cid, day, pm),
        {},
        summary=f"Supprimer l'appel {cname} — {day} {period_label(pm)} ({len(existing)} ligne(s))",
        preview={"classroom": cname, "date": day, "period": period_label(pm), "rows": len(existing)},
        warnings=[REGISTER_WARNING, "Suppression définitive des lignes d'appel de cette demi-journée."],
    )
