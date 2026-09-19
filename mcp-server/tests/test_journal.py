"""Tests de régression sur les 4 méthodes Journal ajoutées le 16/09/2026
(cartographie-edumoov.md §6.6). L'enjeu principal ici n'est pas le contenu des
réponses (schéma non confirmé sur données réelles, voir §6.6) mais le nom de
méthode RPC et le domaine exacts (`user.` vs `classroom.`) — trouvés
empiriquement par essais successifs contre l'API réelle, donc faciles à
regresser silencieusement si quelqu'un "simplifie" le nommage plus tard."""
from __future__ import annotations

import json

import httpx
import respx

from edumoov_mcp.client import EdumoovClient

from ._helpers import fake_auth


@respx.mock
async def test_list_journal_lessons_calls_user_domain(tmp_path):
    client = EdumoovClient(auth=fake_auth(tmp_path))
    route = respx.post("https://api.edumoov.com/rpc/user.clog_lessons.fetch").mock(
        return_value=httpx.Response(200, json={"success": True, "data": []})
    )
    result = await client.list_journal_lessons("90003", page=2, limit=50)
    assert result == []
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"params": {"user_id": "90003", "page": 2, "limit": 50}, "payload": {}}
    await client.aclose()


@respx.mock
async def test_list_journal_schedules_calls_user_domain(tmp_path):
    client = EdumoovClient(auth=fake_auth(tmp_path))
    route = respx.post("https://api.edumoov.com/rpc/user.clog_schedules.fetch").mock(
        return_value=httpx.Response(200, json={"success": True, "data": []})
    )
    result = await client.list_journal_schedules("90003")
    assert result == []
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "params": {"user_id": "90003", "page": 1, "limit": 50},
        "payload": {},
    }  # page/limit désormais explicites — voir client.py:_rpc_all_pages (17/09/2026)
    await client.aclose()


@respx.mock
async def test_list_journal_slots_calls_classroom_domain(tmp_path):
    """Seule des 4 méthodes Journal à utiliser le domaine `classroom` plutôt
    que `user` — confirmé empiriquement (user.clog_slots.fetch n'a pas été
    testé, classroom.clog_slots.fetch a directement fonctionné)."""
    client = EdumoovClient(auth=fake_auth(tmp_path))
    route = respx.post("https://api.edumoov.com/rpc/classroom.clog_slots.fetch").mock(
        return_value=httpx.Response(200, json={"success": True, "data": []})
    )
    result = await client.list_journal_slots("90002")
    assert result == []
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "params": {"classroom_id": "90002", "page": 1, "limit": 50},
        "payload": {},
    }  # page/limit désormais explicites — voir client.py:_rpc_all_pages (17/09/2026)
    await client.aclose()


@respx.mock
async def test_list_journal_pedagroups_calls_user_domain(tmp_path):
    client = EdumoovClient(auth=fake_auth(tmp_path))
    route = respx.post("https://api.edumoov.com/rpc/user.clog_pedagroups.fetch").mock(
        return_value=httpx.Response(200, json={"success": True, "data": []})
    )
    result = await client.list_journal_pedagroups("90003")
    assert result == []
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "params": {"user_id": "90003", "page": 1, "limit": 50},
        "payload": {},
    }  # page/limit désormais explicites — voir client.py:_rpc_all_pages (17/09/2026)
    await client.aclose()
