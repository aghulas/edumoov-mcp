"""Écritures du Cartable (01/10/2026) — cahier de liaison et commentaires.
Mêmes enjeux que test_writes.py : rien ne part sans aperçu + confirmation,
l'appel confirmé est exactement celui prévisualisé, création toujours en
brouillon, publication impossible sans destinataire, périmètre limité aux
classes de l'école et aux élèves de la classe."""
from __future__ import annotations

import dataclasses
import json

import httpx
import pytest
import respx

import edumoov_mcp.cartable_write_tools as cwt
import edumoov_mcp.server as server_module
import edumoov_mcp.write_tools as wt
from edumoov_mcp.client import EdumoovClient
from edumoov_mcp.writes import ConfirmationError, WriteGate, WritesDisabledError

from ._helpers import fake_auth

RPC = "https://api.edumoov.com/rpc"
REST = "https://www.edumoov.com/api/1.0"
MSG = "0b5c2e3a-1111-4222-8333-444455556666"
CLASSROOMS = [
    {"id": 101, "name": "CP A", "school_id": 90001, "release": False, "deleted": False},
    {"id": 201, "name": "Autre école", "school_id": 90009, "release": False, "deleted": False},
]
PUPILS = [
    {"id": 1, "fullname": "Zoé B", "firstname": "Zoé", "name": "B"},
    {"id": 2, "fullname": "Adam A", "firstname": "Adam", "name": "A"},
]


def _message(**over):
    m = {
        "id": MSG,
        "type": "info",
        "title": "Sortie",
        "body": "Texte",
        "visible": False,
        "visibility": None,
        "commentable": True,
        "achievement": "",
        "recipients": [{"type": "Pupil", "value": 1, "name": "Zoé B"}],
        "scope_model": "Classroom",
        "scope_key": "101",
        "comments_count": 0,
    }
    m.update(over)
    return m


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


def _mock_get(message):
    respx.get(f"{REST}/cartable/classroom/101/messages/{MSG}").mock(
        return_value=httpx.Response(200, json={"success": True, "data": message})
    )


# ---------------------------------------------------------------- création
@respx.mock
async def test_create_is_draft_whole_class_then_confirm(env):
    post = respx.post(f"{REST}/cartable/classroom/101/messages").mock(
        return_value=httpx.Response(201, json={"success": True, "data": {"id": MSG, "visible": False, "body": "x"}})
    )
    out = await cwt.edumoov_cahier_prepare_create("101", "Sortie", "Texte")
    assert not post.called
    payload = out["request"]["payload"]
    assert payload["visible"] is False and payload["visibility"] is None
    assert payload["recipients"] == [
        {"type": "Pupil", "value": 2, "name": "Adam A"},
        {"type": "Pupil", "value": 1, "name": "Zoé B"},
    ]
    assert out["request"]["rest"] == "POST cartable/classroom/101/messages"
    res = await wt.edumoov_write_confirm(out["confirmation_token"])
    sent = json.loads(post.calls.last.request.content)
    assert sent == payload
    assert res["item"] == {"id": MSG, "visible": False}  # jamais le corps
    with pytest.raises(ConfirmationError):
        await wt.edumoov_write_confirm(out["confirmation_token"])


@respx.mock
async def test_create_refuses_pupil_outside_class(env):
    with pytest.raises(ValueError):
        await cwt.edumoov_cahier_prepare_create("101", "T", "B", pupil_ids=["999"])


@respx.mock
async def test_create_refuses_other_school_classroom(env):
    with pytest.raises(ValueError):
        await cwt.edumoov_cahier_prepare_create("201", "T", "B")


@respx.mock
async def test_create_refuses_bad_type_and_ack(env):
    with pytest.raises(ValueError):
        await cwt.edumoov_cahier_prepare_create("101", "T", "B", message_type="lesson")
    with pytest.raises(ValueError):
        await cwt.edumoov_cahier_prepare_create("101", "T", "B", acknowledgement="signature")


@respx.mock
async def test_disabled_gate_blocks(env, monkeypatch):
    monkeypatch.setattr(wt, "_gate", WriteGate(enabled=False))
    with pytest.raises(WritesDisabledError):
        await cwt.edumoov_cahier_prepare_create("101", "T", "B")


# ---------------------------------------------------------------- modification / publication
@respx.mock
async def test_update_partial_payload(env):
    _mock_get(_message())
    put = respx.put(f"{REST}/cartable/classroom/101/messages/{MSG}").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"id": MSG}})
    )
    out = await cwt.edumoov_cahier_prepare_update("101", MSG, title="Sortie au musée", acknowledgement="read")
    assert out["request"]["payload"] == {"title": "Sortie au musée", "achievement": "read"}
    assert out["warnings"] == []
    await wt.edumoov_write_confirm(out["confirmation_token"])
    assert json.loads(put.calls.last.request.content) == out["request"]["payload"]


@respx.mock
async def test_update_noop_refused(env):
    _mock_get(_message())
    with pytest.raises(ValueError):
        await cwt.edumoov_cahier_prepare_update("101", MSG, title="Sortie", pupil_ids=["1"])


@respx.mock
async def test_update_published_warns(env):
    _mock_get(_message(visible=True))
    out = await cwt.edumoov_cahier_prepare_update("101", MSG, body="Nouveau")
    assert any("déjà publié" in w for w in out["warnings"])


@respx.mock
async def test_message_of_other_classroom_refused(env):
    _mock_get(_message(scope_key="102"))
    with pytest.raises(ValueError):
        await cwt.edumoov_cahier_prepare_delete("101", MSG)


@respx.mock
async def test_publish_warns_and_sends_visible_only(env):
    _mock_get(_message())
    out = await cwt.edumoov_cahier_prepare_visibility("101", MSG, "publish")
    assert out["request"]["payload"] == {"visible": True}
    assert out["request"]["rest"] == f"PUT cartable/classroom/101/messages/{MSG}"
    assert any("IMMÉDIATE" in w for w in out["warnings"])


@respx.mock
async def test_publish_without_recipient_refused(env):
    _mock_get(_message(recipients=[]))
    with pytest.raises(ValueError):
        await cwt.edumoov_cahier_prepare_visibility("101", MSG, "publish")


@respx.mock
async def test_publish_already_published_refused(env):
    _mock_get(_message(visible=True))
    with pytest.raises(ValueError):
        await cwt.edumoov_cahier_prepare_visibility("101", MSG, "publish")


@respx.mock
async def test_delete_sends_no_body(env):
    _mock_get(_message(comments_count=2))
    dele = respx.delete(f"{REST}/cartable/classroom/101/messages/{MSG}").mock(
        return_value=httpx.Response(200, json={"success": True, "data": None})
    )
    out = await cwt.edumoov_cahier_prepare_delete("101", MSG)
    assert any("commentaire" in w for w in out["warnings"])
    await wt.edumoov_write_confirm(out["confirmation_token"])
    assert dele.called and dele.calls.last.request.content == b""


# ---------------------------------------------------------------- commentaires
THREAD = [
    {"id": 7, "user_id": 3, "user": {"firstname": "Parent", "name": "X"}, "body": "Question",
     "deleted": False, "children": [{"id": 8, "user_id": 4, "body": "R", "deleted": False, "children": []}]},
]


def _mock_thread():
    respx.get(f"{REST}/core/classroom/101/comments").mock(
        return_value=httpx.Response(200, json={"success": True, "data": THREAD})
    )


@respx.mock
async def test_comment_reply_payload(env):
    _mock_get(_message(visible=True))
    _mock_thread()
    post = respx.post(f"{REST}/core/classroom/101/comments").mock(
        return_value=httpx.Response(201, json={"success": True, "data": {"id": 9}})
    )
    out = await cwt.edumoov_comment_prepare_create("101", MSG, "Réponse", reply_to_comment_id="7")
    assert out["request"]["payload"] == {"key": MSG, "model": "message", "body": "Réponse", "reply": 7}
    assert out["preview"]["in_reply_to"]["author"] == "Parent X"
    assert any("visible des familles" in w for w in out["warnings"])
    await wt.edumoov_write_confirm(out["confirmation_token"])
    assert json.loads(post.calls.last.request.content) == out["request"]["payload"]


@respx.mock
async def test_comment_unknown_parent_refused(env):
    _mock_get(_message())
    _mock_thread()
    with pytest.raises(ValueError):
        await cwt.edumoov_comment_prepare_create("101", MSG, "x", reply_to_comment_id="42")


@respx.mock
async def test_comment_delete_nested_found(env):
    _mock_get(_message())
    _mock_thread()
    out = await cwt.edumoov_comment_prepare_delete("101", MSG, "8")
    assert out["request"]["rest"] == "DELETE core/classroom/101/comments/8/trash"
    with pytest.raises(ValueError):
        await cwt.edumoov_comment_prepare_delete("101", MSG, "42")
