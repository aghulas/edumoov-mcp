"""Garde-fou des écritures Edumoov : prévisualiser, puis confirmer.

Principe (voir spec-connecteur-mcp-edumoov.md §5) : aucun outil MCP n'écrit
directement dans Edumoov. Chaque outil « prepare » construit l'appel RPC exact
qui sera envoyé, calcule un aperçu lisible (avant → après, destinataires...) et
le met en attente derrière un jeton de confirmation à usage unique. Seul
`edumoov_write_confirm(jeton)` exécute l'appel — exactement celui qui a été
prévisualisé, sans possibilité de le modifier entre les deux.

Trois verrous cumulés :
1. `EDUMOOV_ENABLE_WRITES` doit valoir 1 (désactivé par défaut) — sinon les
   outils d'écriture refusent, y compris la préparation.
2. Le jeton expire (défaut 10 min) et ne sert qu'une fois.
3. Liste blanche des méthodes RPC d'écriture autorisées (`ALLOWED_WRITE_METHODS`) :
   même si un bug construisait un autre appel, il serait refusé ici.

Journalisation : uniquement la méthode RPC, le jeton tronqué et l'issue, sur
stderr (stdout est le canal JSON-RPC du serveur en mode stdio) — jamais le
contenu d'une annonce ni une valeur de paramètre.
"""
from __future__ import annotations

import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from .config import SETTINGS

# Méthodes RPC d'écriture connues et autorisées. Toutes identifiées par lecture du
# bundle front app.edumoov.com (30/09/2026), puis validées en conditions réelles
# sauf mention contraire — voir cartographie-edumoov.md §6.11.
ALLOWED_WRITE_METHODS: frozenset[str] = frozenset(
    {
        "school.messages.create",  # annonce d'école (brouillon ou publiée) — validé
        "school.messages.update",  # modification / publication / dépublication — validé
        "school.messages.delete",  # suppression — validé
        "user.settings.set",  # préférences utilisateur — validé (aller-retour)
        "classroom.settings.set",  # réglages de classe (Livret, Educartable...) — validé (no-op)
        "school.settings.set",  # réglages d'école — non validé en réel
        "school.classrooms.update",  # fiche classe — validé (no-op)
        "classroom.classrooms.link",  # affecter un enseignant à une classe — NON validé en réel
        "classroom.classrooms.unlink",  # retirer un enseignant d'une classe — NON validé en réel
        # Appel (04/10/2026, bundle front, entité `appeals` / `pupilsappeals`) :
        "classroom.pupilsappeals.batchUpsert",  # enregistrer / modifier l'appel d'une demi-journée
        "classroom.pupilsappeals.resetDay",  # supprimer l'appel d'une demi-journée
        # Pièce jointe d'une annonce (06/10/2026, bundle front : composant
        # EAdvertView + useMedias) : URL signée d'envoi, puis POST multipart du
        # fichier sur filerz.edumoov.com avec un lien primaire {model:"Message",
        # key:<annonce>}. Transport "upload" uniquement (voir client.upload_media).
        "school.medias.url",
    }
)

# Méthodes utilisables avec le transport "upload" (signature + envoi de fichier).
ALLOWED_UPLOAD_METHODS: frozenset[str] = frozenset(
    {
        "school.medias.url",
        # Pièce jointe d'un message du cahier de liaison (06/10/2026, bundle
        # cartable : composant add-files) : GET REST legacy
        # core/classroom/{id}/medias/url, puis POST multipart sur filerz avec
        # model="HomeworkMessage", key=<message>, sig, payload, file.
        "core.classroom.medias.url",
    }
)


# Écritures REST legacy (www.edumoov.com/api/1.0) autorisées — Cartable : cahier de
# liaison d'une classe et commentaires (01/10/2026, lus dans le bundle
# static.edumoov.com/cartable puis validés en réel sur brouillon sans destinataire).
ALLOWED_REST_WRITES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^cartable/classroom/\d+/messages$")),
    ("PUT", re.compile(r"^cartable/classroom/\d+/messages/[0-9a-f-]{36}$")),
    ("DELETE", re.compile(r"^cartable/classroom/\d+/messages/[0-9a-f-]{36}$")),
    ("POST", re.compile(r"^core/classroom/\d+/comments$")),
    ("DELETE", re.compile(r"^core/classroom/\d+/comments/\d+/trash$")),
)


def rest_write_allowed(http_method: str, path: str) -> bool:
    return any(m == http_method and rx.match(path) for m, rx in ALLOWED_REST_WRITES)


class WritesDisabledError(RuntimeError):
    """Écriture demandée alors que EDUMOOV_ENABLE_WRITES n'est pas activé."""


class ConfirmationError(RuntimeError):
    """Jeton inconnu, expiré ou déjà utilisé."""


@dataclass
class PendingWrite:
    method: str
    params: dict[str, Any]
    payload: dict[str, Any]
    summary: str
    preview: dict[str, Any]
    expires_at: float
    warnings: list[str] = field(default_factory=list)
    # "rpc" (api.edumoov.com/rpc/<method>) ou "rest" (www.edumoov.com/api/1.0/<method>
    # = chemin, avec http_method)
    transport: str = "rpc"
    http_method: str | None = None
    # transport "upload" : fichier local envoyé à la confirmation
    file_path: str | None = None


def _log(message: str) -> None:
    print(f"[edumoov-writes] {message}", file=sys.stderr, flush=True)


class WriteGate:
    def __init__(self, *, enabled: bool | None = None, ttl_seconds: int | None = None):
        self._enabled = SETTINGS.enable_writes if enabled is None else enabled
        self._ttl = SETTINGS.write_confirmation_ttl_seconds if ttl_seconds is None else ttl_seconds
        self._pending: dict[str, PendingWrite] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def ensure_enabled(self) -> None:
        if not self._enabled:
            raise WritesDisabledError(
                "Les écritures Edumoov sont désactivées sur ce serveur. Pour les activer, "
                "définir EDUMOOV_ENABLE_WRITES=1 dans la configuration du serveur MCP "
                "puis le redémarrer."
            )

    def _purge(self) -> None:
        now = time.time()
        for token in [t for t, p in self._pending.items() if p.expires_at < now]:
            del self._pending[token]

    def prepare(
        self,
        method: str,
        params: dict[str, Any],
        payload: dict[str, Any],
        *,
        summary: str,
        preview: dict[str, Any],
        warnings: list[str] | None = None,
        transport: str = "rpc",
        http_method: str | None = None,
        file_path: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_enabled()
        if transport == "rpc":
            if method not in ALLOWED_WRITE_METHODS:
                raise ValueError(f"Méthode d'écriture non autorisée : {method!r}")
        elif transport == "rest":
            if not http_method or not rest_write_allowed(http_method, method):
                raise ValueError(f"Écriture REST non autorisée : {http_method} {method!r}")
        elif transport == "upload":
            if method not in ALLOWED_UPLOAD_METHODS or not file_path:
                raise ValueError(f"Envoi de fichier non autorisé : {method!r}")
        else:
            raise ValueError(f"Transport inconnu : {transport!r}")
        self._purge()
        token = secrets.token_urlsafe(16)
        pending = PendingWrite(
            method=method,
            params=params,
            payload=payload,
            summary=summary,
            preview=preview,
            expires_at=time.time() + self._ttl,
            warnings=list(warnings or []),
            transport=transport,
            http_method=http_method,
            file_path=file_path,
        )
        self._pending[token] = pending
        label = f"{http_method} {method}" if transport == "rest" else method
        _log(f"préparé {label} jeton={token[:6]}…")
        return {
            "status": "EN ATTENTE DE CONFIRMATION — rien n'a encore été modifié dans Edumoov",
            "summary": summary,
            "warnings": pending.warnings,
            "preview": preview,
            "request": (
                {"rest": f"{http_method} {method}", "params": params, "payload": payload}
                if transport == "rest"
                else {"rpc": method, "params": params, "payload": payload}
            ),
            "confirmation_token": token,
            "expires_in_seconds": self._ttl,
            "next_step": (
                "Montrer cet aperçu à l'utilisateur et n'appeler edumoov_write_confirm "
                "avec ce jeton qu'après son accord explicite. Sinon, edumoov_write_cancel."
            ),
        }

    def pop(self, token: str) -> PendingWrite:
        self.ensure_enabled()
        self._purge()
        pending = self._pending.pop(token, None)
        if pending is None:
            raise ConfirmationError(
                "Jeton de confirmation inconnu, expiré ou déjà utilisé — refaire la "
                "préparation (outil *_prepare_*) pour obtenir un nouvel aperçu."
            )
        return pending

    def cancel(self, token: str) -> bool:
        return self._pending.pop(token, None) is not None

    def list_pending(self) -> list[dict[str, Any]]:
        self._purge()
        now = time.time()
        return [
            {
                "confirmation_token": t,
                "method": p.method,
                "summary": p.summary,
                "expires_in_seconds": int(p.expires_at - now),
            }
            for t, p in self._pending.items()
        ]


def diff_leaves(before: Any, after: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Liste des feuilles modifiées entre `before` et le patch `after` (fusion
    récursive façon settings.set) — pour l'aperçu avant → après."""
    changes: list[dict[str, Any]] = []
    if isinstance(after, dict) and isinstance(before, dict):
        for key, value in after.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            changes.extend(diff_leaves(before.get(key), value, path))
        return changes
    if isinstance(after, dict) and before is None:
        for key, value in after.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            changes.extend(diff_leaves(None, value, path))
        return changes
    if before != after:
        changes.append({"path": prefix, "before": before, "after": after})
    return changes


def log_outcome(method: str, ok: bool) -> None:
    _log(f"exécuté {method} → {'succès' if ok else 'échec'}")
