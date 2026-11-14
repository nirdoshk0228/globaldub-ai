# Deploying GlobalDub AI (free tiers)

You'll end up with a website on Vercel calling an API on Render, with Postgres on Neon, a Redis queue on Upstash and
audio files on Cloudflare R2. Do the steps in order; each one gives you a value the next one needs.

Keep every key and password in the dashboards' environment-variable settings. Never put them in code or commit them.

## 0. Test locally first

Run `python scripts/run_local.py ../samples/virasat_ep01.json --langs en-US,ta-IN` with your real keys in
`backend/.env`. Fix anything that fails here before deploying; it's much faster than debugging on a server.

## 1. GitHub

Create a repo named `globaldub-ai`, then push:

```bash
git init && git add . && git commit -m "GlobalDub AI"
git branch -M main
git remote add origin https://github.com/nirdoshk0228/globaldub-ai.git
git push -u origin main
```

Check that no `.env` file appears on GitHub (`.gitignore` excludes it).

## 2. Neon (Postgres)

1. Create a project at neon.tech.
2. Copy the connection string → this is `DATABASE_URL` (it looks like `postgresql://...neon.tech/neondb?sslmode=require`).

Tables are created automatically on first start.

## 3. Upstash (Redis queue)

1. Create a Redis database at upstash.com (pick the region closest to your Render region).
2. Copy the `rediss://...` URL → this is `REDIS_URL`.

## 4. Cloudflare R2 (audio storage)

1. In the Cloudflare dashboard, open R2 and create a bucket `globaldub-audio` → `S3_BUCKET`.
2. Under "Manage R2 API tokens", create a token with Object Read & Write on that bucket. You get:
   - Access Key ID → `S3_ACCESS_KEY_ID`
   - Secret Access Key → `S3_SECRET_ACCESS_KEY`
   - the S3 endpoint `https://<account_id>.r2.cloudflarestorage.com` → `S3_ENDPOINT`

The bucket stays private. The API hands the browser short-lived signed URLs.

## 5. Render (API + worker in one container)

1. New → Web Service → connect the GitHub repo.
2. Runtime: **Docker**. Root directory: leave empty. Dockerfile path: `backend/Dockerfile`.
3. Instance: Free.
4. Environment variables: everything from `.env.example`, filled in:
   - `GD_LLM=gemini`, `GD_TTS=edge`, `GD_TTS_FALLBACK=gemini`, `GD_ASR=groq`
   - `GEMINI_API_KEY`, `GROQ_API_KEY`
   - `DATABASE_URL`, `REDIS_URL`, `S3_BUCKET`, `S3_ENDPOINT`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`
   - `ADMIN_TOKEN` = a long random string (`python -c "import secrets;print(secrets.token_urlsafe(32))"`)
   - `IP_SALT` = another random string
   - `DAILY_BUDGET_USD=1`, `JOBS_PER_IP_PER_DAY=3`
   - `CORS_ORIGINS` = your Vercel URL (fill it in after step 6, then redeploy)
5. Deploy. Open `https://<your-service>.onrender.com/api/health`; it should return `"ok": true`.

Because `REDIS_URL` is set, `start.sh` starts the RQ worker next to the API in the same container.

Free Render services sleep after inactivity, and the first request then takes about a minute. The demo gallery
is pre-rendered, so visitors still see results right away.

## 6. Vercel (website)

1. New Project → import the repo → **Root Directory: `frontend`**.
2. Environment variable: `NEXT_PUBLIC_API_URL=https://<your-service>.onrender.com`
3. Deploy, then copy the URL (for example `https://globaldub.vercel.app`).
4. Go back to Render, set `CORS_ORIGINS` to that URL, and redeploy.

## 7. Publish the demo episode

From your laptop:

```bash
cd backend
python scripts/seed_demo.py --api https://<your-service>.onrender.com --admin-token <ADMIN_TOKEN> \
  --langs en-US,ta-IN,te-IN,bn-IN,mr-IN,ar-SA,pt-BR,es-MX
```

This dubs the sample episode into all eight languages and puts it in the gallery. Then open the website and
listen to every language before you share the link.

## 8. Benchmark numbers on the site

```bash
cd backend
python eval/planted_errors.py --langs en-US              # rules only
python eval/planted_errors.py --langs en-US --judge      # rules + Gemini judge
git add eval/results && git commit -m "benchmark results" && git push
```

Render redeploys and the website's Benchmark page shows the new numbers.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Website says it can't reach the server | Render is waking up (wait a minute), or `NEXT_PUBLIC_API_URL` is wrong. |
| Browser console shows a CORS error | `CORS_ORIGINS` on Render must exactly match the Vercel URL, with no trailing slash. |
| A language fails with a TTS error | Edge-TTS can be rate-limited from cloud IPs; `GD_TTS_FALLBACK=gemini` takes over. Check the Render logs. |
| Gemini 404 for the model | Model names change. Set `GEMINI_MODEL` / `GEMINI_TTS_MODEL` to a model listed in Google AI Studio. |
| Jobs stay "queued" | `REDIS_URL` is set but the worker isn't running. Check the Render logs for `worker` lines. |
