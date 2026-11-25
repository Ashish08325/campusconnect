"""CampusConnect REST API and static frontend server backed by Supabase."""

from __future__ import annotations

import os
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, send_file
from postgrest import APIError
from supabase import Client, ClientOptions, create_client

load_dotenv()

app = Flask(__name__)
PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")
LOCAL_FRONTEND_INDEX = PROJECT_DIR / "frontend" / "app" / "index.html"
BUNDLED_FRONTEND_INDEX = PROJECT_DIR / "static" / "index.html"
FRONTEND_INDEX = (
    LOCAL_FRONTEND_INDEX if LOCAL_FRONTEND_INDEX.is_file() else BUNDLED_FRONTEND_INDEX
)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY")
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "*").strip() or "*"
supabase: Client | None = (
    create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
)


@app.before_request
def handle_cors_preflight():
    if request.method == "OPTIONS":
        return app.make_response(("", 204))


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if CORS_ALLOWED_ORIGINS == "*":
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin and origin in {o.strip() for o in CORS_ALLOWED_ORIGINS.split(",")}:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    return response

VALID_EVENT_STATUSES = {"upcoming", "open", "ongoing", "full", "closed", "cancelled"}
CLOSED_EVENT_STATUSES = {"closed", "cancelled", "full"}
VALID_USER_ROLES = {"student", "organizer"}

EVENT_CORE_FIELDS = {"title", "capacity", "status"}
EVENT_MIGRATION_V1_FIELDS = {"category", "starts_at", "ends_at"}
EVENT_OPTIONAL_FIELDS = {"description", "location", "campus", "club_name", "is_featured"}
EVENT_WRITABLE_FIELDS = EVENT_CORE_FIELDS | EVENT_MIGRATION_V1_FIELDS | EVENT_OPTIONAL_FIELDS
EVENT_FALLBACK_STRIPPABLE = EVENT_MIGRATION_V1_FIELDS | EVENT_OPTIONAL_FIELDS

USER_UPDATABLE_FIELDS = {"role"}


class AuthError(Exception):
    """Raised when authentication or authorization fails."""

    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ConflictError(Exception):
    """Raised when an operation violates a domain uniqueness or state rule."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(Exception):
    """Raised when a requested resource does not exist."""

    def __init__(self, message: str = "Record not found"):
        super().__init__(message)
        self.message = message


@app.errorhandler(AuthError)
def handle_auth_error(error: AuthError):
    return jsonify({"error": error.message}), error.status_code


@app.errorhandler(ConflictError)
def handle_conflict_error(error: ConflictError):
    return jsonify({"error": error.message}), 409


@app.errorhandler(NotFoundError)
def handle_not_found_error(error: NotFoundError):
    return jsonify({"error": error.message}), 404


@app.errorhandler(ValueError)
def handle_validation_error(error: ValueError):
    return jsonify({"error": str(error)}), 400


@app.errorhandler(RuntimeError)
def handle_runtime_error(error: RuntimeError):
    return jsonify({"error": str(error)}), 503


@app.errorhandler(APIError)
def handle_database_error(error: APIError):
    code = getattr(error, "code", None)
    if code == "23505":
        return jsonify({"error": "Record already exists"}), 409
    if code == "23503":
        return jsonify({"error": "Referenced record does not exist"}), 400
    if code == "22P02":
        return jsonify({"error": "Invalid identifier or value format"}), 400
    if code == "42501":
        return jsonify({"error": "Permission denied"}), 403
    return jsonify({"error": "Database request failed", "details": error.message}), 400


@app.errorhandler(400)
def handle_bad_request(error):
    description = getattr(error, "description", "Invalid request")
    return jsonify({"error": description}), 400


@app.errorhandler(404)
def handle_not_found(error):
    description = getattr(error, "description", "Not found")
    return jsonify({"error": description}), 404


def database() -> Client:
    """Return the shared public Supabase client or fail if unconfigured."""
    if supabase is None:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY in .env")
    return supabase


def scoped_database(access_token: str) -> Client:
    """Create a per-request Supabase client authenticated with the caller's JWT."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY in .env")
    options = ClientOptions(
        headers={"Authorization": f"Bearer {access_token}"},
        auto_refresh_token=False,
        persist_session=False,
    )
    return create_client(SUPABASE_URL, SUPABASE_KEY, options=options)


def extract_bearer_token() -> str | None:
    header = request.headers.get("Authorization", "").strip()
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AuthError("Authorization header must use Bearer token format", 401)
    return parts[1].strip()


def authenticate_request() -> tuple[Any, Client, str]:
    """Validate the Bearer JWT against Supabase Auth and return (user, client, token)."""
    token = extract_bearer_token()
    if not token:
        raise AuthError("Authentication required", 401)

    try:
        user_response = database().auth.get_user(jwt=token)
    except Exception as exc:
        raise AuthError("Invalid or expired authentication token", 401) from exc

    user = getattr(user_response, "user", None)
    if user is None or not getattr(user, "id", None):
        raise AuthError("Invalid or expired authentication token", 401)

    return user, scoped_database(token), token


def require_auth(view):
    """Decorator that enforces Supabase JWT authentication on a Flask route."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        user, client, token = authenticate_request()
        g.current_user = user
        g.db = client
        g.access_token = token
        return view(*args, **kwargs)

    return wrapped


def optional_auth_client() -> tuple[Any | None, Client]:
    """Return authenticated (user, scoped_client) when a Bearer token is supplied."""
    token = extract_bearer_token()
    if not token:
        return None, database()
    user, client, _ = authenticate_request()
    return user, client


def json_object() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")
    return data


def validate_uuid(value: Any, field_name: str = "id") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a valid UUID")
    try:
        return str(UUID(value.strip()))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid UUID") from exc


def parse_iso_timestamp(value: Any, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp string")
    raw = value.strip()
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp") from exc
    return parsed.isoformat()


def validate_event_payload(
    data: dict[str, Any],
    *,
    partial: bool = False,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate event creation or update fields and cross-field constraints."""
    unknown = set(data) - EVENT_WRITABLE_FIELDS
    if unknown:
        raise ValueError(f"Unsupported field(s): {', '.join(sorted(unknown))}")
    if not data:
        raise ValueError("Request body must include at least one field")

    cleaned: dict[str, Any] = {}

    if not partial or "title" in data:
        title = data.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")
        cleaned["title"] = title.strip()[:120]

    if not partial or "capacity" in data:
        capacity = data.get("capacity")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity must be an integer greater than 0")
        cleaned["capacity"] = capacity

    if "status" in data or not partial:
        status = data.get("status", "upcoming")
        if not isinstance(status, str) or status.strip().lower() not in VALID_EVENT_STATUSES:
            allowed = ", ".join(sorted(VALID_EVENT_STATUSES))
            raise ValueError(f"status must be one of: {allowed}")
        cleaned["status"] = status.strip().lower()

    if "category" in data or not partial:
        category = data.get("category", "Other")
        if not isinstance(category, str) or not category.strip():
            raise ValueError("category must be a non-empty string")
        cleaned["category"] = category.strip()[:60]

    if "starts_at" in data:
        cleaned["starts_at"] = parse_iso_timestamp(data.get("starts_at"), "starts_at")
    if "ends_at" in data:
        cleaned["ends_at"] = parse_iso_timestamp(data.get("ends_at"), "ends_at")

    effective_start = cleaned.get("starts_at", existing.get("starts_at") if existing else None)
    effective_end = cleaned.get("ends_at", existing.get("ends_at") if existing else None)
    if effective_start and effective_end:
        start_dt = datetime.fromisoformat(effective_start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(effective_end.replace("Z", "+00:00"))
        if end_dt < start_dt:
            raise ValueError("ends_at must be greater than or equal to starts_at")

    for text_field in ("description", "location", "campus", "club_name"):
        if text_field in data:
            val = data.get(text_field)
            if val is None or val == "":
                cleaned[text_field] = None
            elif isinstance(val, str):
                cleaned[text_field] = val.strip()[:500]
            else:
                raise ValueError(f"{text_field} must be a string")

    if "is_featured" in data:
        if not isinstance(data["is_featured"], bool):
            raise ValueError("is_featured must be a boolean")
        cleaned["is_featured"] = data["is_featured"]

    return cleaned


def ensure_public_user_profile(client: Client, user: Any, role: str = "student") -> dict[str, Any] | None:
    """Ensure the authenticated user has a row in public.users for FK integrity."""
    user_id = str(user.id)
    existing = client.table("users").select("*").eq("id", user_id).limit(1).execute()
    if existing.data:
        return existing.data[0]
    email = getattr(user, "email", None)
    if not email:
        return None
    safe_role = role if role in VALID_USER_ROLES else "student"
    inserted = (
        client.table("users")
        .insert({"id": user_id, "email": email, "role": safe_role})
        .select("*")
        .execute()
    )
    return inserted.data[0] if inserted.data else None


def serialize_auth_user(user: Any, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = getattr(user, "user_metadata", None) or {}
    role = (profile or {}).get("role") or metadata.get("role") or "student"
    return {
        "id": str(user.id),
        "email": getattr(user, "email", None),
        "role": role,
    }


def fetch_event_or_404(client: Client, event_id: str) -> dict[str, Any]:
    valid_id = validate_uuid(event_id, "event_id")
    result = client.table("events").select("*").eq("id", valid_id).limit(1).execute()
    if not result.data:
        raise NotFoundError("Event not found")
    return result.data[0]


def enrich_event_counts(client: Client, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach seats_remaining and normalize registered_count on serialized events."""
    for event in events:
        reg_count = event.get("registered_count")
        if reg_count is None:
            reg_count = 0
            event["registered_count"] = 0
        capacity = event.get("capacity")
        if isinstance(capacity, int):
            event["seats_remaining"] = max(0, capacity - reg_count)
    return events


def write_event_with_schema_fallback(
    client: Client,
    payload: dict[str, Any],
    *,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Write an event and strip unmigrated columns only if the DB schema lacks them."""
    attempt_payload = dict(payload)
    while True:
        try:
            if event_id is None:
                result = client.table("events").insert(attempt_payload).select("*").execute()
            else:
                result = (
                    client.table("events")
                    .update(attempt_payload)
                    .eq("id", event_id)
                    .select("*")
                    .execute()
                )
            return result.data[0] if result.data else {}
        except APIError as err:
            if getattr(err, "code", None) in {"PGRST204", "42703"}:
                removed = False
                for opt_col in EVENT_FALLBACK_STRIPPABLE:
                    if opt_col in attempt_payload and opt_col in str(err.message):
                        attempt_payload.pop(opt_col)
                        removed = True
                if removed and attempt_payload:
                    continue
            raise


@app.get("/")
def index():
    if FRONTEND_INDEX.is_file():
        return send_file(FRONTEND_INDEX)
    return jsonify({
        "error": "Frontend files are missing",
        "expected": str(FRONTEND_INDEX),
    }), 404


@app.get("/api")
def api_index():
    return jsonify({
        "name": "CampusConnect Event Management API",
        "authentication": "supabase-jwt",
        "supabase_configured": supabase is not None,
        "endpoints": {
            "auth": ["/api/auth/signup", "/api/auth/signin", "/api/auth/me"],
            "events": ["/api/events", "/api/events/<id>", "/api/events/<id>/registrations"],
            "registrations": ["/api/registrations", "/api/registrations/<id>", "/api/me/registrations"],
            "me": ["/api/me"],
        },
    })


@app.post("/api/auth/signup")
def auth_signup():
    body = json_object()
    email = body.get("email")
    password = body.get("password")
    role = body.get("role", "student")

    if not isinstance(email, str) or "@" not in email:
        raise ValueError("Valid email is required")
    if not isinstance(password, str) or len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    if not isinstance(role, str) or role.strip().lower() not in VALID_USER_ROLES:
        raise ValueError("role must be either 'student' or 'organizer'")

    normalized_role = role.strip().lower()
    try:
        response = database().auth.sign_up({
            "email": email.strip().lower(),
            "password": password,
            "options": {"data": {"role": normalized_role}},
        })
    except Exception as exc:
        raise ValueError(str(exc)) from exc

    user = getattr(response, "user", None)
    session = getattr(response, "session", None)
    if user is None:
        raise ValueError("Unable to create user account")

    profile = None
    access_token = getattr(session, "access_token", None) if session else None
    if access_token:
        profile = ensure_public_user_profile(scoped_database(access_token), user, role=normalized_role)

    return jsonify({
        "user": serialize_auth_user(user, profile),
        "access_token": access_token,
        "refresh_token": getattr(session, "refresh_token", None) if session else None,
        "email_confirmation_required": session is None,
    }), 201


@app.post("/api/auth/signin")
def auth_signin():
    body = json_object()
    email = body.get("email")
    password = body.get("password")
    if not isinstance(email, str) or not email.strip() or not isinstance(password, str) or not password:
        raise ValueError("email and password are required")

    try:
        response = database().auth.sign_in_with_password({
            "email": email.strip().lower(),
            "password": password,
        })
    except Exception as exc:
        raise AuthError("Invalid email or password", 401) from exc

    user = getattr(response, "user", None)
    session = getattr(response, "session", None)
    access_token = getattr(session, "access_token", None) if session else None
    if not user or not access_token:
        raise AuthError("Invalid email or password", 401)

    profile = ensure_public_user_profile(scoped_database(access_token), user)
    return jsonify({
        "user": serialize_auth_user(user, profile),
        "access_token": access_token,
        "refresh_token": getattr(session, "refresh_token", None),
    })


@app.get("/api/auth/me")
@app.get("/api/me")
@require_auth
def get_me():
    profile = ensure_public_user_profile(g.db, g.current_user)
    return jsonify({"user": serialize_auth_user(g.current_user, profile)})


@app.patch("/api/me")
@require_auth
def update_me():
    body = json_object()
    unknown = set(body) - USER_UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"Unsupported field(s): {', '.join(sorted(unknown))}")
    role = body.get("role")
    if not isinstance(role, str) or role.strip().lower() not in VALID_USER_ROLES:
        raise ValueError("role must be either 'student' or 'organizer'")

    ensure_public_user_profile(g.db, g.current_user, role=role.strip().lower())
    result = (
        g.db.table("users")
        .update({"role": role.strip().lower()})
        .eq("id", str(g.current_user.id))
        .select("*")
        .execute()
    )
    profile = result.data[0] if result.data else None
    return jsonify({"user": serialize_auth_user(g.current_user, profile)})


@app.get("/api/users")
@require_auth
def list_own_user():
    """Return only the authenticated caller's profile to prevent email enumeration."""
    profile = ensure_public_user_profile(g.db, g.current_user)
    return jsonify([profile] if profile else [])


@app.get("/api/events")
def list_events():
    _, client = optional_auth_client()
    search = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "").strip()
    status = (request.args.get("status") or "").strip().lower()

    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except ValueError as exc:
        raise ValueError("limit and offset must be integers") from exc

    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    def run_query(*, with_starts_at_order: bool):
        query = client.table("events").select("*")
        if search:
            query = query.ilike("title", f"%{search}%")
        if category and category.lower() != "all":
            query = query.ilike("category", category)
        if status and status != "all":
            query = query.eq("status", status)
        if with_starts_at_order:
            query = query.order("starts_at", desc=False, nullsfirst=False)
        query = query.order("id", desc=False)
        return query.range(offset, offset + limit - 1).execute()

    try:
        result = run_query(with_starts_at_order=True)
    except APIError as err:
        if getattr(err, "code", None) == "42703" and "starts_at" in str(err.message):
            result = run_query(with_starts_at_order=False)
        else:
            raise

    events = enrich_event_counts(client, list(result.data or []))
    return jsonify(events)


@app.post("/api/events")
@require_auth
def create_event():
    payload = validate_event_payload(json_object())
    ensure_public_user_profile(g.db, g.current_user, role="organizer")
    payload["organizer_id"] = str(g.current_user.id)
    created = write_event_with_schema_fallback(g.db, payload)
    enrich_event_counts(g.db, [created] if created else [])
    return jsonify(created), 201


@app.get("/api/events/<string:event_id>")
def get_event(event_id: str):
    _, client = optional_auth_client()
    event = fetch_event_or_404(client, event_id)
    enrich_event_counts(client, [event])
    return jsonify(event)


@app.put("/api/events/<string:event_id>")
@app.patch("/api/events/<string:event_id>")
@require_auth
def update_event(event_id: str):
    existing = fetch_event_or_404(g.db, event_id)
    if str(existing.get("organizer_id")) != str(g.current_user.id):
        raise AuthError("Only the event organizer can update this event", 403)

    payload = validate_event_payload(
        json_object(),
        partial=request.method == "PATCH",
        existing=existing,
    )
    updated = write_event_with_schema_fallback(g.db, payload, event_id=existing["id"])
    enrich_event_counts(g.db, [updated] if updated else [])
    return jsonify(updated)


@app.delete("/api/events/<string:event_id>")
@require_auth
def delete_event(event_id: str):
    existing = fetch_event_or_404(g.db, event_id)
    if str(existing.get("organizer_id")) != str(g.current_user.id):
        raise AuthError("Only the event organizer can delete this event", 403)

    g.db.table("events").delete().eq("id", existing["id"]).select("*").execute()
    return jsonify({"deleted": True, "id": existing["id"]})


@app.get("/api/events/<string:event_id>/registrations")
@require_auth
def list_event_registrations(event_id: str):
    """Return attendee list for an event (owning organizer only)."""
    event = fetch_event_or_404(g.db, event_id)
    if str(event.get("organizer_id")) != str(g.current_user.id):
        raise AuthError("Only the owning organizer can view event registrations", 403)

    result = (
        g.db.table("registrations")
        .select("*")
        .eq("event_id", event["id"])
        .order("created_at", desc=False)
        .execute()
    )
    return jsonify(result.data or [])


@app.get("/api/me/registrations")
@require_auth
def list_my_registrations():
    """Return the authenticated user's event registrations."""
    user_id = str(g.current_user.id)
    regs_result = (
        g.db.table("registrations")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    registrations = list(regs_result.data or [])
    if not registrations:
        return jsonify([])

    event_ids = sorted({r["event_id"] for r in registrations if r.get("event_id")})
    events_by_id: dict[str, dict[str, Any]] = {}
    if event_ids:
        events_result = g.db.table("events").select("*").in_("id", event_ids).execute()
        for ev in events_result.data or []:
            events_by_id[ev["id"]] = ev

    for reg in registrations:
        reg["event"] = events_by_id.get(reg.get("event_id"))
    return jsonify(registrations)


@app.post("/api/registrations")
@require_auth
def create_registration():
    """Register the authenticated user for an event, returning 201 or 409."""
    body = json_object()
    unknown = set(body) - {"event_id"}
    if unknown:
        raise ValueError(f"Unsupported field(s): {', '.join(sorted(unknown))}")

    event_id = validate_uuid(body.get("event_id"), "event_id")
    user_id = str(g.current_user.id)

    ensure_public_user_profile(g.db, g.current_user)
    event = fetch_event_or_404(g.db, event_id)

    status = str(event.get("status") or "").lower()
    if status in CLOSED_EVENT_STATUSES:
        raise ConflictError(f"Event is {status} and cannot accept registrations")

    duplicate = (
        g.db.table("registrations")
        .select("id")
        .eq("user_id", user_id)
        .eq("event_id", event_id)
        .limit(1)
        .execute()
    )
    if duplicate.data:
        raise ConflictError("You are already registered for this event")

    capacity = event.get("capacity")
    if isinstance(capacity, int):
        current_count = event.get("registered_count")
        if current_count is None:
            count_result = (
                g.db.table("registrations")
                .select("id", count="exact")
                .eq("event_id", event_id)
                .execute()
            )
            current_count = getattr(count_result, "count", None) or len(count_result.data or [])
        if current_count >= capacity:
            raise ConflictError("Event has reached maximum capacity")

    result = (
        g.db.table("registrations")
        .insert({"user_id": user_id, "event_id": event_id})
        .select("*")
        .execute()
    )
    return jsonify(result.data[0] if result.data else {}), 201


@app.delete("/api/registrations/<string:registration_id>")
@require_auth
def delete_registration(registration_id: str):
    valid_id = validate_uuid(registration_id, "registration_id")
    existing = (
        g.db.table("registrations")
        .select("*")
        .eq("id", valid_id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise NotFoundError("Registration not found")

    record = existing.data[0]
    if str(record.get("user_id")) != str(g.current_user.id):
        raise AuthError("You can only cancel your own registration", 403)

    g.db.table("registrations").delete().eq("id", valid_id).select("*").execute()
    return jsonify({"deleted": True, "id": valid_id})


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG") == "1",
    )
