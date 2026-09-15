"""Configuration du serveur MCP Edumoov — tout est piloté par variables d'environnement.

Rien de sensible n'est codé en dur ici : pas de token, pas de refresh_token, pas de
client_secret. Le seul "secret" que ce projet manipule (le refresh_token du prototype)
vit dans un fichier local hors du dépôt (voir TokenStore dans auth.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # --- Keycloak (accounts.edumoov.com), realm "edumoov" ---
    keycloak_realm_base: str = os.environ.get(
        "EDUMOOV_KEYCLOAK_REALM_BASE",
        "https://accounts.edumoov.com/auth/realms/edumoov",
    )
    # "apps" est le client_id observé pour app.edumoov.com (Direction nouvelle génération).
    # "edumoov" est utilisé par static.edumoov.com / le pont connect.php.
    # Voir cartographie-edumoov.md §5 pour le détail des deux client_id observés.
    client_id: str = os.environ.get("EDUMOOV_CLIENT_ID", "apps")

    # --- Surfaces API découvertes par capture réseau ---
    rpc_base: str = os.environ.get("EDUMOOV_RPC_BASE", "https://api.edumoov.com/rpc")
    rest_base: str = os.environ.get("EDUMOOV_REST_BASE", "https://www.edumoov.com/api/1.0")

    # --- Stockage du token du prototype, hors du dépôt git ---
    token_store_path: Path = Path(
        os.environ.get(
            "EDUMOOV_TOKEN_STORE_PATH",
            str(Path.home() / ".edumoov-mcp" / "token.json"),
        )
    )

    # --- Identifiants "pivots" pratiques pour l'usage personnel (optionnels) ---
    # Permettent de ne pas avoir à les répéter à chaque appel d'outil si l'utilisateur
    # ne travaille que sur une école/classe. Un outil peut toujours les surcharger.
    default_school_id: str | None = os.environ.get("EDUMOOV_DEFAULT_SCHOOL_ID")
    default_classroom_id: str | None = os.environ.get("EDUMOOV_DEFAULT_CLASSROOM_ID")
    default_user_id: str | None = os.environ.get("EDUMOOV_DEFAULT_USER_ID")

    # --- Redaction (voir spec-connecteur-mcp-edumoov.md §4) ---
    # Schéma réel confirmé le 15/09/2026 par un appel authentifié (voir client.py
    # list_pupils) : la première hypothèse de noms de champs était fausse (le JSON
    # utilise "birthday", pas "date_naissance"/"dob"), ce qui avait fait passer la
    # redaction inaperçue en silence côté client.py/server.py — corrigé dans le même
    # correctif. Champs volontairement PAS redactés par défaut malgré leur caractère
    # sensible : parents[].mail (emails des parents/tuteurs) — nécessaires à l'usage
    # principal envisagé (contacter une famille) ; à revoir si l'usage change.
    sensitive_pupil_fields: tuple[str, ...] = (
        "ine",
        "birthday",
    )

    @property
    def token_endpoint(self) -> str:
        return f"{self.keycloak_realm_base}/protocol/openid-connect/token"


SETTINGS = Settings()
