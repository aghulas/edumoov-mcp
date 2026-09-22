"""Tests de régression sur le dépaquetage d'enveloppe REST et le format des
appels RPC (voir client.py et cartographie-edumoov.md §6.1/§6.2/§9).

_unwrap_rest_envelope() est le point unique qui garantit qu'un appelant REST ne
reçoit jamais autre chose qu'une vraie liste — c'est le correctif direct de
l'incident documenté en §9 de la cartographie (une enveloppe brute qui passait en
silence, faisant échouer toute redaction en aval)."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from edumoov_mcp.client import EdumoovApiError, EdumoovClient, _unwrap_rest_envelope

from ._helpers import fake_auth


class TestUnwrapRestEnvelope:
    def test_list_passthrough(self):
        assert _unwrap_rest_envelope({"data": [{"id": 1}]}, context="x") == [{"id": 1}]

    def test_none_data_becomes_empty_list(self):
        assert _unwrap_rest_envelope({"data": None}, context="x") == []

    def test_missing_data_key_raises(self):
        with pytest.raises(EdumoovApiError):
            _unwrap_rest_envelope({"success": True}, context="x")

    def test_non_dict_payload_raises(self):
        with pytest.raises(EdumoovApiError):
            _unwrap_rest_envelope([{"id": 1}], context="x")
        with pytest.raises(EdumoovApiError):
            _unwrap_rest_envelope(None, context="x")


class TestRpcEnvelope:
    @respx.mock
    async def test_rpc_sends_params_payload_envelope(self, tmp_path):
        """Constat empirique du 15/09/2026 (cartographie §6.2) : sans cette
        enveloppe exacte, plusieurs endpoints RPC renvoient HTTP 412."""
        client = EdumoovClient(auth=fake_auth(tmp_path))
        route = respx.post("https://api.edumoov.com/rpc/school.schools.fetch").mock(
            return_value=httpx.Response(200, json={"success": True, "data": {"id": 90001}})
        )
        result = await client.rpc("school.schools.fetch", {"school_id": "90001"})
        assert result == {"id": 90001}
        sent_body = json.loads(route.calls.last.request.content)
        assert sent_body == {"params": {"school_id": "90001"}, "payload": {}}
        await client.aclose()

    @respx.mock
    async def test_rpc_raises_on_non_200(self, tmp_path):
        client = EdumoovClient(auth=fake_auth(tmp_path))
        respx.post("https://api.edumoov.com/rpc/whatever").mock(return_value=httpx.Response(412))
        with pytest.raises(EdumoovApiError):
            await client.rpc("whatever")
        await client.aclose()

    @respx.mock
    async def test_rpc_raises_on_success_false(self, tmp_path):
        client = EdumoovClient(auth=fake_auth(tmp_path))
        respx.post("https://api.edumoov.com/rpc/whatever").mock(
            return_value=httpx.Response(200, json={"success": False})
        )
        with pytest.raises(EdumoovApiError):
            await client.rpc("whatever")
        await client.aclose()
