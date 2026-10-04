"""Téléchargement des registres d'appel (04/10/2026) — school_tools.edumoov_registers_download.
Enjeux : appel exact au front (types, mois, classes), attente du job, fichier
enregistré en local uniquement, URL signée jamais renvoyée, hôte de téléchargement
vérifié, refus sans dossier local configuré."""
from __future__ import annotations

import dataclasses
import io
import json
import zipfile

import httpx
import pytest
import respx

import edumoov_mcp.school_tools as st
import edumoov_mcp.server as server_module
from edumoov_mcp.client import EdumoovClient, ForbiddenEndpointError

from ._helpers import fake_auth

RPC = "https://api.edumoov.com/rpc"
URL = "https://filerz.edumoov.com/f/abc?temp_url_sig=secret&temp_url_expires=1"
CLASSROOMS = [
    {"id": 101, "name": "CP A", "school_id": 90001, "release": False, "deleted": False},
    {"id": 102, "name": "CE1 B", "school_id": 90001, "release": False, "deleted": False},
    {"id": 103, "name": "Ancienne", "school_id": 90001, "release": True, "deleted": False},
    {"id": 201, "name": "Autre école", "school_id": 90009, "release": False, "deleted": False},
]


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("appel/2026-09-appel-septembre.pdf", b"%PDF-1.4 a")
        zf.writestr("cantine/2026-09-cantine-septembre.pdf", b"%PDF-1.4 b")
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "_client", EdumoovClient(auth=fake_auth(tmp_path)))
    monkeypatch.setattr(
        st, "SETTINGS",
        dataclasses.replace(st.SETTINGS, default_school_id="90001", download_dir=str(tmp_path / "dl"), job_timeout_seconds=30),
    )
    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    respx.post(f"{RPC}/user.classrooms.fetch").mock(
        return_value=httpx.Response(200, json={"success": True, "data": CLASSROOMS})
    )
    return tmp_path / "dl"


async def _no_sleep(_):
    return None


def _mock_job(final_status=7, url=URL):
    start = respx.post(f"{RPC}/school.pupilsappeals.downloadRegisters").mock(
        return_value=httpx.Response(202, json={"success": True, "job": {"id": "j1", "rpc": "appeal.downloadRegisters", "status": 5}})
    )
    done = {"id": "j1", "status": final_status, "result": {"download": {"url": url}}, "error": {"message": "boom"}}
    respx.post(f"{RPC}/user.jobs.get").mock(
        side_effect=[
            httpx.Response(200, json={"success": True, "data": {"id": "j1", "status": 6}}),
            httpx.Response(200, json={"success": True, "data": done}),
        ]
    )
    return start


@respx.mock
async def test_download_all_classes_saves_zip_without_url(env):
    start = _mock_job()
    respx.get(URL).mock(return_value=httpx.Response(200, content=_zip(), headers={"content-type": "application/zip"}))
    out = await st.edumoov_registers_download(["2026-09"], extract=True, color=False)
    sent = json.loads(start.calls[0].request.content)
    assert sent["params"] == {"school_id": 90001}
    assert sent["payload"] == {
        "types": ["appeal", "eat", "study", "play"], "months": ["2026-09-01"],
        "classroomIds": [101, 102], "color": False,
    }
    assert out["file"].endswith("registres_2026-09_toutes-classes_nb.zip")
    assert (env / "registres" / "registres_2026-09_toutes-classes_nb.zip").exists()
    assert out["pdf_files"] == ["appel/2026-09-appel-septembre.pdf", "cantine/2026-09-cantine-septembre.pdf"]
    assert (env / "registres" / "registres_2026-09_toutes-classes_nb" / "appel" / "2026-09-appel-septembre.pdf").exists()
    assert "secret" not in json.dumps(out) and "filerz" not in json.dumps(out)


@respx.mock
async def test_download_one_class_types_aliases_and_no_overwrite(env):
    start = _mock_job()
    respx.get(URL).mock(return_value=httpx.Response(200, content=_zip()))
    (env / "registres").mkdir(parents=True)
    (env / "registres" / "registres_2026-09_a_2026-10_CE1-B.zip").write_bytes(b"old")
    out = await st.edumoov_registers_download(["2026-10", "2026-09-15"], classroom_ids=["102"], types=["appel", "cantine"])
    sent = json.loads(start.calls[0].request.content)["payload"]
    assert sent == {"types": ["appeal", "eat"], "months": ["2026-09-01", "2026-10-01"], "classroomIds": [102], "color": True}
    assert out["file"].endswith("registres_2026-09_a_2026-10_CE1-B_2.zip")  # couleur par défaut
    assert (env / "registres" / "registres_2026-09_a_2026-10_CE1-B.zip").read_bytes() == b"old"


@respx.mock
async def test_refusals(env, monkeypatch):
    with pytest.raises(ValueError, match="inconnue"):
        await st.edumoov_registers_download(["2026-09"], classroom_ids=["201"])
    with pytest.raises(ValueError, match="Type"):
        await st.edumoov_registers_download(["2026-09"], types=["absences"])
    with pytest.raises(ValueError, match="Mois"):
        await st.edumoov_registers_download(["sept"])
    monkeypatch.setattr(st, "SETTINGS", dataclasses.replace(st.SETTINGS, download_dir=None))
    with pytest.raises(ValueError, match="EDUMOOV_DOWNLOAD_DIR"):
        await st.edumoov_registers_download(["2026-09"])


@respx.mock
async def test_job_failure_and_foreign_host(env):
    _mock_job(final_status=9)
    with pytest.raises(RuntimeError, match="échec"):
        await st.edumoov_registers_download(["2026-09"])
    _mock_job(url="https://evil.example.com/x.zip")
    with pytest.raises(ForbiddenEndpointError):
        await st.edumoov_registers_download(["2026-09"])


def test_signed_url_redacted_in_httpx_logs(caplog):
    import logging

    import edumoov_mcp.client  # noqa: F401 — installe le filtre

    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info('HTTP Request: %s %s "%s"', "GET", httpx.URL(URL), "HTTP/1.1 200 OK")
    assert "secret" not in caplog.text and "signature masquée" in caplog.text


# ---------------------------------------------------------------- feuilles d'appel
def _mock_pdf_job(scope):
    route = respx.post(f"{RPC}/{scope}.jobs.tempPdf").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"id": "p1", "rpc": "pdf.temporary", "status": 5}})
    )
    respx.post(f"{RPC}/user.jobs.get").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"id": "p1", "status": 7, "result": {"download": {"url": URL}}}})
    )
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"%PDF-1.4 x"))
    return route


@respx.mock
async def test_appeal_sheet_day_whole_school(env):
    route = _mock_pdf_job("school")
    out = await st.edumoov_appeal_sheet_download("jour", date="2026-09-01")
    sent = json.loads(route.calls[0].request.content)
    assert sent["params"] == {"app": "direction", "school_id": 90001}
    assert sent["payload"]["path"] == "/appeals/2026-09-01/color"
    assert out["files"][0]["file"].endswith("appels/appel_2026-09-01_ecole.pdf")
    assert (env / "appels" / "appel_2026-09-01_ecole.pdf").read_bytes() == b"%PDF-1.4 x"
    assert "secret" not in json.dumps(out)


@respx.mock
async def test_appeal_sheet_blank_per_class_and_grey(env):
    route = _mock_pdf_job("classroom")
    out = await st.edumoov_appeal_sheet_download("vierge", classroom_ids=["101", "102"])
    paths = [json.loads(c.request.content)["payload"]["path"] for c in route.calls]
    assert len(paths) == 2 and all(p.endswith("/empty") for p in paths)
    assert {json.loads(c.request.content)["params"]["classroom_id"] for c in route.calls} == {101, 102}
    assert [f["file"].rsplit("/", 1)[1] for f in out["files"]] == ["appel_vierge_CP-A.pdf", "appel_vierge_CE1-B.pdf"]
    assert out["date"] is None
    out = await st.edumoov_appeal_sheet_download("jour", date="2026-09-03", classroom_ids=["101"], color=False)
    assert out["files"][0]["file"].endswith("appel_2026-09-03_CP-A_nb.pdf")
    assert json.loads(route.calls[-1].request.content)["payload"]["path"] == "/appeals/2026-09-03/grey"


@respx.mock
async def test_appeal_sheet_refusals(env):
    with pytest.raises(ValueError, match="kind"):
        await st.edumoov_appeal_sheet_download("semaine")
    with pytest.raises(ValueError, match="inconnue"):
        await st.edumoov_appeal_sheet_download("jour", classroom_ids=["201"])
    client = server_module._client
    with pytest.raises(ForbiddenEndpointError):
        await client.start_pdf_job("school", "90001", app="direction", path="/pupils/codes", filename="x")
    with pytest.raises(ForbiddenEndpointError):
        await client.start_pdf_job("user", "1", app="direction", path="/appeals/2026-09-01/color", filename="x")
