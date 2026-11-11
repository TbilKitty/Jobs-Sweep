// Cloudflare Worker for the Job Sweep Archive.
//  1. Serves the tracker page from the site/ folder.
//  2. /api/applied — "applied" marks shared across devices (KV namespace bound as APPLIED).
//  3. Cron trigger — starts the GitHub job sweep at 8:45am Eastern on Tuesdays and Thursdays.
//  4. /api/run-sweep?key=… — starts a PREVIEW run right now, to test the trigger. Key = the RUN_KEY secret.
//
// Secrets to add in Cloudflare (Worker → Settings → Variables and Secrets):
//   GH_DISPATCH_TOKEN  a GitHub fine-grained token with "Actions: Read and write" on TbilKitty/Jobs-Sweep
//   RUN_KEY            any password you make up, used only for the test link

const KEY = "applied";
const REPO = "TbilKitty/Jobs-Sweep";
const WORKFLOW = "job-sweep.yml";

async function getMarks(env) {
  return (await env.APPLIED.get(KEY, "json")) || {};
}

function easternHour(date = new Date()) {
  return Number(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", hour12: false })
    .format(date)) % 24;
}

async function startSweep(env, source, dryRun = false) {
  if (!env.GH_DISPATCH_TOKEN) return { ok: false, status: 0, text: "GH_DISPATCH_TOKEN secret is missing" };
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
      accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28",
      "user-agent": "job-sweep-cloudflare-trigger",
      "content-type": "application/json",
    },
    body: JSON.stringify({ ref: "main", inputs: { dry_run: dryRun ? "true" : "false", source } }),
  });
  return { ok: r.status === 204, status: r.status, text: r.status === 204 ? "started" : await r.text() };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/api/run-sweep") {
      if (!env.RUN_KEY || url.searchParams.get("key") !== env.RUN_KEY) return new Response("Not found", { status: 404 });
      const result = await startSweep(env, "cloudflare-test", true);
      return new Response(result.ok
        ? "Test run started (preview only: no email is sent and nothing is saved). Check the Actions tab on GitHub in a minute."
        : `Could not start the sweep (GitHub said ${result.status}): ${result.text}`,
        { status: result.ok ? 200 : 502, headers: { "content-type": "text/plain; charset=utf-8" } });
    }

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

  // Cloudflare runs this at 12:45 and 13:45 UTC on Tue/Thu; only the one that is 8:45 in Eastern time starts the sweep.
  async scheduled(event, env, ctx) {
    if (easternHour(new Date(event.scheduledTime)) !== 8) return;
    let result = await startSweep(env, "cloudflare");
    if (!result.ok) {               // one retry after 60 seconds
      await new Promise(r => setTimeout(r, 60000));
      result = await startSweep(env, "cloudflare");
    }
    console.log("Job sweep trigger:", result.status, result.text);
  },
};
