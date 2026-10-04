"""Écriture de l'appel (04/10/2026) — appeals.py et appeal_write_tools.py.
Enjeux : le payload reproduit exactement celui du front (batchUpsert), rien ne
part sans aperçu + confirmation, une mise à jour conserve les pointages déjà
saisis, périmètre limité aux classes de l'école et aux élèves de la classe,
pas de date future."""
from __future__ import annotations

import dataclasses
import datetime
import json

import httpx
import pytest
import respx

import edumoov_mcp.appeal_write_tools as awt
import edumoov_mcp.server as server_module
import edumoov_mcp.write_tools as wt
from edumoov_mcp.appeals import (
    build_rows,
    ignored_pupils,
    match_pupils,
    norm_name,
    parse_halfday,
    reset_call,
    upsert_call,
)
from edumoov_mcp.client import EdumoovClient
from edumoov_mcp.writes import ALLOWED_WRITE_METHODS, WriteGate, WritesDisabledError

from ._helpers import fake_auth

RPC = "https://api.edumoov.com/rpc"
REST = "https://www.edumoov.com/api/1.0"
CLASSROOMS = [
    {"id": 101, "name": "CP A", "school_id": 90001, "release": False, "deleted": False},
    {"id": 201, "name": "Autre école", "school_id": 90009, "release": False, "deleted": False},
]
PUPILS = [
    {"id": 1, "firstname": "Zoé", "name": "BERNARD"},
    {"id": 2, "firstname": "Adam", "name": "LEFÈVRE"},
    {"id": 3, "firstname": "Léa", "name": "LEFÈVRE"},
    {"id": 4, "firstname": "Hugo", "name": "MOREAU"},
]


# ---------------------------------------------------------------- logique pure
def test_parse_halfday():
    today = datetime.date(2026, 10, 4)
    assert parse_halfday("2026-09-14", "am", today=today) == ("2026-09-14", False)
    assert parse_halfday("2026-09-14", "après-midi", today=today) == ("2026-09-14", True)
    with pytest.raises(ValueError):
        parse_halfday("2026-10-05", "am", today=today)
    with pytest.raises(ValueError):
        parse_halfday("14/09/2026", "am", today=today)
    with pytest.raises(ValueError):
        parse_halfday("2026-09-14", "soir", today=today)


def test_build_rows_creation_matches_front_format():
    rows = build_rows([1, 2, 3, 4], absent_ids={2}, ignore_ids={4}, existing={})
    assert [r["pupil_id"] for r in rows] == [1, 2, 3, 4]
    assert rows[0] == {
        "pupil_id": 1, "present": True, "eat": False, "eatalt": False, "study": False,
        "play": False, "delay": 0, "arrival": None, "departure": None, "ignore": False,
    }
    assert rows[1]["present"] is False and rows[1]["justification"] == "pendingjustification"
    assert rows[3]["ignore"] is True and rows[3]["present"] is False and rows[3]["justification"] is None
    assert all("id" not in r for r in rows)


def test_build_rows_update_keeps_existing_data():
    existing = {
        1: {"id": "a1", "present": True, "eat": True, "study": True, "delay": 5, "arrival": "08:40"},
        2: {"id": "a2", "present": False, "justification": "justified", "eat": False},
        3: {"id": "a3", "present": True, "eat": True, "delay": 10},
    }
    rows = {r["pupil_id"]: r for r in build_rows([1, 2, 3], {2, 3}, set(), existing, "unjustified")}
    assert rows[1]["id"] == "a1" and rows[1]["eat"] and rows[1]["study"] and rows[1]["delay"] == 5
    assert rows[1]["arrival"] == "08:40"
    assert rows[2]["justification"] == "justified"  # motif déjà saisi conservé
    assert rows[3]["justification"] == "unjustified" and rows[3]["delay"] == 0  # nouvel absent
    assert rows[3]["eat"] is True  # pointage cantine conservé


def test_build_rows_rejects_unknown_justification():
    with pytest.raises(ValueError):
        build_rows([1], set(), set(), {}, "malade")


def test_calls_and_whitelist():
    params, payload = upsert_call(101, "2026-09-14", True, [{"pupil_id": 1}])
    assert params == {"classroom_id": 101}
    assert payload["globalData"] == {"date": "2026-09-14", "pm": True, "classroom_id": 101}
    assert reset_call(101, "2026-09-14", False) == {"classroom_id": 101, "date": "2026-09-14", "am": True}
    assert {"classroom.pupilsappeals.batchUpsert", "classroom.pupilsappeals.resetDay"} <= ALLOWED_WRITE_METHODS
    # justification (school.pupilsappeals.update) et registres : toujours hors liste blanche
    assert "school.pupilsappeals.update" not in ALLOWED_WRITE_METHODS
    assert "school.pupilsappeals.downloadRegisters" not in ALLOWED_WRITE_METHODS


def test_ignored_pupils():
    assert ignored_pupils({"pupils": {"ignore": [4, "7"], "eatalt": []}}) == {4, 7}
    assert ignored_pupils(None) == set()


def test_match_pupils():
    assert norm_name("Lefèvre  Léa") == "LEFEVRE LEA"
    found, missing, ambiguous = match_pupils(
        ["BERNARD ZOE", "Lefèvre Adam", "Hugo MOREAU", "MOREAU Hugo Jean", "DURAND Paul", "LEFEVRE"],
        PUPILS,
    )
    assert found == {"BERNARD ZOE": 1, "Lefèvre Adam": 2, "Hugo MOREAU": 4, "MOREAU Hugo Jean": 4}
    assert missing == ["DURAND Paul"]
    assert ambiguous == ["LEFEVRE"]  # deux élèves LEFÈVRE : jamais de choix au hasard


# ---------------------------------------------------------------- outils MCP
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "_client", EdumoovClient(auth=fake_auth(tmp_path)))
    monkeypatch.setattr(wt, "_gate", WriteGate(enabled=True, ttl_seconds=600))
    monkeypatch.setattr(wt, "SETTINGS", dataclasses.replace(wt.SETTINGS, default_school_id="90001"))
    respx.post(f"{RPC}/user.classrooms.fetch").mock(
        return_value=httpx.Response(200, json={"success": True, "data": CLASSROOMS})
    )
    respx.get(f"{REST}/core/classroom/101/pupils").mock(
        return_value=httpx.Response(200, json={"success": True, "data": PUPILS})
    )
    respx.post(f"{RPC}/classroom.settings.get").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"pupils": {"ignore": [4]}}})
    )


def _mock_existing(rows):
    return respx.post(f"{RPC}/school.pupilsappeals.fetch").mock(
        return_value=httpx.Response(200, json={"success": True, "data": rows})
    )


@respx.mock
async def test_prepare_halfday_then_confirm_sends_previewed_call(env):
    _mock_existing([])
    upsert = respx.post(f"{RPC}/classroom.pupilsappeals.batchUpsert").mock(
        return_value=httpx.Response(200, json={"success": True, "data": []})
    )
    out = await awt.edumoov_appeal_prepare_halfday("101", "2026-09-14", "am", absent_pupil_ids=["2"])
    assert not upsert.called
    assert out["preview"]["mode"] == "création"
    assert out["preview"]["present"] == 2 and out["preview"]["ignored"] == 1
    assert out["preview"]["absent"] == ["LEFÈVRE Adam"]
    await wt.edumoov_write_confirm(out["confirmation_token"])
    sent = json.loads(upsert.calls[0].request.content)
    assert sent["params"] == {"classroom_id": 101}
    assert sent == {"params": out["request"]["params"], "payload": out["request"]["payload"]}
    assert sent["payload"]["globalData"] == {"date": "2026-09-14", "pm": False, "classroom_id": 101}


@respx.mock
async def test_prepare_halfday_update_mode_keeps_ids(env):
    _mock_existing([
        {"id": "x1", "pupil_id": 1, "date": "2026-09-14", "pm": True, "present": True, "eat": True},
        {"id": "x9", "pupil_id": 1, "date": "2026-09-14", "pm": False, "present": False},
    ])
    out = await awt.edumoov_appeal_prepare_halfday("101", "2026-09-14", "pm")
    rows = {r["pupil_id"]: r for r in out["request"]["payload"]["data"]}
    assert out["preview"]["mode"] == "mise à jour"
    assert rows[1]["id"] == "x1" and rows[1]["eat"] is True  # ligne du matin (x9) ignorée


@respx.mock
async def test_prepare_halfday_refuses_unknown_pupil_and_other_school(env):
    _mock_existing([])
    with pytest.raises(ValueError, match="inconnu"):
        await awt.edumoov_appeal_prepare_halfday("101", "2026-09-14", "am", absent_pupil_ids=["99"])
    with pytest.raises(ValueError, match="inconnue"):
        await awt.edumoov_appeal_prepare_halfday("201", "2026-09-14", "am")


async def test_prepare_halfday_refuses_when_writes_disabled(monkeypatch):
    monkeypatch.setattr(wt, "_gate", WriteGate(enabled=False))
    monkeypatch.setattr(wt, "SETTINGS", dataclasses.replace(wt.SETTINGS, default_school_id="90001"))
    with pytest.raises(WritesDisabledError):
        await awt.edumoov_appeal_prepare_halfday("101", "2026-09-14", "am")
    with pytest.raises(WritesDisabledError):
        await awt.edumoov_appeal_prepare_reset("101", "2026-09-14", "am")


@respx.mock
async def test_prepare_reset(env):
    _mock_existing([{"id": "x1", "pupil_id": 1, "date": "2026-09-14", "pm": False, "present": True}])
    reset = respx.post(f"{RPC}/classroom.pupilsappeals.resetDay").mock(
        return_value=httpx.Response(200, json={"success": True, "data": True})
    )
    out = await awt.edumoov_appeal_prepare_reset("101", "2026-09-14", "am")
    assert not reset.called
    await wt.edumoov_write_confirm(out["confirmation_token"])
    assert json.loads(reset.calls[0].request.content)["params"] == {
        "classroom_id": 101, "date": "2026-09-14", "am": True,
    }
    _mock_existing([])
    with pytest.raises(ValueError, match="Aucun appel"):
        await awt.edumoov_appeal_prepare_reset("101", "2026-09-15", "am")


def test_build_rows_activities_exact_or_kept():
    existing = {1: {"id": "a1", "present": True, "eat": True, "play": True}}
    rows = {r["pupil_id"]: r for r in build_rows([1, 2, 4], set(), {4}, existing, activities={"eat": {2, 4}})}
    assert rows[1]["eat"] is False and rows[2]["eat"] is True  # liste fournie : fixée exactement
    assert rows[1]["play"] is True  # activité non fournie : valeur existante gardée
    assert rows[4]["eat"] is False  # élève exclu de l'appel : jamais pointé
    with pytest.raises(ValueError):
        build_rows([1], set(), set(), {}, activities={"garderie": {1}})


@respx.mock
async def test_prepare_halfday_with_activities(env):
    _mock_existing([])
    out = await awt.edumoov_appeal_prepare_halfday(
        "101", "2026-09-14", "am", absent_pupil_ids=["2"], eat_pupil_ids=["1", "2"], play_pupil_ids=["3"]
    )
    rows = {r["pupil_id"]: r for r in out["request"]["payload"]["data"]}
    assert rows[1]["eat"] and rows[3]["play"] and not rows[3]["eat"]
    assert out["preview"]["activities"] == {"Cantine": 2, "Étude": 0, "Périscolaire": 1}
    assert any("absent" in w for w in out["warnings"])  # élève 2 : cantine mais absent
    with pytest.raises(ValueError, match="inconnu"):
        await awt.edumoov_appeal_prepare_halfday("101", "2026-09-14", "am", eat_pupil_ids=["99"])
