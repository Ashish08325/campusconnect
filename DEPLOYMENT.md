# CampusConnect – Deployment Guide

## Prerequisites
- **Render backend** is already deployed and accessible at `https://campusconnect-3-xiqo.onrender.com`.
- **Supabase CLI** installed (`npm i -g supabase`).
- Supabase project linked locally (`supabase link`).

## Steps

1. **Apply database migrations**
   ```bash
   ./apply_migrations.sh
   ```
   The script runs the two migration files located in `backend/migrations/` in the correct order.

2. **Verify the API**
   ```bash
   curl -i https://campusconnect-3-xiqo.onrender.com/api/events
   ```
   You should see `200 OK` with an empty JSON array (`[]`) and the header `Access-Control-Allow-Origin: *`.

3. **Deploy the frontend to Vercel**
   - The repo now contains a `vercel.json` that tells Vercel to serve the static files from the `static/` folder.
   - Push the changes to GitHub (step 4). Vercel will automatically pick up the new commit and trigger a deployment.

4. **Commit & push**
   ```bash
   git add vercel.json DEPLOYMENT.md
   git commit -m "Add Vercel config and deployment guide"
   git push origin master
   ```

5. **Test the live site**
   - Open the Vercel URL (e.g. `https://<your‑project>.vercel.app`).
   - The page should display a green “API Live” status pill, indicating it successfully read the `<meta name="api-base-url">` tag.
   - Test sign‑up, sign‑in, event creation, registration, etc. No CORS errors should appear in the browser console.

## Re‑running migrations
If you later add new columns or constraints, simply run `./apply_migrations.sh` again. The UI will automatically reflect the updated schema on the next page load.
