"""Tests sur la redaction des champs sensibles élèves (spec-connecteur-mcp-
edumoov.md §4), écrits en réaction directe à l'incident documenté dans
cartographie-edumoov.md §9 : une redaction qui ne s'exécutait jamais, en silence,
à cause d'une hypothèse de typage jamais vérifiée explicitement."""
from __future__ import annotations

import pytest

import edumoov_mcp.server as server_module
from edumoov_mcp.config import SETTINGS
from edumoov_mcp.server import _redact_pupil, edumoov_classroom_pupils_list

SAMPLE_PUPIL = {
    "id": 1,
    "name": "Dupont",
    "firstname": "Alice",
    "birthday": "01/09/2020",
    "ine": "1234567890A",
    "gender": "F",
    "parents": [{"id": 10, "name": "Dupont", "mail": "parent@example.com"}],
}


class _FakeClient:
    def __init__(self, pupils):
        self._pupils = pupils

    async def list_pupils(self, classroom_id):
        return self._pupils


def test_redact_pupil_strips_sensitive_fields():
    redacted = _redact_pupil(SAMPLE_PUPIL)
    for field in SETTINGS.sensitive_pupil_fields:
        assert field not in redacted
    assert redacted["name"] == "Dupont"
    # Les emails de parents restent, volontairement (voir config.py) : nécessaires
    # à l'usage principal envisagé (contacter une famille).
    assert redacted["parents"][0]["mail"] == "parent@example.com"


async def test_pupils_list_redacts_by_default(monkeypatch):
    monkeypatch.setattr(server_module, "_get_client", lambda: _FakeClient([SAMPLE_PUPIL]))
    result = await edumoov_classroom_pupils_list("44571")
    assert "ine" not in result[0]
    assert "birthday" not in result[0]
    assert result[0]["name"] == "Dupont"


async def test_pupils_list_includes_sensitive_fields_when_requested(monkeypatch):
    monkeypatch.setattr(server_module, "_get_client", lambda: _FakeClient([SAMPLE_PUPIL]))
    result = await edumoov_classroom_pupils_list("44571", include_sensitive_fields=True)
    assert result[0]["ine"] == "1234567890A"
    assert result[0]["birthday"] == "01/09/2020"


async def test_pupils_list_refuses_non_list_payload_instead_of_silent_passthrough(monkeypatch):
    """Régression directe de l'incident §9 : si list_pupils() renvoie un jour
    autre chose qu'une liste (ex. une enveloppe non dépaquetée), le tool doit
    échouer bruyamment plutôt que renvoyer la donnée telle quelle en silence."""
    monkeypatch.setattr(
        server_module, "_get_client", lambda: _FakeClient({"success": True, "data": []})
    )
    with pytest.raises(TypeError):
        await edumoov_classroom_pupils_list("44571")
