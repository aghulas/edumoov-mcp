"""Rafraichissement du refresh_token via un vrai login navigateur automatise
(Playwright), en s'appuyant sur la session SSO Keycloak deja active plutot que
sur une recapture manuelle DevTools toutes les ~3h.

Principe (voir cartographie-edumoov.md §6.8 pour le detail complet) : on ne
tente PAS notre propre flow OAuth avec notre propre redirect_uri (verrouille
cote Keycloak pour tous les client_id testes). On laisse la vraie appli
app.edumoov.com faire SON login normal, avec SON client_id/redirect_uri deja
autorises, et on intercepte au niveau reseau la reponse de l'endpoint token
que son adaptateur keycloak-js appelle lui-meme (silent SSO via iframe, ou
apres saisie manuelle si aucune session n'est active).

Deux commandes :
  python -m edumoov_mcp.browser_auth login    -> connexion manuelle une fois
                                                  (bootstrap du profil
                                                  navigateur dedie).
  python -m edumoov_mcp.browser_auth refresh  -> reutilise la session SSO du
                                                  profil pour obtenir un token
                                                  frais sans intervention
                                                  humaine. A planifier
                                                  periodiquement (launchd)
                                                  pour ne plus jamais avoir a
                                                  recapturer a la main.

Note importante : les deux modes lancent Chromium en mode visible
(headless=False), PAS headless. Teste empiriquement : en headless=True, le
silent-SSO-check de Keycloak (iframe prompt=none) ne reconnait pas la session
active et retombe sur le vrai formulaire de login, meme avec un cookie SSO
valide — cause exacte non identifiee (suspicion : partitionnement des
cookies / traitement des cookies tiers different en headless). En
headless=False avec une session SSO valide, la reconnexion est fiable et ne
necessite aucune interaction humaine : une fenetre Chrome s'affiche
brievement puis se ferme toute seule. C'est le compromis retenu.

Ne journalise et n'affiche jamais le contenu d'un token.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from .auth import TokenStore, _TokenData
from .config import SETTINGS

PROFILE_DIR = Path.home() / ".edumoov-mcp" / "browser-profile"
APP_URL = "https://app.edumoov.com/"


class BrowserAuthError(RuntimeError):
    pass


def _run(timeout_seconds: int) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserAuthError(
            "playwright n'est pas installe dans ce venv. `pip install playwright` "
            "puis `python -m playwright install chromium`."
        ) from exc

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    captured: dict = {}

    def on_response(response):
        url = response.url
        if "protocol/openid-connect/token" in url and response.request.method == "POST":
            try:
                body = response.json()
            except Exception:
                return
            if "refresh_token" in body and "access_token" in body:
                captured["body"] = body

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, viewport={"width": 1280, "height": 900}
        )
        try:
            context.on("response", on_response)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(APP_URL)

            deadline = time.time() + timeout_seconds
            next_reload = time.time() + 15
            while time.time() < deadline and "body" not in captured:
                time.sleep(1)
                if time.time() > next_reload and "body" not in captured:
                    try:
                        page.reload(wait_until="networkidle", timeout=15000)
                    except Exception:
                        pass
                    next_reload = time.time() + 15

            if "body" not in captured:
                raise BrowserAuthError(
                    "Aucun token capture dans le delai imparti. Session SSO probablement "
                    "expiree — relance `python -m edumoov_mcp.browser_auth login` pour te "
                    "reconnecter manuellement."
                )

            body = captured["body"]
            data = _TokenData(
                access_token=body["access_token"],
                refresh_token=body["refresh_token"],
                expires_at=time.time() + float(body.get("expires_in", 60)),
            )
            TokenStore().save(data)
            # stderr, pas stdout : ce module peut desormais etre invoque comme
            # sous-processus depuis l'auth automatique du serveur MCP (voir auth.py),
            # dont le stdout est le canal JSON-RPC — n'y jamais rien ecrire.
            print(f"Token rafraichi et enregistre dans {SETTINGS.token_store_path}.", file=sys.stderr)
        finally:
            context.close()


def _cli() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("login", "refresh"):
        print("Usage: python -m edumoov_mcp.browser_auth [login|refresh]", file=sys.stderr)
        raise SystemExit(2)
    mode = sys.argv[1]
    if mode == "login":
        print("Fenetre Chrome dediee (profil separe) en cours d'ouverture — connecte-toi normalement.")
        _run(timeout_seconds=300)
    else:
        _run(timeout_seconds=120)


if __name__ == "__main__":
    _cli()
