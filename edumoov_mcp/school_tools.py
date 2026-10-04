"""Outils de lecture « Direction » ajoutés le 01/10/2026 : app Appel et
licences/factures. Méthodes RPC lues dans le bundle front app.edumoov.com puis
validées en lecture réelle (cartographie-edumoov.md §6.11).

L'écriture de l'appel (`classroom.pupilsappeals.batchUpsert`, `resetDay`) est dans
appeal_write_tools.py (04/10/2026), toujours en deux temps (aperçu puis
confirmation) : le registre d'appel a une valeur réglementaire.
"""
from __future__ import annotations

import datetime
from typing import Any

from .config import SETTINGS
from .server import _get_client, mcp

PRODUCT_LABELS = {"NOTEBOOK": "Livret", "CARTABLE": "Cartable", "CLOG": "Journal"}
ACTIVE_STATUSES = ("valid", "unpayed")  # repris du front (Subscription.ACTIVE_STATUSES)


def _school_id(school_id: str | None) -> str:
    resolved = school_id or SETTINGS.default_school_id
    if not resolved:
        raise ValueError("school_id requis (ou EDUMOOV_DEFAULT_SCHOOL_ID).")
    return str(resolved)


def _days_until(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        when = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (when.date() - datetime.date.today()).days


@mcp.tool()
async def edumoov_appeals_stats(
    date: str | None = None,
    period: str = "day",
    classroom_id: str | None = None,
    school_id: str | None = None,
) -> Any:
    """Synthèse de l'appel par classe et demi-journée (présents, absents,
    cantine, étude, garderie, retards). `period` : "day" (défaut, `date` =
    aujourd'hui) ou "month" (`date` = n'importe quel jour du mois). Liste vide
    si l'appel n'a pas été fait dans Edumoov (app peu utilisée à l'école : 25
    lignes seulement depuis septembre 2025 au 01/10/2026)."""
    sid = _school_id(school_id)
    day = date or datetime.date.today().isoformat()
    if period == "month":
        day = day[:8] + "01"
    elif period != "day":
        raise ValueError("period doit être 'day' ou 'month'.")
    return await _get_client().appeal_stats(sid, date=day, period=period, classroom_id=classroom_id)


@mcp.tool()
async def edumoov_appeals_list(
    start: str,
    stop: str | None = None,
    classroom_id: str | None = None,
    school_id: str | None = None,
) -> Any:
    """Lignes d'appel détaillées (une par élève et par demi-journée) entre
    `start` et `stop` (dates ISO, `stop` = `start` par défaut), éventuellement
    pour une seule classe. Les élèves sont identifiés par `pupil_id` (voir
    edumoov_classroom_pupils_list pour les noms)."""
    sid = _school_id(school_id)
    return await _get_client().list_appeals(
        sid, start=start, stop=stop or start, classroom_id=classroom_id
    )


@mcp.tool()
async def edumoov_subscriptions_list(
    school_id: str | None = None, active_only: bool = True
) -> Any:
    """Licences Edumoov de l'école (Direction → Licences et factures) : produit
    (Livret, Cartable, Journal), classe ou enseignant bénéficiaire, statut, dates
    de début/fin, jours avant expiration et facture associée (date, montant,
    payée ou non, échéance). `active_only=False` inclut l'historique expiré.
    Les codes d'accès (licence et paiement) ne sont jamais renvoyés."""
    sid = _school_id(school_id)
    client = _get_client()
    rows = await client.list_subscriptions(sid)
    classrooms = {
        int(c["id"]): c.get("name") for c in await client.list_classrooms()
    }
    teachers = {
        int(t["id"]): f"{t.get('firstname', '')} {t.get('name', '')}".strip()
        for t in await client.list_school_teachers(sid)
    }
    out = []
    for r in rows:
        if active_only and r.get("status") not in ACTIVE_STATUSES:
            continue
        model = r.get("link_model")
        key = r.get("link_key")
        if model == "Classroom":
            beneficiary = classrooms.get(int(key), f"classe {key}")
        elif model == "User":
            beneficiary = teachers.get(int(key), f"utilisateur {key}")
        else:
            beneficiary = f"{model} {key}"
        invoice = r.get("invoice") or {}
        out.append(
            {
                "id": r.get("id"),
                "product": PRODUCT_LABELS.get(r.get("product"), r.get("product")),
                "beneficiary_type": model,
                "beneficiary": beneficiary,
                "status": r.get("status"),
                "type": r.get("type"),
                "start": r.get("start"),
                "expiration": r.get("expiration"),
                "days_until_expiration": _days_until(r.get("expiration")),
                "bill_id": r.get("bill_id") or None,
                "invoice": {
                    k: invoice.get(k)
                    for k in ("bill_date", "total_price", "payed", "paiement_date", "paiement_type", "limit_date", "cancelled")
                }
                if invoice
                else None,
            }
        )
    out.sort(key=lambda x: (x["days_until_expiration"] is None, x["days_until_expiration"] or 0))
    return out


# ----------------------------------------------------------------------
# Registres d'appel (04/10/2026)
# ----------------------------------------------------------------------
REGISTER_TYPES = {"appeal": "appel", "eat": "cantine", "study": "étude", "play": "périscolaire"}
_TYPE_ALIASES = {
    "appel": "appeal", "presence": "appeal", "présence": "appeal",
    "cantine": "eat", "etude": "study", "étude": "study",
    "periscolaire": "play", "périscolaire": "play", "garderie": "play",
}
JOB_RUNNING = (3, 4, 5, 6)
JOB_DONE = 7


def _register_types(types: list[str] | None) -> list[str]:
    if not types:
        return list(REGISTER_TYPES)
    out = []
    for t in types:
        key = str(t).strip().lower()
        key = _TYPE_ALIASES.get(key, key)
        if key not in REGISTER_TYPES:
            raise ValueError(f"Type de registre inconnu : {t!r}. Connus : {list(REGISTER_TYPES)} (ou appel, cantine, étude, périscolaire).")
        if key not in out:
            out.append(key)
    return out


def _register_months(months: list[str] | str) -> list[str]:
    items = [months] if isinstance(months, str) else list(months or [])
    if not items:
        raise ValueError("Au moins un mois est requis (AAAA-MM).")
    out = []
    for m in items:
        try:
            day = datetime.date.fromisoformat(str(m)[:7] + "-01")
        except ValueError as exc:
            raise ValueError(f"Mois invalide : {m!r} (attendu AAAA-MM).") from exc
        if day.isoformat() not in out:
            out.append(day.isoformat())
    return sorted(out)


def _slug(text: str) -> str:
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:40] or "classe"


def _free_path(folder, stem: str, ext: str):
    path = folder / f"{stem}{ext}"
    n = 2
    while path.exists():
        path = folder / f"{stem}_{n}{ext}"
        n += 1
    return path


@mcp.tool()
async def edumoov_registers_download(
    months: list[str],
    classroom_ids: list[str] | None = None,
    types: list[str] | None = None,
    color: bool = True,
    extract: bool = False,
    school_id: str | None = None,
) -> Any:
    """Télécharge les registres d'appel d'Edumoov (Direction → Appel →
    Registres) et les enregistre dans le dossier local EDUMOOV_DOWNLOAD_DIR.
    `months` : mois au format AAAA-MM ; `classroom_ids` : une ou plusieurs
    classes (None = toutes les classes actives de l'école) ; `types` : appel,
    cantine, étude, périscolaire (None = les quatre) ; `color` : registres en
    couleur (défaut) ou, avec False, en noir et blanc (fichier suffixé « _nb »). Edumoov génère une archive zip (un PDF par type et par mois, toutes
    classes réunies) ; `extract=True` la décompresse aussi dans un dossier voisin.
    Renvoie le chemin du fichier et la liste des PDF — jamais l'URL de
    téléchargement (signée, valable 12 h sans authentification). Ne modifie
    aucune donnée d'appel."""
    import asyncio
    import io
    import os
    import pathlib
    import zipfile

    if not SETTINGS.download_dir:
        raise ValueError(
            "Téléchargement désactivé : définir EDUMOOV_DOWNLOAD_DIR (dossier local) dans "
            "la configuration du serveur MCP (mode local uniquement)."
        )
    folder = pathlib.Path(SETTINGS.download_dir).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    sid = _school_id(school_id)
    kinds = _register_types(types)
    month_list = _register_months(months)
    client = _get_client()
    classrooms = {
        int(c["id"]): c.get("name") or str(c["id"])
        for c in await client.list_classrooms()
        if str(c.get("school_id")) == sid and not c.get("release") and not c.get("deleted")
    }
    if classroom_ids is None:
        ids = sorted(classrooms)
    else:
        ids = [int(x) for x in classroom_ids]
        unknown = [i for i in ids if i not in classrooms]
        if unknown:
            raise ValueError(f"Classe(s) inconnue(s) pour l'école {sid} : {unknown}.")
    if not ids:
        raise ValueError("Aucune classe à exporter.")

    job = await client.start_registers_job(sid, types=kinds, months=month_list, classroom_ids=ids, color=color)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SETTINGS.job_timeout_seconds
    status = job.get("status")
    while status in JOB_RUNNING or status is None:
        if loop.time() > deadline:
            raise TimeoutError(
                f"Génération des registres toujours en cours après {SETTINGS.job_timeout_seconds} s "
                "(le fichier sera aussi proposé dans les notifications Edumoov)."
            )
        await asyncio.sleep(2)
        job = await client.get_job(str(job["id"]))
        status = job.get("status")
    if status != JOB_DONE:
        err = job.get("error")
        msg = err.get("message") if isinstance(err, dict) else err
        raise RuntimeError(f"Génération des registres en échec (statut {status}) : {msg or 'sans détail'}.")
    url = ((job.get("result") or {}).get("download") or {}).get("url")
    if not url:
        raise RuntimeError("Job terminé sans fichier à télécharger.")
    content, _ = await client.fetch_job_file(url)

    month_part = month_list[0][:7] if len(month_list) == 1 else f"{month_list[0][:7]}_a_{month_list[-1][:7]}"
    if classroom_ids is None:
        who = "toutes-classes"
    elif len(ids) <= 3:
        who = "_".join(_slug(classrooms[i]) for i in ids)
    else:
        who = f"{len(ids)}-classes"
    stem = f"registres_{month_part}_{who}" + ("" if color else "_nb")
    path = _free_path(folder, stem, ".zip")
    path.write_bytes(content)
    os.chmod(path, 0o600)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            extracted = None
            if extract:
                target = _free_path(folder, stem, "")
                target.mkdir()
                for n in names:
                    dest = (target / n).resolve()
                    if not str(dest).startswith(str(target.resolve()) + os.sep):
                        raise ValueError(f"Chemin suspect dans l'archive : {n!r}")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(n))
                    os.chmod(dest, 0o600)
                extracted = str(target)
    except zipfile.BadZipFile:
        names, extracted = [], None
    return {
        "file": str(path),
        "size": len(content),
        "extracted_to": extracted,
        "pdf_files": names,
        "classrooms": [classrooms[i] for i in ids],
        "months": [m[:7] for m in month_list],
        "types": [REGISTER_TYPES[k] for k in kinds],
        "color": bool(color),
    }
