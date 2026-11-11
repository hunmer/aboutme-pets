// Live-site spot check: https://hunmer.github.io/aboutme-pets/ (gh-pages flavor,
// assets from raw.githubusercontent.com). Asserts the fixed-layout mobile pet works end to end.
const { spawn } = require("child_process");
const { mkdtempSync } = require("fs");
const { tmpdir } = require("os");
const path = require("path");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const PORT = 9224;
const URL = "https://hunmer.github.io/aboutme-pets/";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForJson(retries = 50) {
  for (let i = 0; i < retries; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json`);
      return await res.json();
    } catch (_) { await sleep(200); }
  }
  throw new Error("chrome devtools endpoint never came up");
}

async function main() {
  const userDataDir = mkdtempSync(path.join(tmpdir(), "dsh-pets-live-"));
  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--autoplay-policy=no-user-gesture-required",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${userDataDir}`, "about:blank",
  ], { stdio: "ignore" });

  const results = [];
  const ok = (name, pass, detail = "") => results.push({ name, pass, detail: String(detail) });

  try {
    const targets = await waitForJson();
    const page = targets.find((t) => t.type === "page");
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

    let seq = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    };
    const send = (method, params = {}) => new Promise((res) => {
      const id = ++seq;
      pending.set(id, res);
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evaluate = async (expression, awaitPromise = false) => {
      const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise });
      if (r.result && r.result.exceptionDetails) return { __ex: r.result.exceptionDetails.text };
      return r.result ? r.result.result.value : undefined;
    };

    await send("Runtime.enable");
    await send("Page.enable");
    await send("Page.navigate", { url: URL });
    await sleep(4000); // page + preview.json + first spritesheet from raw.githubusercontent

    await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await sleep(1200);

    const pets = await evaluate("document.querySelectorAll('.pet').length");
    ok("live: pets list rendered", pets >= 7, pets);

    const rect = await evaluate("(() => { const r = document.getElementById('mobilePet').getBoundingClientRect(); return r.width + 'x' + r.height; })()");
    ok("live: mobile pet 150x260", rect === "150x260", rect);

    const painted = await evaluate(`(() => {
      const c = document.getElementById('mobilePetCanvas');
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; // throws if CORS broke
      for (let i = 3; i < d.length; i += 40) if (d[i] > 25) return true;
      return false;
    })()`);
    ok("live: canvas painted (CORS pixel reads OK)", painted === true, painted);

    const jitter = await evaluate(`new Promise((resolve) => {
      const c = document.getElementById('mobilePetCanvas');
      let prev = null; const dc = [];
      const sample = () => {
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        let minX = 1e9, minY = 1e9, maxX = -1, maxY = -1;
        for (let y = 0; y < c.height; y += 2) for (let x = 0; x < c.width; x += 2) {
          if (d[(y * c.width + x) * 4 + 3] > 25) {
            if (x < minX) minX = x; if (x > maxX) maxX = x;
            if (y < minY) minY = y; if (y > maxY) maxY = y;
          }
        }
        if (maxX >= minX) {
          const cx = (minX + maxX) / 2 / c.width, cy = (minY + maxY) / 2 / c.height;
          if (prev) dc.push([Math.abs(cx - prev[0]), Math.abs(cy - prev[1])]);
          prev = [cx, cy];
        }
      };
      const t0 = performance.now();
      const iv = setInterval(() => { sample(); if (performance.now() - t0 > 1000) { clearInterval(iv); resolve({ dc }); } }, 80);
    })`, true);
    const med = (i) => {
      const v = jitter.dc.map((d) => d[i]).sort((a, b) => a - b);
      return v.length ? v[v.length >> 1] : 1;
    };
    ok("live: fixed layout, no jitter", jitter.dc.length > 0 && med(0) <= 0.05 && med(1) <= 0.05,
      JSON.stringify({ medDx: +med(0).toFixed(4), medDy: +med(1).toFixed(4), n: jitter.dc.length }));

    ws.close();
  } finally {
    chrome.kill();
  }

  console.log(JSON.stringify(results, null, 2));
  const failed = results.filter((r) => !r.pass);
  console.log(`\n${results.length - failed.length}/${results.length} live checks passed`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
