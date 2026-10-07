"""Codes familles Educartable en PDF (07/10/2026) — à remettre aux parents.

Dans Educartable, page « Codes familles » d'une classe : l'enseignant ou la
direction sélectionne des élèves, puis « Exporter en pdf ». Le front appelle
POST www.edumoov.com/api/1.0/core/classroom/{id}/pupils/codes avec la liste des
élèves (payload = [id, …]) et reçoit pour chacun {name, firstname, key, code} :
l'identifiant et le mot de passe qui permettent à la famille de créer son compte
sur le portail familles. Le PDF est ensuite fabriqué dans le navigateur sur un
modèle d'image (pdf-parents/modele.jpg) ; ce module reproduit cette mise en page,
un fichier par élève, pour qu'il puisse être joint au mail adressé à ses parents
(usage prévu par Edumoov : document imprimé glissé dans le cartable).

Garde-fous (décision de Rémi du 07/10/2026, qui remplace l'exclusion initiale) :
- outil LOCAL uniquement : refusé sans EDUMOOV_DOWNLOAD_DIR et sans
  EDUMOOV_FAMILY_CODES=1 (jamais défini sur le serveur distant) ;
- élèves vérifiés comme appartenant à la classe ; 30 au plus par appel ;
- les codes ne sont JAMAIS renvoyés à l'appelant ni journalisés : l'outil ne
  renvoie que le nom de l'élève et le chemin du PDF ;
- un PDF par élève, droits 600, jamais écrasé, sous EDUMOOV_DOWNLOAD_DIR/codes ;
- le modèle d'image n'est téléchargé que depuis static.edumoov.com.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

from .config import SETTINGS

logger = logging.getLogger("edumoov_mcp.family_codes")

MAX_PUPILS = 30
TEMPLATE_URL = "https://static.edumoov.com/cartable/pdf-parents/modele.jpg"

RGPD_PRIVE = [
    ("b", "Informations importantes"),
    ("n+", "Cette application se conforme au RGPD et à la loi Informatique et libertés."),
    ("n+", "Le Responsable des Traitements est le chef d’établissement. Il peut sur demande vous fournir l’adresse du Délégué à la"),
    ("n", "Protection des données s’il a été désigné."),
    ("n+", "Les finalités des traitements sont les suivantes :"),
    ("n", "- publier en ligne les livrets scolaires de vos enfants, faire l’appel numérique. (Application Edulivret)"),
    ("n", "et/ou"),
    ("n", "- communiquer via les cahiers de textes, de liaison et de vie numériques. (Application Educartable et portail familles)"),
    ("n+", "Les traitements de données sont confiés à la société Edumoov qui agit donc pour le compte du Responsable de"),
    ("n", "Traitements en qualité de sous-traitant."),
    ("n+", "Le détail des données traitées par application sont consultables dans les fiches de registre de traitements"),
    ("n", "accessibles ici: https://legal.edumoov.com/register/"),
    ("n+", "Les données sont hébergées en France."),
    ("n+", "Les destinataires de ces données sont les enseignants et les familles concernées, la société Edumoov et"),
    ("n", "ses sous-traitants. Les destinataires sont décrits de manière précise dans chacune des fiches de registre."),
    ("n+", "La durée de conservation des ces données varient selon les obligations légales et les applications. Dans la plupart des"),
    ("n", "cas, les données sont supprimées des serveurs trois mois maximum après la suppression d’un enfant sur l’interface. Le"),
    ("n", "détail des durées de conservation des données est accessible également dans chacune des fiches de registre."),
    ("n+", "Vous pouvez à tout moment exercer vos droits relatifs aux données vous concernant ou concernant vos enfants auprès"),
    ("n", "du Responsable de Traitement, comme par exemple :"),
    ("n", "- Vous permettre de consulter, modifier, exporter vos données ;"),
    ("n", "- Vous permettre d’en effacer toutes traces si cette demande est compatible avec l’exercice de nos missions ;"),
    ("n", "- Vous informer pour vous permettre de comprendre :"),
    ("i", "- Quelles sont les données personnelles récoltées sur vous, et sur votre (vos) enfant(s) ;"),
    ("i", "- Où, comment et par qui elles sont traitées ;"),
    ("i", "- Ce qui en est précisément fait ;"),
    ("i", "- Et quelles sont les mesures de sécurité prises pour les protéger."),
    ("n+", "Il vous est également possible d’interpeler la CNIL en tant qu’autorité de contrôle à l’adresse :"),
    ("n", "https://www.cnil.fr/fr/cnil-direct"),
]


def codes_enabled() -> None:
    if not SETTINGS.download_dir:
        raise ValueError("Codes familles désactivés : définir EDUMOOV_DOWNLOAD_DIR (mode local uniquement).")
    if os.environ.get("EDUMOOV_FAMILY_CODES", "") != "1":
        raise ValueError("Codes familles désactivés : définir EDUMOOV_FAMILY_CODES=1 dans la configuration "
                         "locale du serveur MCP (jamais sur le serveur distant).")


def codes_folder() -> Path:
    folder = Path(SETTINGS.download_dir).expanduser() / "codes"
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    return folder


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return "_".join(re.sub(r"[^A-Za-z0-9 -]+", "", s).split())[:60] or "eleve"


def pdf_path(folder: Path, classe: str, name: str, firstname: str) -> Path:
    base = f"codes_famille_{_slug(classe)}_{_slug(name)}_{_slug(firstname)}"
    p = folder / f"{base}.pdf"
    n = 2
    while p.exists():
        p = folder / f"{base}__{n}.pdf"
        n += 1
    return p


def _latin(s: str) -> str:
    """Les polices de base de fpdf sont en latin-1 : remplace les caractères hors table."""
    return (s or "").replace("’", "'").replace("–", "-").encode("latin-1", "replace").decode("latin-1")


def build_pdf(entry: dict[str, Any], template: Path | None) -> bytes:
    """Une page A4 portrait reproduisant le modèle Educartable (positions du front,
    décalées pour une seule fiche centrée)."""
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    x0, y0 = 36.0, 20.0  # coin du modèle (138 x 193 mm) ; le front le place à (10, 9)
    if template and template.exists():
        pdf.image(str(template), x=x0, y=y0, w=138, h=193)
    nom = f"{entry.get('firstname', '')} {entry.get('name', '')}".upper()
    pdf.set_font("helvetica", "B", 16)
    pdf.set_text_color(255, 0, 0)
    w = pdf.get_string_width(_latin(nom))
    pdf.text(x0 + 71 - w / 2, y0 + 27, _latin(nom))
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("helvetica", "B", 13)
    pdf.text(x0 + 99, y0 + 71, _latin(str(entry.get("key", ""))))
    pdf.text(x0 + 99, y0 + 77, _latin(str(entry.get("code", ""))))
    y, step, x = y0 + 99, 2.8, x0 + 5
    pdf.set_text_color(38, 50, 56)
    for i, (style, line) in enumerate(RGPD_PRIVE):
        if i:
            y += step + (1 if style.endswith("+") else 0)
        pdf.set_font("helvetica", "B" if style == "b" else "", 6.8)
        pdf.text(x + (5 if style == "i" else 0), y, _latin(line))
    return bytes(pdf.output())


def write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


async def _template(client) -> Path | None:
    """Modèle d'image Educartable, mis en cache dans le dossier des codes."""
    folder = codes_folder()
    p = folder / ".modele_educartable.jpg"
    if p.exists():
        return p
    try:
        resp = await client._http.get(TEMPLATE_URL, timeout=30)
        if resp.status_code == 200 and resp.content[:2] == b"\xff\xd8":
            write_private(p, resp.content)
            return p
    except Exception:  # noqa: BLE001 — sans modèle, la fiche reste lisible
        logger.warning("Modèle de fiche Educartable indisponible, PDF sans fond.")
    return None


# ----------------------------------------------------------------------
# Outil MCP
# ----------------------------------------------------------------------
from .server import _get_client, mcp  # noqa: E402


@mcp.tool()
async def edumoov_family_codes_pdf(classroom_id: str, pupil_ids: list[str]) -> Any:
    """Génère, pour chaque élève demandé, la fiche « codes familles » Educartable en
    PDF (identifiant et mot de passe permettant aux parents de créer leur compte
    sur le portail familles), comme le bouton « Exporter en pdf » de la page Codes
    familles de la classe. UN fichier par élève, à joindre au mail adressé à SES
    parents (ou à imprimer pour le cartable).

    LOCAL uniquement (EDUMOOV_DOWNLOAD_DIR et EDUMOOV_FAMILY_CODES=1). Les codes ne
    sont jamais renvoyés ici : seulement le nom de l'élève et le chemin du PDF
    (EDUMOOV_DOWNLOAD_DIR/codes, droits 600). Élèves de la classe uniquement, 30 au
    plus. Ne jamais recopier le contenu d'un PDF dans la conversation ni l'envoyer
    à une autre famille que celle de l'élève."""
    codes_enabled()
    ids = [str(int(x)) for x in pupil_ids]
    if not ids:
        raise ValueError("Aucun élève demandé.")
    if len(ids) > MAX_PUPILS:
        raise ValueError(f"{MAX_PUPILS} élèves au plus par appel.")
    client = _get_client()
    classe = (await client.get_classroom(classroom_id) or {}).get("name") or str(classroom_id)
    eleves = {str(p["id"]): p for p in await client.list_pupils(str(classroom_id))}
    inconnus = [i for i in ids if i not in eleves]
    if inconnus:
        raise ValueError(f"Élève(s) absent(s) de la classe {classroom_id} : {inconnus}.")
    data = await client.fetch_family_codes(str(classroom_id), [int(i) for i in ids])
    template = await _template(client)
    folder = codes_folder()
    fichiers = []
    for entry in data:
        path = pdf_path(folder, classe.split("(")[0].strip(), entry.get("name", ""), entry.get("firstname", ""))
        write_private(path, build_pdf(entry, template))
        fichiers.append({"eleve": f"{entry.get('name', '')} {entry.get('firstname', '')}".strip(), "fichier": str(path)})
    logger.info("Codes familles : %d fiche(s) PDF générée(s).", len(fichiers))
    return {
        "classe": classe,
        "nb_fiches": len(fichiers),
        "fichiers": fichiers,
        "manquants": max(0, len(ids) - len(fichiers)),
        "note": ("Fiches enregistrées en local (droits 600). Joindre chaque fiche au seul mail des parents de "
                 "l'élève ; ne pas recopier les codes dans la conversation."),
    }
