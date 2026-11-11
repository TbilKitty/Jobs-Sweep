// Cloudflare Pages Function: shared "applied" marks for the Job Sweep archive.
// Needs a KV namespace bound to this Pages project with the variable name APPLIED.
// Stored as one JSON object: { "<job url>": "<ISO date applied>" | null }  (null = un-marked)

const KEY = "applied";

export async function onRequestGet({ env }) {
  if (!env.APPLIED) return new Response("KV not bound", { status: 501 });
  const data = (await env.APPLIED.get(KEY, "json")) || {};
  return Response.json(data, { headers: { "cache-control": "no-store" } });
}

export async function onRequestPost({ env, request }) {
  if (!env.APPLIED) return new Response("KV not bound", { status: 501 });
  let body;
  try { body = await request.json(); } catch { return new Response("Bad JSON", { status: 400 }); }
  const { url, at } = body || {};
  if (typeof url !== "string" || !/^https?:\/\//.test(url) || url.length > 2000) {
    return new Response("Bad url", { status: 400 });
  }
  if (at !== null && (typeof at !== "string" || isNaN(Date.parse(at)))) {
    return new Response("Bad date", { status: 400 });
  }
  const data = (await env.APPLIED.get(KEY, "json")) || {};
  data[url] = at;
  await env.APPLIED.put(KEY, JSON.stringify(data));
  return Response.json({ ok: true });
}
