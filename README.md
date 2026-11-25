# CampusConnect

CampusConnect is a Flask + Supabase event hub with a single-file frontend.

## Split Deployment (Backend on Render + Frontend on Vercel)

1. **Backend (Render)**:
   - Deploy this repository on Render using Gunicorn (`gunicorn app:app`).
   - Set environment variables: `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, and optionally `CORS_ALLOWED_ORIGINS` (defaults to `*`).
   - CORS headers (`Authorization`, `Content-Type`) and `OPTIONS` preflight responses are built into `app.py`.

2. **Frontend (Vercel)**:
   - Set Vercel **Root Directory** to `static` (or `frontend/app`) with **Framework Preset: Other** so Vercel serves the static frontend without running Python serverless functions.
   - Set your Render URL in `meta[name="api-base-url"]` in `frontend/app/index.html` (and copy to `static/index.html`), **or** pass `?api=https://your-service.onrender.com`, **or** enter it directly in the UI's **Connect API** box.

## Single-Origin Deployment (Render or Vercel Full-Stack)

- When deployed together on Render or Vercel (root directory containing `app.py` with `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` configured), the frontend calls `/api/...` on the same origin automatically.

## Authentication & API Contract

- Public read endpoints: `GET /api/events` (supports `q`, `category`, `status`, `limit`, `offset`) and `GET /api/events/<id>`.
- Auth endpoints: `POST /api/auth/signup`, `POST /api/auth/signin`, `GET /api/auth/me`.
- Authenticated write & private endpoints (`Authorization: Bearer <access_token>`):
  - `POST /api/events`, `PUT`/`PATCH /api/events/<id>`, `DELETE /api/events/<id>` (organizer only)
  - `POST /api/registrations` (returns `201` or `409` on duplicate/full/closed event)
  - `DELETE /api/registrations/<id>` (owner only)
  - `GET /api/me/registrations`
  - `GET /api/events/<id>/registrations` (owning organizer only)
  - `GET /api/me`, `PATCH /api/me`

## Database Migrations

Apply both SQL migrations in the Supabase SQL Editor in order:
1. `migrations/20261125_event_directory.sql`
2. `migrations/20261125_security_and_directory_v2.sql`

## Frontend Sync

`frontend/app/index.html` is the frontend source, and `static/index.html` is the deployable copy. Keep them in sync before pushing.
