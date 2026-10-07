"""Codes familles en PDF (07/10/2026) — sans réseau."""
from __future__ import annotations

import dataclasses
import json
import os
import stat

import httpx
import pytest
import respx

import edumoov_mcp.server as server_module  # noqa: F401
import edumoov_mcp.family_codes as fc
from edumoov_mcp.client import EdumoovClient
from edumoov_mcp.config import SETTINGS

from ._helpers import fake_auth

SECRET_KEY, SECRET_CODE = "fam-ident-123", "S3cr3t-987"


class FakeClient:
    def __init__(self):
        self._http = None
        self.demandes = []

    async def get_classroom(self, cid):
        return {"id": cid, "name": "CM1 B (TEST)"}

    async def list_pupils(self, cid):
        return [{"id": 11, "name": "DUPONT", "firstname": "Léa"}, {"id": 12, "name": "MARTIN", "firstname": "Paul"}]

    async def fetch_family_codes(self, cid, ids):
        self.demandes.append(ids)
        return [{"name": "DUPONT", "firstname": "Léa", "key": SECRET_KEY, "code": SECRET_CODE}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "SETTINGS", dataclasses.replace(SETTINGS, download_dir=str(tmp_path)))
    monkeypatch.setenv("EDUMOOV_FAMILY_CODES", "1")
    client = FakeClient()
    monkeypatch.setattr(fc, "_get_client", lambda: client)

    async def no_template(c):
        return None
    monkeypatch.setattr(fc, "_template", no_template)
    return tmp_path, client


async def test_refuse_sans_activation(env, monkeypatch):
    monkeypatch.delenv("EDUMOOV_FAMILY_CODES")
    with pytest.raises(ValueError):
        await fc.edumoov_family_codes_pdf("57281", ["11"])


async def test_refuse_sans_dossier_local(env, monkeypatch):
    monkeypatch.setattr(fc, "SETTINGS", dataclasses.replace(SETTINGS, download_dir=None))
    with pytest.raises(ValueError):
        await fc.edumoov_family_codes_pdf("57281", ["11"])


async def test_refuse_eleve_hors_classe(env):
    with pytest.raises(ValueError):
        await fc.edumoov_family_codes_pdf("57281", ["11", "99"])


async def test_fiche_generee_sans_exposer_les_codes(env):
    tmp, client = env
    r = await fc.edumoov_family_codes_pdf("57281", ["11"])
    assert client.demandes == [[11]]
    texte = json.dumps(r, ensure_ascii=False)
    assert SECRET_KEY not in texte and SECRET_CODE not in texte
    f = r["fichiers"][0]["fichier"]
    assert os.path.basename(f) == "codes_famille_CM1_B_DUPONT_Lea.pdf"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert open(f, "rb").read(4) == b"%PDF"
    # jamais écrasé
    r2 = await fc.edumoov_family_codes_pdf("57281", ["11"])
    assert r2["fichiers"][0]["fichier"] != f


async def test_limite_par_appel(env):
    with pytest.raises(ValueError):
        await fc.edumoov_family_codes_pdf("57281", [str(i) for i in range(31)])


@respx.mock
async def test_client_appelle_le_chemin_dedie(tmp_path):
    route = respx.post(f"{SETTINGS.rest_base}/core/classroom/57281/pupils/codes").mock(
        return_value=httpx.Response(200, json={"success": True, "data": [{"name": "A", "firstname": "B",
                                                                          "key": "k", "code": 1}]}))
    c = EdumoovClient(auth=fake_auth(tmp_path))
    data = await c.fetch_family_codes("57281", [11, 12])
    assert data[0]["name"] == "A"
    assert json.loads(route.calls[0].request.content) == [11, 12]
