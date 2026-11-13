# Cloudflare: admin panel + `/api` proxy

One Cloudflare Pages project, `smartattendance`, serves:

| URL | What |
|---|---|
| `https://smartattendance.driptobhattacharyya.com/` | Admin SPA (built from `admin/`, `VITE_API_URL=/api`) |
| `/api/*` | Pages Function `admin/functions/api/[[path]].js`, a reverse proxy to the Lambda Function URL |
| `/download` | Static page linking to the latest Android APK (`admin/public/download/`) |

The mobile app and the admin panel both call `https://smartattendance.driptobhattacharyya.com/api`, so there is a single origin and no CORS. The proxy sends the secret header `X-Origin-Auth`, which the backend requires on everything except `/api/health`, so the public Lambda URL is useless to anyone who finds it.

Why not `api.smartattendance.driptobhattacharyya.com`? Cloudflare's free Universal SSL certificate covers `driptobhattacharyya.com` and `*.driptobhattacharyya.com` only, one subdomain level. A second level (`api.smartattendance.…`) would need Advanced Certificate Manager (paid). Same-origin `/api` avoids it.

## 1. Create the Pages project

Dashboard: Workers & Pages, Create, Pages, **Direct Upload**, name it exactly `smartattendance` and upload any placeholder file (the GitHub Action replaces it). Or from a terminal (needs `npx wrangler login`):

```bash
cd admin
npm ci
VITE_API_URL=/api npm run build
npx wrangler pages project create smartattendance --production-branch main
npx wrangler pages deploy dist --project-name smartattendance --branch main
```

Wrangler picks up `admin/functions/` automatically when run from `admin/`, and `dist/_routes.json` limits Function invocations to `/api/*` (static files never count against the Functions quota).

## 2. Set the proxy variables

You need the Function URL from the AWS stack output (see `infra/aws/README.md`) and the same `ORIGIN_SECRET` that was given to `sam deploy`.

```bash
cd admin
npx wrangler pages secret put API_ORIGIN    --project-name smartattendance   # https://xxxx.lambda-url.ap-south-1.on.aws
npx wrangler pages secret put ORIGIN_SECRET --project-name smartattendance   # same value as the AWS OriginSecret
```

Or in the dashboard: the project, Settings, Variables and Secrets, add both as **Secret** for Production (and Preview if you want preview deployments to work). Variables take effect on the next deployment, so redeploy after setting them (`npx wrangler pages deploy dist --project-name smartattendance --branch main`, or re-run the GitHub Action).

`API_ORIGIN` must be the bare origin (no `/api` suffix, trailing slash optional); the function appends the incoming path `/api/...` itself.

## 3. Custom domain and DNS

The zone `driptobhattacharyya.com` is already on Cloudflare.

1. Pages project, Custom domains, Set up a custom domain, enter `smartattendance.driptobhattacharyya.com`.
2. Because the zone is on Cloudflare, accept the offered proxied `CNAME smartattendance -> smartattendance.pages.dev` record. Wait until the domain shows Active and the certificate is issued (a few minutes).
3. SSL/TLS mode for the zone: **Full (strict)** (Pages is fine with it).

## 4. Caching rules for `/api`

The function already returns `Cache-Control: no-store` unless the backend sets its own, and `_headers` repeats it. For belt and braces, add a Cache Rule (zone, Caching, Cache Rules): if URI Path starts with `/api/` then **Bypass cache**. Do not enable Rocket Loader, Auto Minify or "Always Online" for this hostname.

## 5. Verify

```bash
curl -s https://smartattendance.driptobhattacharyya.com/api/health      # {"status":"ok",...}
curl -s -o /dev/null -w "%{http_code}\n" https://smartattendance.driptobhattacharyya.com/   # 200 (SPA)
curl -s -o /dev/null -w "%{http_code}\n" https://smartattendance.driptobhattacharyya.com/download/   # 200
```

If `/api/health` returns `{"detail":"Proxy is not configured ..."}` the two secrets are missing or the project was not redeployed after setting them. A `403` JSON `{"detail":"Forbidden"}` means `ORIGIN_SECRET` differs from the one in AWS. A `502` means `API_ORIGIN` is wrong or Lambda is failing (check the CloudWatch log group `/aws/lambda/attendify-api-api`).

## Limits to know

- Pages Functions (Workers) free plan: 100,000 requests/day, 10 ms CPU per request (a pass-through proxy uses well under 1 ms). The paid Workers plan (5 USD/month) lifts this; a small company will not need it.
- Request bodies pass through unbuffered. Cloudflare limits request bodies to 100 MB on the free plan; the API caps uploads at 5 MB anyway.
- Lambda's 30 s timeout is the real upper limit for any API call.

## Publishing the Android APK

`/download` shows a button for whatever URL is in `admin/public/download/latest.json` (`url`, optional `version` and `updated`). See `docs/deploy.md` for the recommended flow (attach the APK to a GitHub Release, put that URL in the JSON, deploy the admin site).
