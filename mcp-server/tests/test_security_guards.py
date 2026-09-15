"""Garde-fous de sécurité testés explicitement, plutôt que vérifiés au cas par cas
(voir spec-connecteur-mcp-edumoov.md §4 et l'esprit général de ce dossier de tests :
transformer une convention en propriété vérifiée automatiquement)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import edumoov_mcp.client as client_module
import edumoov_mcp.server as server_module
from edumoov_mcp.client import ForbiddenEndpointError, _check_allowed


class TestCheckAllowed:
    def test_blocks_pupils_codes_path(self):
        """POST core/classroom/{id}/pupils/codes renvoie les codes d'accès
        individuels des élèves (équivalent à un mot de passe de mineur) — voir
        cartographie-edumoov.md §6.1. Jamais accessible via ce connecteur."""
        with pytest.raises(ForbiddenEndpointError):
            _check_allowed("core/classroom/44571/pupils/codes")

    def test_permits_normal_pupils_path(self):
        _check_allowed("core/classroom/44571/pupils")  # ne doit rien lever

    async def test_rest_get_blocks_before_any_network_call(self):
        """_check_allowed() doit être appelé AVANT toute tentative réseau/auth —
        sinon le garde-fou existe dans le code mais pas forcément sur le vrai
        chemin d'exécution. Pas de mock HTTP ici : si ce test passe sans
        respx.mock actif, c'est la preuve qu'aucune requête n'a été tentée."""
        client = client_module.EdumoovClient.__new__(client_module.EdumoovClient)
        with pytest.raises(ForbiddenEndpointError):
            await client.rest_get("core/classroom/44571/pupils/codes")


def test_classroom_pupils_fetch_rpc_not_called_anywhere():
    """`classroom.pupils.fetch` (la variante RPC de la liste d'élèves, distincte
    de la REST utilisée par list_pupils()) inclut un champ `password` dans son
    schéma — vérifié `None` en test réel le 15/09/2026, mais le nom du champ
    suffit à en faire un point de vigilance permanent (voir cartographie-
    edumoov.md §6.3).

    Ce test ne vérifie pas un comportement à l'exécution : il scanne le code
    source pour repérer un appel à cette méthode RPC. Si ce test casse un jour,
    ce n'est PAS un bug à corriger en le supprimant — c'est le signal que
    quelqu'un a ajouté cet appel sans (re)lire ce commentaire. Avant d'aller plus
    loin : vérifier explicitement que `password` est redacté dans tout retour
    exposé par un outil MCP, puis mettre à jour ce test en connaissance de
    cause."""
    pattern = re.compile(r'\.rpc\(\s*["\']classroom\.pupils\.fetch["\']')
    for module in (client_module, server_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert not pattern.search(source), (
            f"{module.__name__} appelle classroom.pupils.fetch — voir le "
            "docstring de ce test avant d'aller plus loin."
        )
