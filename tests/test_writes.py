"""Garde-fous des écritures (30/09/2026) — voir writes.py, write_tools.py et
spec-connecteur-mcp-edumoov.md §5. Enjeu : qu'aucune écriture ne parte sans
aperçu + confirmation, que le verrou global EDUMOOV_ENABLE_WRITES soit respecté,
et que l'appel confirmé soit exactement celui prévisualisé."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

import edumoov_mcp.server as server_module
import edumoov_mcp.write_tools as wt
from edumoov_mcp.client import EdumoovClient, ForbiddenEndpointError
from edumoov_mcp.writes import (
    ConfirmationError,
    WriteGate,
    WritesDisabledError,
    diff_leaves,
)

from ._helpers import fake_auth

RPC = "https://api.edumoov.com/rpc"
CLASSROOMS = [
    {"id": 101, "name": "CP A", "school_id": 90001, "release": False, "deleted": False},
    {"id": 102, "name": "CE1", "school_id": 90001, "release": False, "deleted": False},
    {"id": 103, "name": "Ancienne", "school_id": 90001, "release": True, "deleted": False},
    {"id": 201, "name": "Autre école", "school_id": 90009, "release": False, "deleted": False},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    client = EdumoovClient(auth=fake_auth(tmp_path))
    monkeypatch.setattr(server_module, "_client", client)
    gate = WriteGate(enabled=True, ttl_seconds=600)
    monkeypatch.setattr(wt, "_gate", gate)
    yield client, gate


def _mock_classrooms():
    respx.post(f"{RPC}/user.classrooms.fetch").mock(
        return_value=httpx.Response(200, json={"success": True, "data": CLASSROOMS})
    )


# ---------------------------------------------------------------- WriteGate
class TestWriteGate:
    def test_disabled_by_default_refuses_prepare(self):
        gate = WriteGate(enabled=False)
        with pytest.raises(WritesDisabledError):
            gate.prepare("school.messages.create", {}, {}, summary="x", preview={})

    def test_disabled_refuses_confirm(self):
        gate = WriteGate(enabled=False)
        with pytest.raises(WritesDisabledError):
            gate.pop("nimporte")

    def test_unknown_method_refused(self):
        gate = WriteGate(enabled=True)
        with pytest.raises(ValueError):
            gate.prepare("core.users.delete", {}, {}, summary="x", preview={})

    def test_token_single_use(self):
        gate = WriteGate(enabled=True)
        out = gate.prepare("school.messages.delete", {"id": "a"}, {}, summary="x", preview={})
        pending = gate.pop(out["confirmation_token"])
        assert pending.method == "school.messages.delete"
        with pytest.raises(ConfirmationError):
            gate.pop(out["confirmation_token"])

    def test_expired_token_refused(self):
        gate = WriteGate(enabled=True, ttl_seconds=-1)
        out = gate.prepare("school.messages.delete", {"id": "a"}, {}, summary="x", preview={})
        with pytest.raises(ConfirmationError):
            gate.pop(out["confirmation_token"])

    def test_cancel(self):
        gate = WriteGate(enabled=True)
        out = gate.prepare("school.messages.delete", {"id": "a"}, {}, summary="x", preview={})
        assert gate.cancel(out["confirmation_token"]) is True
        with pytest.raises(ConfirmationError):
            gate.pop(out["confirmation_token"])


def test_diff_leaves_nested():
    before = {"ui": {"precision": 5, "other": 1}, "features": {"A": True}}
    changes = {"ui": {"precision": 10}, "features": {"A": True, "B": False}}
    assert diff_leaves(before, changes) == [
        {"path": "ui.precision", "before": 5, "after": 10},
        {"path": "features.B", "before": None, "after": False},
    ]


def test_text_to_html_escapes():
    assert wt.text_to_html("Bonjour <b>\nligne 2\n\nParag 2") == (
        "<p>Bonjour &lt;b&gt;<br>ligne 2</p><p>Parag 2</p>"
    )


# ---------------------------------------------------------------- client.rpc_write
async def test_rpc_write_refuses_non_whitelisted_before_network(tmp_path):
    client = EdumoovClient(auth=fake_auth(tmp_path))
    with pytest.raises(ForbiddenEndpointError):
        await client.rpc_write("core.users.delete", {}, {})  # aucun mock réseau actif
    await client.aclose()


# ---------------------------------------------------------------- annonces
@respx.mock
async def test_advert_prepare_create_draft_then_confirm(env):
    _mock_classrooms()
    create = respx.post(f"{RPC}/school.messages.create").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "data": {"id": "uuid-1", "title": "Titre", "visibility": None}},
        )
    )
    out = await wt.edumoov_advert_prepare_create(title="Titre", body="Texte", school_id="90001")
    # préparation : aucun appel d'écriture
    assert not create.called
    assert out["preview"]["status"] == "brouillon"
    assert out["preview"]["recipients"] == ["CP A", "CE1"]  # classes actives de l'école seulement
    assert out["rpc"]["payload"]["visibility"] is None
    res = await wt.edumoov_write_confirm(out["confirmation_token"])
    assert create.called
    sent = json.loads(create.calls.last.request.content)
    assert sent["params"] == {"school_id": 90001, "graph": ["recipients"]}
    assert sent["payload"] == out["rpc"]["payload"]
    assert sent["payload"]["recipients"] == [
        {"model": "Classroom", "key": 101},
        {"model": "Classroom", "key": 102},
    ]
    assert res["advert"]["status"] == "brouillon"


@respx.mock
async def test_advert_publish_now_warns(env):
    _mock_classrooms()
    out = await wt.edumoov_advert_prepare_create(
        title="T", body="B", school_id="90001", classroom_ids=["101"], publication="now"
    )
    assert out["preview"]["status"] == "publiée"
    assert any("IMMÉDIATE" in w for w in out["warnings"])
    assert out["rpc"]["payload"]["visibility"].endswith("Z")


@respx.mock
async def test_advert_publish_without_recipient_refused(env):
    _mock_classrooms()
    with pytest.raises(ValueError):
        await wt.edumoov_advert_prepare_create(
            title="T", body="B", school_id="90001", classroom_ids=[], publication="now"
        )


@respx.mock
async def test_advert_unknown_classroom_refused(env):
    _mock_classrooms()
    with pytest.raises(ValueError):
        await wt.edumoov_advert_prepare_create(
            title="T", body="B", school_id="90001", classroom_ids=["201"]
        )


@respx.mock
async def test_writes_disabled_blocks_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "_client", EdumoovClient(auth=fake_auth(tmp_path)))
    monkeypatch.setattr(wt, "_gate", WriteGate(enabled=False))
    _mock_classrooms()
    with pytest.raises(WritesDisabledError):
        await wt.edumoov_advert_prepare_create(title="T", body="B", school_id="90001")


# ---------------------------------------------------------------- réglages
@respx.mock
async def test_settings_prepare_diff_and_confirm(env):
    respx.post(f"{RPC}/user.settings.get").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "data": {"_stack": [], "ui": {"precision": 5}}},
        )
    )
    setr = respx.post(f"{RPC}/user.settings.set").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {}})
    )
    out = await wt.edumoov_settings_prepare_set("user", "90003", "Journal", {"ui": {"precision": 10}})
    assert out["preview"]["changes"] == [{"path": "ui.precision", "before": 5, "after": 10}]
    await wt.edumoov_write_confirm(out["confirmation_token"])
    sent = json.loads(setr.calls.last.request.content)
    assert sent == {
        "params": {"user_id": 90003, "app": "Journal", "context": None},
        "payload": {"ui": {"precision": 10}},
    }


@respx.mock
async def test_settings_noop_refused(env):
    respx.post(f"{RPC}/user.settings.get").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"ui": {"precision": 5}}})
    )
    with pytest.raises(ValueError):
        await wt.edumoov_settings_prepare_set("user", "90003", "Journal", {"ui": {"precision": 5}})


@respx.mock
async def test_settings_signature_refused(env):
    respx.post(f"{RPC}/classroom.settings.get").mock(
        return_value=httpx.Response(
            200, json={"success": True, "data": {"signatures": {"signatureFile": "x"}}}
        )
    )
    with pytest.raises(ValueError):
        await wt.edumoov_settings_prepare_set(
            "classroom", "101", "Livret", {"signatures": {"signatureFile": "y"}}
        )


async def test_settings_unknown_scope_refused(env):
    with pytest.raises(ValueError):
        await wt.edumoov_settings_prepare_set("structure", "1", "Livret", {"a": 1})


# ---------------------------------------------------------------- classes
async def test_classroom_update_field_allowlist(env):
    with pytest.raises(ValueError):
        await wt.edumoov_classroom_prepare_update("101", {"school_id": 1}, school_id="90001")


@respx.mock
async def test_classroom_update_confirm_payload(env):
    _mock_classrooms()
    upd = respx.post(f"{RPC}/school.classrooms.update").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"id": 101}})
    )
    out = await wt.edumoov_classroom_prepare_update("101", {"name": "CP B"}, school_id="90001")
    assert out["preview"]["changes"] == [{"field": "name", "before": "CP A", "after": "CP B"}]
    await wt.edumoov_write_confirm(out["confirmation_token"])
    sent = json.loads(upd.calls.last.request.content)
    assert sent == {"params": {"school_id": 90001, "id": 101}, "payload": {"id": 101, "name": "CP B"}}
