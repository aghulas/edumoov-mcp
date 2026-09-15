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

import json
import os
import stat
import time
from dataclasses import dataclass

import httpx

from .config import SETTINGS


class AuthError(RuntimeError):
    """Erreur d'authentification — jamais construite avec un token dans le message."""


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
        return await self._refresh(client, data)

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

        if resp.status_code != 200:
            # Ne jamais inclure le corps de la réponse (peut contenir des détails du
            # refresh_token/session) — seulement le code de statut.
            raise AuthError(
                f"Rafraîchissement du token refusé par Keycloak (HTTP {resp.status_code}). "
                "Le refresh_token est probablement expiré — relance le bootstrap avec un "
                "nouveau refresh_token capturé depuis une session navigateur active."
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


def _bootstrap_cli() -> None:
    import sys

    if len(sys.argv) != 2:
        print("Usage : python -m edumoov_mcp.auth <refresh_token>", file=sys.stderr)
        raise SystemExit(2)
    TokenStore().bootstrap_from_refresh_token(sys.argv[1])
    print(f"Token enregistré dans {SETTINGS.token_store_path} (permissions 600).")


if __name__ == "__main__":
    _bootstrap_cli()
