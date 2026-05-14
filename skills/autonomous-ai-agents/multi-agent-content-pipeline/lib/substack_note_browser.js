// Publish a Substack Note via headless Chromium.
//
// Why this exists: as of 2026-05-14 the publication-subdomain
// /api/v1/comment/feed POST is gated by Cloudflare bot-fight when called from
// a datacenter IP. Plain `requests` from the Hetzner VM returns 403 even with
// a valid substack.sid cookie. Driving the call from inside a real Chromium
// page passes the challenge — the page's `fetch` carries the cf_clearance /
// __cf_bm cookies and the request rides the browser's TLS fingerprint, which
// CF allows.
//
// Stdin (JSON):
//   { publicationUrl, sid, paragraphs: string[] }
//
// Stdout (JSON):
//   { status: number, body: object|string }
//
// Nonzero exit means launch/navigation failed before the POST could run.

const { chromium } = require('playwright');

(async () => {
  let raw = '';
  for await (const chunk of process.stdin) raw += chunk;
  const { publicationUrl, sid, paragraphs } = JSON.parse(raw);
  const base = publicationUrl.replace(/\/$/, '');

  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({
      userAgent:
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ' +
        'AppleWebKit/537.36 (KHTML, like Gecko) ' +
        'Chrome/124.0.0.0 Safari/537.36',
      locale: 'en-US',
    });
    await ctx.addCookies([
      {
        name: 'substack.sid',
        value: sid,
        domain: '.substack.com',
        path: '/',
        httpOnly: true,
        secure: true,
        sameSite: 'Lax',
      },
    ]);
    const page = await ctx.newPage();
    await page.goto(`${base}/notes`, {
      waitUntil: 'domcontentloaded',
      timeout: 30000,
    });

    const payload = {
      bodyJson: {
        type: 'doc',
        content: paragraphs.map((p) => ({
          type: 'paragraph',
          content: [{ type: 'text', text: p }],
        })),
      },
      tabId: 'for-you',
      surface: 'feed',
    };

    const result = await page.evaluate(async (p) => {
      const r = await fetch('/api/v1/comment/feed', {
        method: 'POST',
        credentials: 'include',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify(p),
      });
      let body;
      try {
        body = await r.json();
      } catch (e) {
        body = await r.text();
      }
      return { status: r.status, body };
    }, payload);

    process.stdout.write(JSON.stringify(result));
  } finally {
    await browser.close();
  }
})().catch((err) => {
  process.stderr.write((err && err.stack) || String(err));
  process.exit(2);
});
