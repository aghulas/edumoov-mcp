"""Gestion du token Keycloak pour le prototype.

Contexte (voir cartographie-edumoov.md §5 et spec-connecteur-mcp-edumoov.md §2) :
Edumoov utilise Keycloak (OAuth2/OIDC, Authorization Code + PKCE) sans documentation
publique. Pour un usage personnel de prototype, ce module part d'un refresh_token
récupéré manuellement depuis une session navigateur authentifiée, et se contente de le
faire vivre (rafraîchissement automatique) — il n'implémente PAS le flow PKCE
interactif complet, dont on ne sait pas encore s'il fonctionne avec un redirect_uri
non pré-enregistré côté Keycloak (à tester séparément, voir README).

Ce module ne journalise jamais de token, ni en clair ni tronqué.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import time
from dataclasses import dataclass

import httpx

from .config import SETTINGS


class AuthError(RuntimeError):
    """Erreur d'authentification — jamais construite avec un token dans le message."""


class RefreshTokenExpiredError(AuthError):
    """Cas précis où Keycloak a refusé le refresh_token lui-même (HTTP 400/401).

    Distinct des autres AuthError (échec réseau, 5xx, etc.) car c'est le seul cas où
    déclencher automatiquement une réauthentification navigateur (voir
    `KeycloakAuth._auto_browser_reauth`) a une chance de résoudre le problème — pas
    la peine d'ouvrir une fenêtre Chrome pour un souci réseau transitoire.
    """


# Sérialise les tentatives de réauth automatique : si deux appels d'outils MCP
# échouent en même temps sur un refresh_token expiré, on ne veut ouvrir qu'une seule
# fenêtre Chrome, pas une par appel concurrent.
_BROWSER_REAUTH_LOCK = asyncio.Lock()


@dataclass
class _TokenData:
    access_token: str
    refresh_token: str
    expires_at: float  # epoch seconds


class TokenStore:
    """Lit/écrit le token sur disque, hors du dépôt git, avec des permissions restrictives."""

    def __init__(self, path=None):
        self.path = path or SETTINGS.token_store_path

    def load(self) -> _TokenData | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text())
            return _TokenData(**raw)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            raise AuthError(
                f"Fichier de token illisible ou corrompu ({self.path}). "
                "Relance le bootstrap (voir README)."
            ) from exc

    def save(self, data: _TokenData) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(vars(data)))
        # Le fichier contient un refresh_token : lecture/écriture réservées au propriétaire.
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

    def bootstrap_from_refresh_token(self, refresh_token: str) -> None:
        """Amorce le store avec un refresh_token récupéré manuellement (usage prototype).

        Ne fait AUCUN appel réseau : le premier appel à get_access_token() se chargera
        de l'échanger contre un access_token via Keycloak.
        """
        self.save(_TokenData(access_token="", refresh_token=refresh_token, expires_at=0))


class KeycloakAuth:
    """Fournit un access_token valide, en le rafraîchissant via Keycloak si besoin."""

    # Marge de sécurité avant expiration pour déclencher un refresh préventif.
    _REFRESH_MARGIN_SECONDS = 60

    def __init__(self, store: TokenStore | None = None):
        self.store = store or TokenStore()

    async def get_access_token(self, client: httpx.AsyncClient) -> str:
        data = self.store.load()
        if data is None:
            raise AuthError(
                "Aucun token enregistré. Lance d'abord le bootstrap : "
                "`python -m edumoov_mcp.auth <refresh_token>` (voir README)."
            )
        if data.access_token and data.expires_at - self._REFRESH_MARGIN_SECONDS > time.time():
            return data.access_token
        try:
            return await self._refresh(client, data)
        except RefreshTokenExpiredError:
            if not SETTINGS.auto_browser_reauth:
                raise
            await self._auto_browser_reauth()
            data = self.store.load()
            if data is None or not data.access_token:
                # La réauth automatique a réussi côté sous-processus mais n'a, contre toute
                # attente, rien laissé d'exploitable sur disque — on ne boucle pas indéfiniment.
                raise
            return data.access_token

    async def _refresh(self, client: httpx.AsyncClient, data: _TokenData) -> str:
        try:
            resp = await client.post(
                SETTINGS.token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "client_id": SETTINGS.client_id,
                    "refresh_token": data.refresh_token,
                },
            )
        except httpx.HTTPError as exc:
            raise AuthError(f"Échec réseau lors du rafraîchissement du token : {exc}") from exc

        if resp.status_code in (400, 401):
            # Cas typique d'un refresh_token expiré/révoqué (invalid_grant côté Keycloak).
            # Ne jamais inclure le corps de la réponse (peut contenir des détails du
            # refresh_token/session) — seulement le code de statut.
            raise RefreshTokenExpiredError(
                f"Rafraîchissement du token refusé par Keycloak (HTTP {resp.status_code}). "
                "Le refresh_token est probablement expiré."
            )
        if resp.status_code != 200:
            raise AuthError(
                f"Rafraîchissement du token refusé par Keycloak (HTTP {resp.status_code})."
            )

        payload = resp.json()
        new_data = _TokenData(
            access_token=payload["access_token"],
            # Keycloak peut faire tourner (rotate) le refresh_token : on garde le nouveau
            # s'il est fourni, sinon on conserve l'actuel.
            refresh_token=payload.get("refresh_token", data.refresh_token),
            expires_at=time.time() + float(payload.get("expires_in", 60)),
        )
        self.store.save(new_data)
        return new_data.access_token

    async def _auto_browser_reauth(self) -> None:
        """Déclenche `python -m edumoov_mcp.browser_auth refresh` en sous-processus.

        Contexte (cartographie-edumoov.md §6.8/§9, incident du 17/09/2026) : jusqu'ici,
        un refresh_token expiré faisait simplement échouer l'appel MCP en cours — sans
        planification (`launchd`, explicitement écartée le 15/09/2026 puis reposée à
        [prénom] le 17/09/2026), ce token pouvait rester périmé jusqu'au prochain usage.
        Plutôt que planifier un rafraîchissement périodique, ce correctif rend le
        rafraîchissement réactif : dès qu'un appel constate que le refresh_token ne
        fonctionne plus, il déclenche lui-même une réauth navigateur puis retente.

        Lancé en SOUS-PROCESSUS (pas en import direct + thread) pour une raison précise :
        ce serveur MCP communique avec Claude Desktop via JSON-RPC sur stdin/stdout. Le
        module browser_auth (Playwright) tourne dans son propre interpréteur Python, avec
        son propre stdout — aucun risque qu'un print ou une sortie Playwright ne vienne
        corrompre le flux JSON-RPC du serveur, contrairement à un appel in-process.

        Ouvre une fenêtre Chrome visible sur la machine où tourne le serveur MCP (voir
        browser_auth.py : le mode headless ne reconnaît pas la session SSO active). Si la
        session SSO du profil dédié est elle-même expirée, le sous-processus échoue et
        cette méthode lève une AuthError invitant à relancer `browser_auth login` à la main
        — l'automatisation ne remplace pas ce cas de secours, déjà documenté en §6.8.
        """
        async with _BROWSER_REAUTH_LOCK:
            # Un appel concurrent a peut-être déjà rafraîchi le token pendant qu'on
            # attendait le verrou — pas la peine de rouvrir une deuxième fenêtre Chrome.
            data = self.store.load()
            if data and data.access_token and data.expires_at - self._REFRESH_MARGIN_SECONDS > time.time():
                return

            print(
                "[edumoov-mcp] refresh_token expiré — déclenchement automatique de "
                "`edumoov_mcp.browser_auth refresh` (fenêtre Chrome dédiée, ~20-120s, "
                "normalement sans action requise si la session SSO est encore active)...",
                file=sys.stderr,
            )
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "edumoov_mcp.browser_auth",
                    "refresh",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=SETTINGS.browser_reauth_timeout_seconds,
                    )
                except asyncio.TimeoutError as exc:
                    proc.kill()
                    await proc.wait()
                    raise AuthError(
                        "Réauthentification automatique via navigateur : délai dépassé "
                        f"({SETTINGS.browser_reauth_timeout_seconds}s). Relance "
                        "manuellement `python -m edumoov_mcp.browser_auth login`."
                    ) from exc
            except OSError as exc:
                raise AuthError(
                    f"Impossible de lancer la réauthentification automatique : {exc}"
                ) from exc

            if proc.returncode != 0:
                # browser_auth.py ne journalise jamais de token — sûr de relayer sa
                # dernière ligne de stderr pour donner un indice concret (ex. session SSO
                # expirée) sans risquer d'exposer un secret.
                last_line = ""
                if stderr_bytes:
                    lines = stderr_bytes.decode(errors="replace").strip().splitlines()
                    last_line = lines[-1] if lines else ""
                raise AuthError(
                    "Réauthentification automatique via navigateur a échoué "
                    f"(code {proc.returncode}). {last_line} "
                    "Si la session SSO du profil dédié a expiré, relance manuellement "
                    "`python -m edumoov_mcp.browser_auth login`."
                )


def _bootstrap_cli() -> None:
    import sys

    if len(sys.argv) != 2:
        print("Usage : python -m edumoov_mcp.auth <refresh_token>", file=sys.stderr)
        raise SystemExit(2)
    TokenStore().bootstrap_from_refresh_token(sys.argv[1])
    print(f"Token enregistré dans {SETTINGS.token_store_path} (permissions 600).")


if __name__ == "__main__":
    _bootstrap_cli()
