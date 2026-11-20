// The replay of a published conversation (tools/site/export.py): the page doing live what it did. Each step is the
// events the page's server sent at one moment of the conversation, given to the page's own handler (SITE.emit), so
// what plays is the page itself: its work line, its panels merging into place, its reply arriving. This file holds
// the pace, the bar at the foot of the window while it plays, and the line above the page that says what the page
// is and starts it; it draws nothing of the conversation.
(() => {
  "use strict";
  const SITE = window.SITE;
  const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const PLAY = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 4.5v11l9-5.5z" fill="currentColor" stroke="none"/></svg>';
  const PAUSE = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 5v10M13 5v10" stroke-width="2.4"/></svg>';

  const bar = el(`<div id="replay" class="idle" role="region" aria-label="Replay">
    <button type="button" id="rp-play" class="rp-main">${PLAY}<span id="rp-word">Replay the analysis</span><small id="rp-len"></small></button>
    <div id="rp-track" hidden><div id="rp-fill"></div><div id="rp-marks"></div></div>
    <span id="rp-now" hidden></span>
    <span id="rp-clock" class="n" hidden></span>
    <div id="rp-speed" role="group" aria-label="Speed" hidden>
      <button type="button" data-x="1" aria-pressed="true">1×</button><button type="button" data-x="2">2×</button><button type="button" data-x="4">4×</button>
    </div>
    <button type="button" id="rp-end" class="rp-link" hidden>Skip to the end</button>
  </div>`);
  // what the page is, for someone who opens it cold, with the replay's start and who made it
  const intro = el(`<header id="intro">
    <p class="in-text"><strong>What could move your portfolio?</strong> An AI analyst starts with your holdings, identifies
      events that could affect them, and estimates how likely each event is and how much you could gain or lose under
      each outcome.</p>
    <div class="in-act"><button type="button" id="in-play">${PLAY}<span class="in-word">Replay the analysis</span><small id="in-len"></small></button>
      <span id="in-by"></span><a id="in-gh" target="_blank" rel="noopener">GitHub</a></div>
  </header>`);
  document.body.prepend(intro);
  document.body.append(bar);
  document.body.classList.add("has-replay");
  bar.hidden = true;
  const $ = (id) => bar.querySelector(id);
  intro.querySelector("#in-gh").href = window.SITE_REPO || "#";
  intro.querySelector("#in-by").textContent = window.SITE_BY ? `Built by ${window.SITE_BY}` : "";
  const room = () => document.documentElement.style.setProperty("--intro-h", `${intro.offsetHeight}px`);
  new ResizeObserver(room).observe(intro);
  room();

  // steps: [{t, d, ev, type?}]; at: the second, at 1x, each step starts at; i: the step being held; into: the
  // milliseconds, at 1x, spent in it; typed: how much of a reply's words (a step's `type`) has been sent
  const R = { data: null, at: [], state: "idle", i: 0, into: 0, typed: 0, begun: -1, speed: 1, timer: 0, last: 0, about: null };

  const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const analyst = (event) => ({ type: "analyst", event });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  SITE.site.then((d) => {
    R.about = d.replay || null;
    if (!R.about) { intro.querySelector("#in-play").hidden = true; return; }
    controls();  // the replay's length beside its start
    if (location.hash === "#play") play();
  });

  async function load() {
    if (R.data) return R.data;
    intro.querySelector(".in-word").textContent = "Loading…";
    const d = (R.data = await SITE.read("data/replay.json"));
    let sum = 0;
    R.at = d.steps.map((s) => { const a = sum; sum += s.d; return a; });
    R.total = sum;
    $("#rp-marks").innerHTML = d.marks.map((m, k) =>
      `<button type="button" data-mark="${k}" style="left:${(100 * R.at[m.step] / sum).toFixed(2)}%" title="${esc(m.label)}" aria-label="${esc(m.label)}"></button>`).join("");
    return d;
  }

  // The page as it was before the first message: no conversation, no panels, the first screen.
  async function reset() {
    const d = R.data;
    SITE.live = { state: d.empty, panels: { portfolio: "", ledger: "" },
      conversation: { turns: [], store: d.state.store, labels: {} } };
    if (typeof S !== "undefined") S.stagePicked = false;  // the page's stage follows the conversation again (web/app.js)
    SITE.emit({ type: "state", ...d.empty });   // any reply under way on the page ends
    SITE.emit({ type: "conversation" });        // the page leaves what it had open and reads the conversation: none yet
    SITE.emit({ type: "panel", name: "portfolio", html: "" });
    SITE.emit({ type: "panel", name: "ledger", html: "" });
    await sleep(80);
    SITE.emit({ type: "state", ...d.empty });
    composer(null);
    R.i = 0; R.into = 0; R.typed = 0; R.begun = -1;
  }

  // The composer as the member had it before sending a message: its words, and the files dropped with it. The page's
  // own composer shows them (web/app.js: its input, and the attachments it lists from S.files).
  function composer(message) {
    const input = document.getElementById("input");
    input.readOnly = !!message;
    input.value = message ? message.text : "";
    input.dispatchEvent(new Event("input"));
    if (typeof S !== "undefined" && typeof renderAttachments === "function") {
      S.files = message ? message.files.map((name) => ({ name })) : [];
      renderAttachments();
    }
  }

  function begin(s) {
    if (s.sent) composer(null);
    for (const ev of s.ev) SITE.emit(ev);
    if (s.compose) composer(s.compose);
  }

  function typeTo(s, share) {
    const upto = Math.min(s.type.length, Math.ceil(s.type.length * share));
    if (upto > R.typed) {
      SITE.emit(analyst({ type: "delta", kind: "text", delta: s.type.slice(R.typed, upto) }));
      R.typed = upto;
    }
  }

  function advance(ms) {
    const steps = R.data.steps;
    while (R.i < steps.length) {
      const s = steps[R.i], d = s.d * 1000;
      if (R.begun !== R.i) { R.begun = R.i; R.typed = 0; begin(s); }
      const use = Math.min(d - R.into, ms);
      R.into += use; ms -= use;
      if (s.type) typeTo(s, d ? R.into / d : 1);
      if (R.into < d) break;
      R.i++; R.into = 0;
    }
    if (R.i >= steps.length) finish();
  }

  function draw() {
    const d = R.data;
    if (!d) return;
    const i = Math.min(R.i, d.steps.length - 1);
    const pos = R.state === "idle" ? 0 : (R.at[i] + R.into / 1000);
    $("#rp-fill").style.width = `${Math.min(100, 100 * pos / R.total).toFixed(2)}%`;
    $("#rp-clock").innerHTML = `${clock(d.steps[i].t)}<span class="rp-wide"> of ${Math.max(1, Math.round(d.seconds / 60))} min</span>`;
    let label = "";
    for (const m of d.marks) { if (m.step <= R.i) label = m.caption ?? m.label; else break; }
    if ($("#rp-now").textContent !== label) $("#rp-now").textContent = label;
    for (const b of bar.querySelectorAll("[data-mark]")) b.classList.toggle("past", d.marks[+b.dataset.mark].step <= R.i);
  }

  function tick() {
    if (R.state !== "playing") return;
    const now = performance.now();
    advance(Math.min(200, now - R.last) * R.speed);  // a tab left in the background waits where it was
    R.last = now;
    draw();
    if (R.state === "playing") R.timer = setTimeout(tick, 40);
  }

  // the bar shows while the replay plays or is paused, and the line above the page starts, pauses and resumes it
  function controls() {
    const on = R.state === "playing" || R.state === "paused";
    bar.className = R.state;
    bar.hidden = !on;
    document.documentElement.style.setProperty("--replay-h", on ? "44px" : "0px");
    document.body.classList.toggle("replaying", on);
    const word = R.state === "playing" ? "Pause" : R.state === "paused" ? "Resume" : "Replay the analysis";
    $("#rp-play").innerHTML = `${R.state === "playing" ? PAUSE : PLAY}<span id="rp-word">${word}</span>`;
    intro.querySelector("#in-play").innerHTML = `${R.state === "playing" ? PAUSE : PLAY}<span class="in-word">${word}</span>`
      + (on || !R.about ? "" : `<small id="in-len">${clock(R.about.length)}</small>`);
    for (const id of ["#rp-track", "#rp-now", "#rp-clock", "#rp-speed", "#rp-end"]) $(id).hidden = !on;
  }

  async function play() {
    if (R.state === "playing") { clearTimeout(R.timer); R.state = "paused"; controls(); return; }
    if (R.state !== "paused") {
      await load();
      await reset();
    }
    R.state = "playing";
    R.last = performance.now();
    controls();
    tick();
  }

  // The conversation as it stood when a step began: everything before it at once, then on from there.
  async function seek(k) {
    const was = R.state;
    clearTimeout(R.timer);
    R.state = "paused";
    await reset();
    const steps = R.data.steps, panels = {};
    for (let j = 0; j < k; j++) {
      for (const ev of steps[j].ev) {
        if (ev.type === "panel") panels[ev.name] = ev;
        else if (ev.type !== "progress") SITE.emit(ev);
      }
      if (steps[j].type) SITE.emit(analyst({ type: "delta", kind: "text", delta: steps[j].type }));
    }
    for (const ev of Object.values(panels)) SITE.emit(ev);
    composer(null);
    R.i = k; R.into = 0; R.typed = 0; R.begun = -1;
    R.state = was === "playing" ? "paused" : was;
    draw();
    if (was === "playing") play(); else controls();
  }

  function finish() {
    clearTimeout(R.timer);
    composer(null);
    SITE.live = null;  // the page reads the finished conversation again
    R.state = "done";
    controls();
  }

  async function end() {
    if (!R.data) return;
    await seek(R.data.steps.length);
    finish();
  }

  $("#rp-play").addEventListener("click", play);
  intro.querySelector("#in-play").addEventListener("click", play);
  $("#rp-end").addEventListener("click", end);
  $("#rp-speed").addEventListener("click", (e) => {
    const b = e.target.closest("[data-x]");
    if (!b) return;
    R.speed = +b.dataset.x;
    for (const x of bar.querySelectorAll("[data-x]")) x.setAttribute("aria-pressed", String(x === b));
  });
  $("#rp-marks").addEventListener("click", (e) => {
    const b = e.target.closest("[data-mark]");
    if (b) seek(R.data.marks[+b.dataset.mark].step);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== " " || e.target.closest("input, textarea, button, a, [tabindex]")) return;
    if (R.state === "playing" || R.state === "paused") { e.preventDefault(); play(); }
  });
  controls();
})();
