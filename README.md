# Deploying on Vercel

This repository is a Flask application. Vercel detects the top-level `app` in
`app.py` automatically; no custom build command or `vercel.json` is needed.

## Deploy

1. Import this repository into Vercel and use the repository root as the
   project root (the directory containing `app.py`).
2. Add `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` under Project Settings >
   Environment Variables for the environments you plan to deploy.
3. Deploy. The root URL serves the bundled CampusConnect page, and the API is
   available under `/api`.

The local `.env` file is excluded from deployment. Do not commit it; configure
production values in Vercel instead. The API currently has authentication
disabled, so its database operations are publicly callable. Apply suitable
Supabase row-level security policies before exposing production data.

## Frontend file

`static/index.html` is the deployable copy of `../frontend/app/index.html`. The
Flask app uses the sibling frontend file when running from the full local
project and falls back to this bundled copy on Vercel. Copy frontend changes to
`static/index.html` before deploying.
