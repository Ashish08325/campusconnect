"""Unit and API contract tests for the CampusConnect Flask application."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app as backend_app  # noqa: E402
from postgrest import APIError  # noqa: E402

USER_ORG = "11111111-1111-4111-8111-111111111111"
USER_STUDENT = "22222222-2222-4222-8222-222222222222"
EVENT_1 = "33333333-3333-4333-8333-333333333333"
EVENT_FULL = "44444444-4444-4444-8444-444444444444"
EVENT_CLOSED = "55555555-5555-4555-8555-555555555555"
REG_1 = "66666666-6666-4666-8666-666666666666"


class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data if data is not None else []
        self.count = count


class FakeQuery:
    def __init__(self, db_store: dict[str, list[dict]], table: str):
        self.store = db_store
        self.table = table
        self.op = "select"
        self.payload = None
        self.filters: list[tuple[str, str, object]] = []
        self._limit = None
        self._range = None
        self._count_mode = None

    def select(self, *_args, count=None):
        if self.op == "select":
            self._count_mode = count
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = dict(payload)
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = dict(payload)
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def ilike(self, col, pattern):
        self.filters.append(("ilike", col, pattern))
        return self

    def in_(self, col, values):
        self.filters.append(("in", col, list(values)))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        self._limit = count
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def _matches(self, row: dict) -> bool:
        for kind, col, val in self.filters:
            cell = row.get(col)
            if kind == "eq" and str(cell) != str(val):
                return False
            if kind == "in" and cell not in val:
                return False
            if kind == "ilike":
                needle = str(val).strip("%").lower()
                if needle not in str(cell or "").lower():
                    return False
        return True

    def execute(self):
        rows = self.store[self.table]
        if self.op == "insert":
            new_row = dict(self.payload)
            if "id" not in new_row:
                new_row["id"] = f"99999999-9999-4999-8999-{len(rows) + 1:012d}"
            if self.table == "registrations" and "created_at" not in new_row:
                new_row["created_at"] = "2026-11-25T10:00:00+00:00"
            rows.append(new_row)
            return FakeResult([dict(new_row)])

        matched = [r for r in rows if self._matches(r)]

        if self.op == "update":
            updated = []
            for r in matched:
                r.update(self.payload)
                updated.append(dict(r))
            return FakeResult(updated)

        if self.op == "delete":
            deleted = [dict(r) for r in matched]
            self.store[self.table] = [r for r in rows if r not in matched]
            return FakeResult(deleted)

        total = len(matched)
        result_rows = [dict(r) for r in matched]
        if self._range is not None:
            start, end = self._range
            result_rows = result_rows[start : end + 1]
        if self._limit is not None:
            result_rows = result_rows[: self._limit]
        return FakeResult(result_rows, count=total if self._count_mode == "exact" else None)


class FakeAuth:
    def __init__(self, users_by_token: dict[str, SimpleNamespace]):
        self.users_by_token = users_by_token

    def get_user(self, jwt: str | None = None):
        user = self.users_by_token.get(jwt or "")
        return SimpleNamespace(user=user) if user else None


class FakeSupabase:
    def __init__(self, db_store: dict[str, list[dict]], users_by_token: dict[str, SimpleNamespace]):
        self.store = db_store
        self.auth = FakeAuth(users_by_token)

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self.store, name)


class CampusConnectApiTests(unittest.TestCase):
    def setUp(self):
        self.store = {
            "users": [
                {"id": USER_ORG, "email": "org@campus.edu", "role": "organizer"},
                {"id": USER_STUDENT, "email": "stu@campus.edu", "role": "student"},
            ],
            "events": [
                {
                    "id": EVENT_1,
                    "organizer_id": USER_ORG,
                    "title": "Annual Quad Hackathon",
                    "capacity": 50,
                    "registered_count": 1,
                    "status": "open",
                    "category": "Hackathon",
                    "starts_at": "2026-12-01T10:00:00+00:00",
                    "ends_at": "2026-12-01T18:00:00+00:00",
                },
                {
                    "id": EVENT_FULL,
                    "organizer_id": USER_ORG,
                    "title": "Full Workshop",
                    "capacity": 10,
                    "registered_count": 10,
                    "status": "open",
                    "category": "Academic",
                    "starts_at": "2026-12-02T10:00:00+00:00",
                    "ends_at": None,
                },
                {
                    "id": EVENT_CLOSED,
                    "organizer_id": USER_ORG,
                    "title": "Closed Meetup",
                    "capacity": 30,
                    "registered_count": 2,
                    "status": "closed",
                    "category": "Other",
                    "starts_at": None,
                    "ends_at": None,
                },
            ],
            "registrations": [
                {
                    "id": REG_1,
                    "user_id": USER_ORG,
                    "event_id": EVENT_1,
                    "created_at": "2026-11-24T12:00:00+00:00",
                }
            ],
        }
        self.tokens = {
            "org-token": SimpleNamespace(id=USER_ORG, email="org@campus.edu", user_metadata={"role": "organizer"}),
            "stu-token": SimpleNamespace(id=USER_STUDENT, email="stu@campus.edu", user_metadata={"role": "student"}),
        }
        self.fake_client = FakeSupabase(self.store, self.tokens)
        self.client = backend_app.app.test_client()

        self.patches = [
            patch.object(backend_app, "supabase", self.fake_client),
            patch.object(backend_app, "SUPABASE_URL", "https://example.supabase.co"),
            patch.object(backend_app, "SUPABASE_KEY", "test-key"),
            patch.object(backend_app, "scoped_database", lambda _tok: self.fake_client),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_frontend_html_clean_and_api_index(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        res.close()
        self.assertIn("WELCOME TO <span>CAMPUSCONNECT</span>", html)
        self.assertNotIn(">??<", html)
        self.assertNotIn("\ufffd", html)

        api_res = self.client.get("/api")
        self.assertEqual(api_res.status_code, 200)
        self.assertEqual(api_res.get_json()["authentication"], "supabase-jwt")

    def test_public_event_browsing_and_validation(self):
        res = self.client.get("/api/events?q=Hackathon")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["seats_remaining"], 49)

        bad_id = self.client.get("/api/events/not-a-uuid")
        self.assertEqual(bad_id.status_code, 400)

        missing = self.client.get("/api/events/00000000-0000-4000-8000-000000000000")
        self.assertEqual(missing.status_code, 404)

    def test_auth_required_for_protected_routes(self):
        self.assertEqual(self.client.post("/api/events", json={"title": "Test", "capacity": 10}).status_code, 401)
        self.assertEqual(self.client.post("/api/registrations", json={"event_id": EVENT_1}).status_code, 401)
        self.assertEqual(self.client.get("/api/me/registrations").status_code, 401)
        self.assertEqual(self.client.get(f"/api/events/{EVENT_1}/registrations").status_code, 401)
        self.assertEqual(self.client.get("/api/users").status_code, 401)

    def test_event_creation_validation_and_ownership(self):
        bad_dates = self.client.post(
            "/api/events",
            headers={"Authorization": "Bearer org-token"},
            json={
                "title": "Invalid Dates",
                "capacity": 20,
                "starts_at": "2026-12-05T14:00:00Z",
                "ends_at": "2026-12-05T12:00:00Z",
            },
        )
        self.assertEqual(bad_dates.status_code, 400)

        bad_cap = self.client.post(
            "/api/events",
            headers={"Authorization": "Bearer org-token"},
            json={"title": "Zero Cap", "capacity": 0},
        )
        self.assertEqual(bad_cap.status_code, 400)

        forbidden_edit = self.client.patch(
            f"/api/events/{EVENT_1}",
            headers={"Authorization": "Bearer stu-token"},
            json={"title": "Hijacked"},
        )
        self.assertEqual(forbidden_edit.status_code, 403)

        ok_create = self.client.post(
            "/api/events",
            headers={"Authorization": "Bearer org-token"},
            json={"title": "Spring Jazz Night", "capacity": 80, "category": "Arts & Culture"},
        )
        self.assertEqual(ok_create.status_code, 201)
        self.assertEqual(ok_create.get_json()["organizer_id"], USER_ORG)

    def test_registration_contract_201_409_and_organizer_list(self):
        reg_ok = self.client.post(
            "/api/registrations",
            headers={"Authorization": "Bearer stu-token"},
            json={"event_id": EVENT_1},
        )
        self.assertEqual(reg_ok.status_code, 201)

        reg_dup = self.client.post(
            "/api/registrations",
            headers={"Authorization": "Bearer stu-token"},
            json={"event_id": EVENT_1},
        )
        self.assertEqual(reg_dup.status_code, 409)

        reg_full = self.client.post(
            "/api/registrations",
            headers={"Authorization": "Bearer stu-token"},
            json={"event_id": EVENT_FULL},
        )
        self.assertEqual(reg_full.status_code, 409)

        reg_closed = self.client.post(
            "/api/registrations",
            headers={"Authorization": "Bearer stu-token"},
            json={"event_id": EVENT_CLOSED},
        )
        self.assertEqual(reg_closed.status_code, 409)

        my_regs = self.client.get(
            "/api/me/registrations",
            headers={"Authorization": "Bearer stu-token"},
        )
        self.assertEqual(my_regs.status_code, 200)
        self.assertEqual(len(my_regs.get_json()), 1)
        self.assertEqual(my_regs.get_json()[0]["event"]["title"], "Annual Quad Hackathon")

        stu_attendees = self.client.get(
            f"/api/events/{EVENT_1}/registrations",
            headers={"Authorization": "Bearer stu-token"},
        )
        self.assertEqual(stu_attendees.status_code, 403)

        org_attendees = self.client.get(
            f"/api/events/{EVENT_1}/registrations",
            headers={"Authorization": "Bearer org-token"},
        )
        self.assertEqual(org_attendees.status_code, 200)
        self.assertEqual(len(org_attendees.get_json()), 2)

    def test_missing_config_returns_json_503(self):
        with patch.object(backend_app, "supabase", None):
            res = self.client.get("/api/events")
            self.assertEqual(res.status_code, 503)
            self.assertIn("error", res.get_json())

    def test_apierror_maps_23505_to_409(self):
        err = APIError({"message": "duplicate key value violates unique constraint", "code": "23505"})
        with backend_app.app.test_request_context():
            resp, code = backend_app.handle_database_error(err)
            self.assertEqual(code, 409)
            self.assertIn("error", resp.get_json())

    def test_list_events_falls_back_when_starts_at_column_missing(self):
        orig_order = FakeQuery.order

        def order_missing_starts_at(self_q, col, *args, **kwargs):
            if col == "starts_at":
                raise APIError({"message": "column events.starts_at does not exist", "code": "42703"})
            return orig_order(self_q, col, *args, **kwargs)

        with patch.object(FakeQuery, "order", order_missing_starts_at):
            res = self.client.get("/api/events")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(len(res.get_json()), 3)

    def test_cors_preflight_and_headers(self):
        preflight = self.client.open("/api/events", method="OPTIONS", headers={"Origin": "https://campusconnect.vercel.app"})
        self.assertEqual(preflight.status_code, 204)
        self.assertEqual(preflight.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertIn("Authorization", preflight.headers.get("Access-Control-Allow-Headers", ""))

        res = self.client.get("/api/events", headers={"Origin": "https://campusconnect.vercel.app"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("Access-Control-Allow-Origin"), "*")


if __name__ == "__main__":
    unittest.main()
