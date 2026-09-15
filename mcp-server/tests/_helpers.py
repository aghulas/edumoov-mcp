"""Utilitaires partagés entre les modules de test (pas de découverte pytest ici :
ce fichier ne commence pas par test_)."""
from __future__ import annotations

import json
import time

from edumoov_mcp.auth import KeycloakAuth, TokenStore


def fake_auth(tmp_path) -> KeycloakAuth:
    """Auth factice pour les tests : un access_token valide 1h, préchargé sur
    disque, pour qu'aucun test n'ait besoin d'un vrai appel réseau à Keycloak."""
    store = TokenStore(path=tmp_path / "token.json")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(
            {
                "access_token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_at": time.time() + 3600,
            }
        )
    )
    return KeycloakAuth(store=store)
