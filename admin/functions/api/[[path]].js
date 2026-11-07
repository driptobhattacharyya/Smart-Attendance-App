// Cloudflare Pages Function: reverse proxy for https://<site>/api/* -> the AWS Lambda Function URL.
//
// Env (set in Pages, see infra/cloudflare/README.md):
//   API_ORIGIN    e.g. https://abc123.lambda-url.ap-south-1.on.aws   (no path)
//   ORIGIN_SECRET shared secret; sent upstream as X-Origin-Auth, checked by the backend.
//
// The browser/mobile app talk to a single origin, so no CORS is involved.

const HOP_BY_HOP = ["host", "connection", "keep-alive", "transfer-encoding", "upgrade", "te"];

export async function onRequest({ request, env }) {
  if (!env.API_ORIGIN || !env.ORIGIN_SECRET) {
    return json({ detail: "Proxy is not configured (API_ORIGIN / ORIGIN_SECRET missing)" }, 500);
  }

  const incoming = new URL(request.url);
  let target;
  try {
    target = new URL(incoming.pathname + incoming.search, env.API_ORIGIN);
  } catch {
    return json({ detail: "API_ORIGIN is not a valid URL" }, 500);
  }

  const headers = new Headers(request.headers);
  for (const name of HOP_BY_HOP) headers.delete(name);
  // Never trust client-supplied values for these; set them ourselves.
  headers.delete("x-origin-auth");
  headers.set("X-Origin-Auth", env.ORIGIN_SECRET);
  headers.set("X-Forwarded-Host", incoming.host);
  headers.set("X-Forwarded-Proto", incoming.protocol.replace(":", ""));
  const clientIp = request.headers.get("cf-connecting-ip");
  if (clientIp) headers.set("X-Forwarded-For", clientIp);
  else headers.delete("x-forwarded-for");

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    // Stream the request body through (uploads such as face photos) without buffering it.
    body: hasBody ? request.body : undefined,
    duplex: hasBody ? "half" : undefined,
    redirect: "manual", // hand redirects to the client untouched
    cf: { cacheEverything: false, cacheTtl: 0 },
  }).catch((err) => err);

  if (upstream instanceof Error) {
    return json({ detail: "Upstream API unreachable" }, 502);
  }

  const out = new Response(upstream.body, upstream);
  out.headers.set("Cache-Control", upstream.headers.get("Cache-Control") ?? "no-store");
  return out;
}

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}
