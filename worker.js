// Cloudflare Worker for the Job Sweep Archive.
// Serves the page from the site/ folder, plus /api/applied for "applied" marks shared across devices.
// Shared marks need a KV namespace bound as APPLIED (see wrangler.jsonc). Without it, marks save per browser.

const KEY = "applied";

async function getMarks(env) {
  return (await env.APPLIED.get(KEY, "json")) || {};
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/api/applied") return env.ASSETS.fetch(request);
    if (!env.APPLIED) return new Response("KV not bound", { status: 501 });

    if (request.method === "GET") {
      return Response.json(await getMarks(env), { headers: { "cache-control": "no-store" } });
    }
    if (request.method === "POST") {
      let body;
      try { body = await request.json(); } catch { return new Response("Bad JSON", { status: 400 }); }
      const { url: jobUrl, at } = body || {};
      if (typeof jobUrl !== "string" || !/^https?:\/\//.test(jobUrl) || jobUrl.length > 2000) {
        return new Response("Bad url", { status: 400 });
      }
      if (at !== null && (typeof at !== "string" || isNaN(Date.parse(at)))) {
        return new Response("Bad date", { status: 400 });
      }
      const marks = await getMarks(env);
      marks[jobUrl] = at; // null = un-marked
      await env.APPLIED.put(KEY, JSON.stringify(marks));
      return Response.json({ ok: true });
    }
    return new Response("Method not allowed", { status: 405 });
  },
};
