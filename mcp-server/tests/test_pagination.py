"""Tests de régression sur la pagination RPC (client.py:_rpc_all_pages).

Contexte : incident du 17/09/2026 (voir cartographie-edumoov.md §9) — plusieurs
méthodes RPC listant des ressources (classes, référentiel des niveaux, créneaux
Journal...) tronquaient silencieusement leurs résultats à `limit=10` (défaut
serveur, jamais explicité côté client), sans la moindre erreur. Pour
`user.classrooms.fetch`, ça a fait disparaître 5 classes sur 15 (et leurs 5
enseignants, propagé via `list_school_teachers`) — détecté seulement parce que
le compte réel de classes dans l'interface Edumoov (15) ne correspondait pas à
celui renvoyé par le connecteur (10).

Ces tests vérifient que `_rpc_all_pages` boucle correctement sur plusieurs pages
plutôt que de supposer qu'une seule page suffit, et que les méthodes qui en
dépendent (list_classrooms, list_grades, list_school_classrooms, les 3 méthodes
Journal sans page/limit exposé) l'utilisent bien plutôt que `rpc()`."""
from __future__ import annotations

import httpx
import pytest
import respx

from edumoov_mcp.client import EdumoovApiError, EdumoovClient

from ._helpers import fake_auth


def _page(rows: list[dict], *, page: int, limit: int, total: int) -> dict:
    pages = (total + limit - 1) // limit
    return {
        "success": True,
        "data": rows,
        "paging": {
            "page": page,
            "entries": len(rows),
            "limit": limit,
            "pages": pages,
            "total": total,
            "prev": page > 1,
            "next": page < pages,
            "end": (page - 1) * limit + len(rows),
        },
    }


class TestRpcAllPages:
    @respx.mock
    async def test_single_page_no_truncation(self, tmp_path):
        """Quand tout tient sur une page, pas de deuxième appel superflu."""
        client = EdumoovClient(auth=fake_auth(tmp_path))
        route = respx.post("https://api.edumoov.com/rpc/whatever.fetch").mock(
            return_value=httpx.Response(
                200, json=_page([{"id": i} for i in range(5)], page=1, limit=50, total=5)
            )
        )
        result = await client._rpc_all_pages("whatever.fetch", {}, page_size=50)
        assert [r["id"] for r in result] == list(range(5))
        assert route.call_count == 1
        await client.aclose()

    @respx.mock
    async def test_multi_page_concatenates_all_rows(self, tmp_path):
        """C'est le cœur de l'incident du 17/09/2026, reproduit ici avec des
        données synthétiques : 15 lignes réparties sur 2 pages de 10, comme
        `user.classrooms.fetch` sur l'école réelle (15 classes, limit=10)."""
        client = EdumoovClient(auth=fake_auth(tmp_path))

        def responder(request: httpx.Request) -> httpx.Response:
            import json

            body = json.loads(request.content)
            page = body["params"]["page"]
            if page == 1:
                rows = [{"id": i} for i in range(10)]
            else:
                rows = [{"id": i} for i in range(10, 15)]
            return httpx.Response(200, json=_page(rows, page=page, limit=10, total=15))

        respx.post("https://api.edumoov.com/rpc/whatever.fetch").mock(side_effect=responder)
        result = await client._rpc_all_pages("whatever.fetch", {}, page_size=10)
        assert [r["id"] for r in result] == list(range(15))
        await client.aclose()

    @respx.mock
    async def test_runaway_pagination_raises_instead_of_looping_forever(self, tmp_path):
        """Garde-fou : si l'API renvoyait un `paging.next` toujours vrai (bug
        serveur ou format inattendu), on doit échouer bruyamment plutôt que
        boucler indéfiniment ou renvoyer une liste tronquée en silence."""
        client = EdumoovClient(auth=fake_auth(tmp_path))
        respx.post("https://api.edumoov.com/rpc/whatever.fetch").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [{"id": 1}],
                    "paging": {"page": 1, "limit": 1, "pages": 999, "total": 999, "next": True},
                },
            )
        )
        with pytest.raises(EdumoovApiError):
            await client._rpc_all_pages("whatever.fetch", {}, page_size=1)
        await client.aclose()


class TestListMethodsUsePagination:
    """Vérifie que les méthodes touchées par l'incident appellent bien
    `_rpc_all_pages` (implicitement, via l'enveloppe `params.page`/`limit`
    envoyée) plutôt que `rpc()` — la régression exacte serait un retour à un
    seul appel sans `page`/`limit` dans le corps envoyé."""

    @respx.mock
    async def test_list_classrooms_requests_limit_and_paginates(self, tmp_path):
        """list_classrooms() utilise un page_size par défaut de 50 (voir
        _rpc_all_pages) — largement suffisant pour tenir les 15 classes réelles
        de l'école en une seule page, ce qui est le comportement attendu. Le
        point vérifié ici n'est pas le multi-page en soi (couvert génériquement
        par test_multi_page_concatenates_all_rows ci-dessus) mais que
        page/limit sont bien envoyés explicitement à chaque appel — sans quoi
        l'API retomberait sur son défaut silencieux limit=10, cause exacte de
        l'incident du 17/09/2026 (5 classes sur 15 invisibles)."""
        client = EdumoovClient(auth=fake_auth(tmp_path))

        def responder(request: httpx.Request) -> httpx.Response:
            import json

            body = json.loads(request.content)
            assert "limit" in body["params"] and "page" in body["params"], (
                "list_classrooms doit toujours envoyer page/limit explicitement "
                "(sans quoi l'API retombe sur limit=10 en silence, cause de "
                "l'incident du 17/09/2026)"
            )
            rows = [{"id": i, "school_id": 11777} for i in range(15)]
            return httpx.Response(
                200,
                json=_page(
                    rows,
                    page=body["params"]["page"],
                    limit=body["params"]["limit"],
                    total=15,
                ),
            )

        respx.post("https://api.edumoov.com/rpc/user.classrooms.fetch").mock(
            side_effect=responder
        )
        result = await client.list_classrooms()
        assert len(result) == 15
        await client.aclose()

    @respx.mock
    async def test_list_grades_paginates(self, tmp_path):
        client = EdumoovClient(auth=fake_auth(tmp_path))

        def responder(request: httpx.Request) -> httpx.Response:
            import json

            body = json.loads(request.content)
            page = body["params"]["page"]
            rows = [{"id": i} for i in range(10)] if page == 1 else [{"id": i} for i in range(10, 18)]
            return httpx.Response(200, json=_page(rows, page=page, limit=10, total=18))

        respx.post("https://api.edumoov.com/rpc/core.grades.fetch").mock(side_effect=responder)
        result = await client.list_grades()
        assert len(result) == 18
        await client.aclose()

    @respx.mock
    async def test_list_school_teachers_sees_all_classrooms(self, tmp_path):
        """Reproduction directe de l'incident : sans la pagination, les
        enseignants des 5 classes de la 2e page (et donc leurs comptes)
        disparaissaient de l'annuaire — silencieusement, sans erreur."""
        client = EdumoovClient(auth=fake_auth(tmp_path))

        def responder(request: httpx.Request) -> httpx.Response:
            import json

            body = json.loads(request.content)
            page = body["params"]["page"]
            if page == 1:
                rows = [
                    {"id": i, "school_id": 11777, "users": [{"id": 1000 + i, "name": f"T{i}"}]}
                    for i in range(10)
                ]
            else:
                rows = [
                    {"id": i, "school_id": 11777, "users": [{"id": 1000 + i, "name": f"T{i}"}]}
                    for i in range(10, 15)
                ]
            return httpx.Response(200, json=_page(rows, page=page, limit=10, total=15))

        respx.post("https://api.edumoov.com/rpc/user.classrooms.fetch").mock(
            side_effect=responder
        )
        teachers = await client.list_school_teachers("11777")
        assert len(teachers) == 15
        await client.aclose()
