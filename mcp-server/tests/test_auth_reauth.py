"""Tests de la réauthentification automatique via navigateur (auth.py).

Contexte : voir cartographie-edumoov.md §6.8/§9 (incident du 17/09/2026 — un
refresh_token périmé faisait échouer le MCP sans qu'aucun mécanisme ne le corrige
automatiquement). Ces tests couvrent le nouveau comportement réactif de
`KeycloakAuth.get_access_token` : sur un refresh_token expiré (HTTP 400/401), il
déclenche `python -m edumoov_mcp.browser_auth refresh` en sous-processus puis
retente — sans jamais lancer un vrai navigateur pendant les tests (tout est simulé).
"""
from __future__ import annotations

import asyncio
import dataclasses
import time

import httpx
import pytest
import respx

import edumoov_mcp.auth as auth_module
from edumoov_mcp.auth import AuthError, KeycloakAuth, RefreshTokenExpiredError
from edumoov_mcp.config import SETTINGS

from ._helpers import fake_auth


def _set_auto_browser_reauth(monkeypatch, value: bool) -> None:
    """Settings est un dataclass frozen (immutable par conception, voir config.py) :
    on ne peut pas monkeypatcher un de ses champs in place. On patche à la place le
    nom `SETTINGS` importé dans le module auth (celui que le code sous test lit
    réellement) par une copie modifiée."""
    monkeypatch.setattr(
        auth_module, "SETTINGS", dataclasses.replace(SETTINGS, auto_browser_reauth=value)
    )


def _expired_auth(tmp_path) -> KeycloakAuth:
    """Auth factice avec un access_token déjà expiré, pour forcer un _refresh()."""
    auth = fake_auth(tmp_path)
    data = auth.store.load()
    data.expires_at = time.time() - 10
    auth.store.save(data)
    return auth


class TestRefreshStatusClassification:
    @respx.mock
    @pytest.mark.parametrize("status", [400, 401])
    async def test_400_401_raise_refresh_token_expired(self, tmp_path, status):
        auth = _expired_auth(tmp_path)
        respx.post(SETTINGS.token_endpoint).mock(return_value=httpx.Response(status))
        async with httpx.AsyncClient() as client:
            with pytest.raises(RefreshTokenExpiredError):
                await auth._refresh(client, auth.store.load())

    @respx.mock
    async def test_other_status_raises_plain_autherror_not_expired(self, tmp_path):
        """Un 500 (souci réseau/serveur transitoire) ne doit pas être confondu avec
        un refresh_token expiré — pas la peine d'ouvrir un navigateur pour ça."""
        auth = _expired_auth(tmp_path)
        respx.post(SETTINGS.token_endpoint).mock(return_value=httpx.Response(500))
        async with httpx.AsyncClient() as client:
            with pytest.raises(AuthError) as exc_info:
                await auth._refresh(client, auth.store.load())
        assert not isinstance(exc_info.value, RefreshTokenExpiredError)


class TestAutoBrowserReauthGating:
    @respx.mock
    async def test_disabled_propagates_without_attempting_reauth(self, tmp_path, monkeypatch):
        _set_auto_browser_reauth(monkeypatch, False)
        auth = _expired_auth(tmp_path)
        respx.post(SETTINGS.token_endpoint).mock(return_value=httpx.Response(400))

        async def _should_not_be_called():
            raise AssertionError("_auto_browser_reauth ne doit pas être appelée si désactivée")

        monkeypatch.setattr(auth, "_auto_browser_reauth", _should_not_be_called)
        async with httpx.AsyncClient() as client:
            with pytest.raises(RefreshTokenExpiredError):
                await auth.get_access_token(client)

    @respx.mock
    async def test_enabled_calls_reauth_and_returns_fresh_token(self, tmp_path, monkeypatch):
        _set_auto_browser_reauth(monkeypatch, True)
        auth = _expired_auth(tmp_path)
        respx.post(SETTINGS.token_endpoint).mock(return_value=httpx.Response(400))

        called = {"n": 0}

        async def _fake_reauth():
            called["n"] += 1
            # Simule ce que fait réellement browser_auth._run() : écrire un token frais.
            data = auth.store.load()
            data.access_token = "fresh-access-token"
            data.refresh_token = "fresh-refresh-token"
            data.expires_at = time.time() + 3600
            auth.store.save(data)

        monkeypatch.setattr(auth, "_auto_browser_reauth", _fake_reauth)
        async with httpx.AsyncClient() as client:
            token = await auth.get_access_token(client)

        assert token == "fresh-access-token"
        assert called["n"] == 1

    @respx.mock
    async def test_reauth_failure_propagates_as_autherror(self, tmp_path, monkeypatch):
        _set_auto_browser_reauth(monkeypatch, True)
        auth = _expired_auth(tmp_path)
        respx.post(SETTINGS.token_endpoint).mock(return_value=httpx.Response(400))

        async def _fake_reauth_fails():
            raise AuthError("session SSO expirée, relance `browser_auth login`")

        monkeypatch.setattr(auth, "_auto_browser_reauth", _fake_reauth_fails)
        async with httpx.AsyncClient() as client:
            with pytest.raises(AuthError):
                await auth.get_access_token(client)


class _FakeProc:
    def __init__(self, returncode: int, stderr: bytes = b""):
        self.returncode = returncode
        self._stderr = stderr
        self.killed = False

    async def communicate(self):
        return b"", self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


class TestAutoBrowserReauthSubprocess:
    """Vérifie la mécanique du sous-processus elle-même (sans jamais lancer de
    vrai Playwright/Chrome) : succès, échec avec code non nul, et verrouillage."""

    async def test_success_returns_without_raising(self, tmp_path, monkeypatch):
        auth = _expired_auth(tmp_path)
        # Token déjà expiré au départ ; le "sous-processus" simulé ne le rafraîchit
        # pas lui-même ici (ce n'est pas son rôle testé) — on vérifie juste l'absence
        # d'exception quand le sous-processus réussit (code 0).
        async def fake_create_subprocess_exec(*args, **kwargs):
            return _FakeProc(returncode=0)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        await auth._auto_browser_reauth()  # ne doit pas lever

    async def test_nonzero_exit_raises_autherror_with_stderr_hint(self, tmp_path, monkeypatch):
        auth = _expired_auth(tmp_path)

        async def fake_create_subprocess_exec(*args, **kwargs):
            return _FakeProc(returncode=1, stderr=b"BrowserAuthError: session SSO expiree\n")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        with pytest.raises(AuthError) as exc_info:
            await auth._auto_browser_reauth()
        assert "session SSO expiree" in str(exc_info.value)

    async def test_already_fresh_token_skips_subprocess(self, tmp_path, monkeypatch):
        """Si un appel concurrent a déjà rafraîchi le token avant l'acquisition du
        verrou, on ne doit pas relancer un sous-processus/navigateur pour rien."""
        auth = fake_auth(tmp_path)  # token déjà valide 1h

        async def _should_not_be_called(*args, **kwargs):
            raise AssertionError("create_subprocess_exec ne doit pas être appelé")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _should_not_be_called)
        await auth._auto_browser_reauth()  # ne doit rien lancer, retour immédiat
