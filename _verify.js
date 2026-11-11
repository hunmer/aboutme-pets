// Headless-CDP functional verification for index.html (desktop + mobile layouts + pixel hit-test pet)
const { spawn } = require("child_process");
const { mkdtempSync } = require("fs");
const { tmpdir } = require("os");
const path = require("path");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const PORT = 9223;
const URL = "http://127.0.0.1:8123/index.html";

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
  const userDataDir = mkdtempSync(path.join(tmpdir(), "dsh-pets-verify-"));
  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--autoplay-policy=no-user-gesture-required",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${userDataDir}`, "about:blank",
  ], { stdio: "ignore" });

  const results = [];
  const pageErrors = [];
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
    const setViewport = (width, height, mobile) =>
      send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });

    await send("Runtime.enable");
    await send("Page.enable");
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.method === "Runtime.exceptionThrown") {
        const d = msg.params.exceptionDetails;
        pageErrors.push((d.exception && d.exception.description) || d.text || "unknown");
      }
    });

    await setViewport(1280, 800, false);
    const expectedPets = await fetch(URL.replace(/index\.html$/, "preview.json"))
      .then((r) => r.json()).then((j) => j.pets.length);
    await send("Page.navigate", { url: URL });

    let petsCount = 0;
    for (let i = 0; i < 80; i++) {
      await sleep(250);
      petsCount = await evaluate("document.querySelectorAll('#petList .pet').length");
      if (petsCount >= expectedPets) break;
    }
    ok("desktop: all pet buttons rendered", petsCount === expectedPets, `${petsCount}/${expectedPets}`);
    const avatars = await evaluate("document.querySelectorAll('#petList .pet img.avatar').length");
    ok("desktop: avatars rendered in pet list", avatars === expectedPets, `${avatars}/${expectedPets}`);
    const wrap = await evaluate("getComputedStyle(document.getElementById('petList')).flexWrap");
    ok("desktop: pet list single-line (nowrap)", wrap === "nowrap", wrap);
    const ovx = await evaluate("getComputedStyle(document.getElementById('petList')).overflowX");
    ok("desktop: pet list horizontally scrollable", ovx === "auto", ovx);
    const stageD = await evaluate("getComputedStyle(document.querySelector('.stage')).display");
    ok("desktop: stage visible", stageD !== "none", stageD);
    const mpetD = await evaluate("getComputedStyle(document.getElementById('mobilePet')).display");
    ok("desktop: floating pet hidden", mpetD === "none", mpetD);

    // ---- audio/animation sync (audio-driven clock) ----
    const audioAction = await fetch(URL.replace(/index\.html$/, "preview.json"))
      .then((r) => r.json())
      .then((j) => {
        for (let p = 0; p < j.pets.length; p++) {
          for (const cat of j.pets[p].categories) {
            const a = cat.actions.find((x) => x.audio);
            if (a) return { petIndex: p, actionId: a.id };
          }
        }
        return null;
      });
    if (audioAction) {
      await evaluate(`document.querySelectorAll('#petList .pet')[${audioAction.petIndex}].click()`);
      await sleep(700);
      await evaluate(`(() => { const chip = [...document.querySelectorAll('.chip')].find((c) => c.textContent === ${JSON.stringify(audioAction.actionId)}); if (chip) chip.click(); })()`);
      let started = false;
      for (let i = 0; i < 40 && !started; i++) {
        await sleep(150);
        const dbg = await evaluate("window.__petDebug && window.__petDebug()");
        started = dbg && dbg.playing && dbg.t !== null && dbg.t > 0;
      }
      ok("sync: audio action started playing with sound", started, JSON.stringify(audioAction));

      // while playing, frame must always equal floor(t*fps) % frameCount (within 1 frame)
      let maxSkew = 0, badSample = null;
      for (let i = 0; i < 8; i++) {
        const dbg = await evaluate("window.__petDebug()");
        if (dbg && dbg.t !== null) {
          const expected = Math.floor(dbg.t * dbg.fps) % dbg.frameCount;
          const skew = Math.min(Math.abs(dbg.frame - expected), Math.abs(dbg.frame - expected + dbg.frameCount), Math.abs(dbg.frame - expected - dbg.frameCount));
          if (skew > maxSkew) { maxSkew = skew; badSample = JSON.stringify(dbg); }
        }
        await sleep(220);
      }
      ok("sync: frame follows audio clock while playing", maxSkew <= 1, `maxSkew=${maxSkew} worst=${badSample}`);

      // pause must freeze BOTH animation and audio
      await evaluate("document.getElementById('playBtn').click()");
      await sleep(150);
      const p1 = await evaluate("window.__petDebug()");
      await sleep(450);
      const p2 = await evaluate("window.__petDebug()");
      ok("sync: pause freezes audio+animation together",
        p1 && p2 && p1.playing === false && Math.abs(p2.t - p1.t) < 0.02 && p2.frame === p1.frame,
        `p1=${JSON.stringify(p1)} p2=${JSON.stringify(p2)}`);

      // scrubbing must seek audio to frame/fps
      await evaluate(`(() => { const s = document.getElementById('scrub'); s.value = 5; s.dispatchEvent(new Event('input', { bubbles: true })); })()`);
      await sleep(250);
      const p3 = await evaluate("window.__petDebug()");
      ok("sync: scrub seeks audio and frame together",
        p3 && p3.frame === 5 && p3.t !== null && Math.abs(p3.t - 5 / p3.fps) < 0.15,
        JSON.stringify(p3));

      await evaluate("document.getElementById('playBtn').click()"); // resume for later checks
      await sleep(300);
    } else {
      ok("sync: audio action started playing with sound", false, "no action with audio found in preview.json");
    }

    // ---- mobile 390x844 ----
    await setViewport(390, 844, true);
    await sleep(600);

    ok("mobile: stage hidden",
      (await evaluate("getComputedStyle(document.querySelector('.stage')).display")) === "none");
    ok("mobile: floating pet visible",
      (await evaluate("getComputedStyle(document.getElementById('mobilePet')).display")) === "block");

    const rect = await evaluate("(() => { const r = document.getElementById('mobilePet').getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.left), y: Math.round(r.top) }; })()");
    ok("mobile: floating pet size 150x260", rect && rect.w === 150 && rect.h === 260, JSON.stringify(rect));

    const canvasSize = await evaluate("document.getElementById('mobilePetCanvas').width + 'x' + document.getElementById('mobilePetCanvas').height");
    ok("mobile: pet canvas backing size matches", canvasSize === "150x260", canvasSize);

    const painted = await evaluate("(() => { const c = document.getElementById('mobilePetCanvas'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) return true; return false; })()");
    ok("mobile: pet canvas has visible pixels", painted === true, painted);

    // size stability: the character's opaque region is scaled per frame, so EVERY frame
    // (playing or paused) must fill the constrained dimension about equally —
    // guards against the "pet enlarges while playing" regression.
    const fill = await evaluate(`new Promise((resolve) => {
      const c = document.getElementById('mobilePetCanvas');
      let best = null, worst = null;
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
          const bw = maxX - minX + 1, bh = maxY - minY + 1;
          const f = Math.max(bw / c.width, bh / c.height);
          const rec = { bw, bh, fill: +f.toFixed(3) };
          if (!best || f > best.fill) best = rec;
          if (!worst || f < worst.fill) worst = rec;
        }
      };
      const t0 = performance.now();
      const iv = setInterval(() => { sample(); if (performance.now() - t0 > 1600) { clearInterval(iv); resolve({ best, worst }); } }, 80);
    })`, true);
    ok("mobile: pet body fills constrained dimension every frame",
      fill && fill.best && fill.worst && fill.best.fill >= 0.9 && fill.worst.fill >= 0.85,
      JSON.stringify(fill));

    // locate one opaque and one transparent sprite pixel (fresh each call, element may have moved)
    const findPoints = () => evaluate(`(() => {
      const el = document.getElementById('mobilePet');
      const c = document.getElementById('mobilePetCanvas');
      const r = el.getBoundingClientRect();
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      let opaque = null, clear = null;
      for (let y = 0; y < c.height && !(opaque && clear); y += 2) {
        for (let x = 0; x < c.width && !(opaque && clear); x += 2) {
          const a = d[(y * c.width + x) * 4 + 3];
          if (a > 25 && !opaque) opaque = { x, y };
          else if (a === 0 && !clear) clear = { x, y };
        }
      }
      const toClient = (p) => p && { cx: r.left + (p.x + .5) / c.width * r.width, cy: r.top + (p.y + .5) / c.height * r.height };
      return { opaque: toClient(opaque), clear: toClient(clear) };
    })()`);
    const fire = `const fire = (target, type, x, y, pid) => target.dispatchEvent(new PointerEvent(type, {
      bubbles: true, cancelable: true, pointerId: pid, isPrimary: true, pointerType: 'touch',
      clientX: x, clientY: y, buttons: 1,
    }));`;

    // --- tap on OPAQUE sprite pixel -> replay, no move ---
    await sleep(1200); // let animation advance mid-way
    let pts = await findPoints();
    ok("fixture: found opaque+transparent sprite pixels", pts && pts.opaque && pts.clear, JSON.stringify(pts));
    if (pts && pts.opaque) {
      const t = await evaluate(`(() => {
        ${fire}
        const el = document.getElementById('mobilePet');
        const under = document.elementFromPoint(${pts.opaque.cx}, ${pts.opaque.cy}) || document;
        const before = el.getBoundingClientRect();
        fire(under, 'pointerdown', ${pts.opaque.cx}, ${pts.opaque.cy}, 11);
        fire(under, 'pointerup', ${pts.opaque.cx}, ${pts.opaque.cy}, 11);
        const after = el.getBoundingClientRect();
        return { moved: Math.round(after.left) === Math.round(before.left) && Math.round(after.top) === Math.round(before.top), btn: document.getElementById('playBtn').textContent };
      })()`);
      ok("mobile: tap on sprite body replays (playing, no move)", t && t.moved && t.btn === "暂停", JSON.stringify(t));
    }

    // --- drag from OPAQUE pixel -> moves element ---
    pts = await findPoints();
    if (pts && pts.opaque) {
      const d = await evaluate(`(() => {
        ${fire}
        const el = document.getElementById('mobilePet');
        const before = el.getBoundingClientRect();
        const under = document.elementFromPoint(${pts.opaque.cx}, ${pts.opaque.cy}) || document;
        fire(under, 'pointerdown', ${pts.opaque.cx}, ${pts.opaque.cy}, 12);
        fire(under, 'pointermove', ${pts.opaque.cx} - 20, ${pts.opaque.cy} - 40, 12);
        fire(under, 'pointermove', ${pts.opaque.cx} - 40, ${pts.opaque.cy} - 80, 12);
        fire(under, 'pointerup', ${pts.opaque.cx} - 40, ${pts.opaque.cy} - 80, 12);
        const after = el.getBoundingClientRect();
        return { dx: Math.round(after.left - before.left), dy: Math.round(after.top - before.top) };
      })()`);
      ok("mobile: drag from sprite body moves (-40,-80)", d && d.dx === -40 && d.dy === -80, JSON.stringify(d));
    }

    // --- TRANSPARENT pixel -> full pass-through ---
    pts = await findPoints();
    if (pts && pts.clear) {
      const p = await evaluate(`(() => {
        ${fire}
        const el = document.getElementById('mobilePet');
        const under = document.elementFromPoint(${pts.clear.cx}, ${pts.clear.cy});
        let received = false;
        if (under) under.addEventListener('pointerdown', () => { received = true; }, { once: true });
        const before = el.getBoundingClientRect();
        fire(under || document, 'pointerdown', ${pts.clear.cx}, ${pts.clear.cy}, 13);
        fire(under || document, 'pointermove', ${pts.clear.cx} - 30, ${pts.clear.cy} - 30, 13);
        fire(under || document, 'pointerup', ${pts.clear.cx} - 30, ${pts.clear.cy} - 30, 13);
        const after = el.getBoundingClientRect();
        return { received, target: under ? under.tagName + '.' + under.className : 'none', unmoved: Math.round(after.left) === Math.round(before.left) && Math.round(after.top) === Math.round(before.top) };
      })()`);
      ok("mobile: transparent area passes events through", p && p.received === true && p.unmoved === true, JSON.stringify(p));
    }

    // elementFromPoint never returns the pet itself (pointer-events:none)
    const hitOwn = await evaluate(`(() => {
      const el = document.getElementById('mobilePet');
      const r = el.getBoundingClientRect();
      return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === el;
    })()`);
    ok("mobile: pet itself never hit-tests (always underneath)", hitOwn === false, hitOwn);

    // clamp far drag inside viewport
    const clamp = await evaluate(`(() => {
      ${fire}
      const el = document.getElementById('mobilePet');
      const c = document.getElementById('mobilePetCanvas');
      const r = el.getBoundingClientRect();
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      let opaque = null;
      for (let y = 0; y < c.height && !opaque; y += 2) for (let x = 0; x < c.width && !opaque; x += 2) if (d[(y * c.width + x) * 4 + 3] > 25) opaque = { x, y };
      const cx = r.left + (opaque.x + .5) / c.width * r.width, cy = r.top + (opaque.y + .5) / c.height * r.height;
      const under = document.elementFromPoint(cx, cy) || document;
      fire(under, 'pointerdown', cx, cy, 14);
      fire(under, 'pointermove', cx - 3000, cy - 3000, 14);
      fire(under, 'pointerup', cx - 3000, cy - 3000, 14);
      const a = el.getBoundingClientRect();
      return { left: Math.round(a.left), top: Math.round(a.top) };
    })()`);
    ok("mobile: drag clamped inside viewport", clamp && clamp.left === 0 && clamp.top === 0, JSON.stringify(clamp));

    await sleep(300);
    ok("mobile: no uncaught page exceptions", pageErrors.length === 0, pageErrors.join(" | ").slice(0, 300));

    const mWrap = await evaluate("getComputedStyle(document.getElementById('petList')).flexWrap");
    ok("mobile: pet list single-line (nowrap)", mWrap === "nowrap", mWrap);
    const mOverflow = await evaluate("(n => n.scrollWidth - n.clientWidth)(document.getElementById('petList'))");
    ok("mobile: list overflows -> touch-scroll needed", Number(mOverflow) > 0, mOverflow);
  } finally {
    chrome.kill();
  }

  console.log(JSON.stringify(results, null, 2));
  const failed = results.filter((r) => !r.pass);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((err) => { console.error("VERIFY SCRIPT ERROR:", err); process.exit(2); });
