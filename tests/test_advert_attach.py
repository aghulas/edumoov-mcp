"""Pièce jointe d'une annonce (06/10/2026) — sans réseau."""
from __future__ import annotations

import httpx
import pytest
import respx

import edumoov_mcp.server as server_module  # noqa: F401  (charge les outils dans le bon ordre)
import edumoov_mcp.write_tools as wt
from edumoov_mcp.client import EdumoovApiError, EdumoovClient, ForbiddenEndpointError
from edumoov_mcp.config import SETTINGS
from edumoov_mcp.writes import WriteGate

from ._helpers import fake_auth

RPC = SETTINGS.rpc_base


def _pdf(d, nom="invitation.pdf", taille=10):
    p = d / nom
    p.write_bytes(b"%PDF-1.4\n" + b"x" * taille)
    return p


def test_controle_du_fichier(tmp_path, monkeypatch):
    monkeypatch.setenv("EDUMOOV_ATTACH_ROOTS", str(tmp_path / "ok"))
    (tmp_path / "ok").mkdir()
    assert wt.check_attachment(str(_pdf(tmp_path / "ok"))).name == "invitation.pdf"
    for mauvais in (_pdf(tmp_path, "ailleurs.pdf"),                       # hors racine
                    _pdf(tmp_path / "ok", "Mandat SEPA.pdf"),              # bancaire
                    _pdf(tmp_path / "ok", "gros.pdf", 3 * 1024 * 1024),    # > 2 Mo
                    tmp_path / "ok" / "absent.pdf"):                       # introuvable
        with pytest.raises(ValueError):
            wt.check_attachment(str(mauvais))
    sh = tmp_path / "ok" / "x.sh"
    sh.write_text("x")
    with pytest.raises(ValueError):
        wt.check_attachment(str(sh))


def test_gate_upload_exige_methode_et_fichier():
    g = WriteGate(enabled=True)
    with pytest.raises(ValueError):
        g.prepare("school.messages.create", {}, {}, summary="", preview={}, transport="upload", file_path="/x.pdf")
    with pytest.raises(ValueError):
        g.prepare("school.medias.url", {}, {}, summary="", preview={}, transport="upload")
    r = g.prepare("school.medias.url", {"school_id": 1}, {"model": "Message", "key": "k"},
                  summary="s", preview={}, transport="upload", file_path="/x.pdf")
    assert g.pop(r["confirmation_token"]).file_path == "/x.pdf"


@respx.mock
async def test_upload_refuse_un_hote_etranger(tmp_path):
    respx.post(f"{RPC}/school.medias.url").mock(return_value=httpx.Response(
        200, json={"success": True, "data": {"url": "https://evil.example.com/up", "sig": "s", "payload": "p"}}))
    c = EdumoovClient(auth=fake_auth(tmp_path))
    with pytest.raises(EdumoovApiError):
        await c.upload_media("school.medias.url", {"school_id": 1}, {"model": "Message", "key": "k"},
                             str(_pdf(tmp_path)))


@respx.mock
async def test_upload_envoie_sig_payload_et_fichier(tmp_path):
    sign = respx.post(f"{RPC}/school.medias.url").mock(return_value=httpx.Response(
        200, json={"success": True, "data": {"url": "https://filerz.edumoov.com/year?x=1", "sig": "S",
                                             "payload": "P"}}))
    up = respx.post("https://filerz.edumoov.com/year").mock(return_value=httpx.Response(
        200, json={"id": "m1", "name": "invitation", "size": 19, "extension": "pdf"}))
    c = EdumoovClient(auth=fake_auth(tmp_path))
    m = await c.upload_media("school.medias.url", {"school_id": 1}, {"model": "Message", "key": "k", "links": []},
                             str(_pdf(tmp_path)))
    assert m["id"] == "m1"
    corps = sign.calls[0].request.content
    assert b'"method": "POST"' in corps or b'"method":"POST"' in corps
    multipart = up.calls[0].request.content
    assert b'name="sig"' in multipart and b'name="payload"' in multipart and b'filename="invitation.pdf"' in multipart


async def test_upload_methode_hors_liste(tmp_path):
    c = EdumoovClient(auth=fake_auth(tmp_path))
    with pytest.raises(ForbiddenEndpointError):
        await c.upload_media("school.messages.create", {}, {}, "/x.pdf")


@respx.mock
async def test_upload_cahier_envoie_le_lien_primaire(tmp_path):
    sign = respx.get(f"{SETTINGS.rest_base}/core/classroom/12/medias/url").mock(return_value=httpx.Response(
        200, json={"success": True, "data": {"url": "https://filerz.edumoov.com/edumoov?x=1", "sig": "S",
                                             "payload": "P"}}))
    up = respx.post("https://filerz.edumoov.com/edumoov").mock(return_value=httpx.Response(
        200, json={"id": "m2", "name": "invitation", "size": 19, "extension": "pdf"}))
    c = EdumoovClient(auth=fake_auth(tmp_path))
    m = await c.upload_media("core.classroom.medias.url", {"classroom_id": 12},
                             {"model": "HomeworkMessage", "key": "uuid-1"}, str(_pdf(tmp_path)))
    assert m["id"] == "m2" and sign.called
    multipart = up.calls[0].request.content
    for champ in (b'name="model"', b"HomeworkMessage", b'name="key"', b"uuid-1", b'name="sig"'):
        assert champ in multipart


def test_cahier_limite_10_mo(tmp_path, monkeypatch):
    monkeypatch.setenv("EDUMOOV_ATTACH_ROOTS", str(tmp_path))
    moyen = _pdf(tmp_path, "moyen.pdf", 3 * 1024 * 1024)
    assert wt.check_attachment(str(moyen), wt.CAHIER_ATTACH_MAX_BYTES).name == "moyen.pdf"
    with pytest.raises(ValueError):
        wt.check_attachment(str(_pdf(tmp_path, "enorme.pdf", 11 * 1024 * 1024)), wt.CAHIER_ATTACH_MAX_BYTES)
