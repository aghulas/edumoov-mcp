"""Outils de lecture « Direction » ajoutés le 01/10/2026 : app Appel et
licences/factures. Méthodes RPC lues dans le bundle front app.edumoov.com puis
validées en lecture réelle (cartographie-edumoov.md §6.11).

L'écriture de l'appel (`classroom.pupilsappeals.batchUpsert`, `resetDay`) est
volontairement NON exposée : le registre d'appel a une valeur réglementaire.
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
