"""Tests sur get_media_url() — endpoint core.medias.file, au comportement de
sécurité atypique et volontairement pas masqué par ce connecteur (voir
cartographie-edumoov.md §6.5 et client.py:get_media_url)."""
from __future__ import annotations

import httpx
import pytest
import respx

from edumoov_mcp.client import EdumoovApiError, EdumoovClient

from ._helpers import fake_auth

_URL = "https://api.edumoov.com/rpc/core.medias.file"


class TestGetMediaUrl:
    @respx.mock
    async def test_returns_redirect_location(self, tmp_path):
        client = EdumoovClient(auth=fake_auth(tmp_path))
        respx.get(_URL).mock(
            return_value=httpx.Response(302, headers={"location": "https://storage.example/signed"})
        )
        url = await client.get_media_url("9249729")
        assert url == "https://storage.example/signed"
        await client.aclose()

    @respx.mock
    async def test_never_sends_authorization_header(self, tmp_path):
        """Régression : un Bearer seul sur CET endpoint précis renvoie HTTP 403
        (constat empirique du 15/09/2026) — le connecteur ne doit jamais
        l'envoyer ici, contrairement à tous les autres appels RPC/REST."""
        client = EdumoovClient(auth=fake_auth(tmp_path))
        route = respx.get(_URL).mock(
            return_value=httpx.Response(302, headers={"location": "https://storage.example/signed"})
        )
        await client.get_media_url("9249729")
        sent_headers = {k.lower() for k in route.calls.last.request.headers.keys()}
        assert "authorization" not in sent_headers
        await client.aclose()

    @respx.mock
    async def test_token_param_passed_when_given(self, tmp_path):
        client = EdumoovClient(auth=fake_auth(tmp_path))
        route = respx.get(_URL).mock(
            return_value=httpx.Response(302, headers={"location": "https://storage.example/signed"})
        )
        await client.get_media_url("9249729", token="abc")
        assert route.calls.last.request.url.params["token"] == "abc"
        await client.aclose()

    @respx.mock
    async def test_non_redirect_response_raises(self, tmp_path):
        client = EdumoovClient(auth=fake_auth(tmp_path))
        respx.get(_URL).mock(return_value=httpx.Response(404))
        with pytest.raises(EdumoovApiError):
            await client.get_media_url("nope")
        await client.aclose()
