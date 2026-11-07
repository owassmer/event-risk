// The page's browser code: our own small layer, no framework and no build step. It streams the
// conversation, merges each updated panel into place, and opens every detail on the right.
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
const S = {
  state: { busy: false, waiting: [], missing: [], can_reply: false, store: null },
  turns: [],
  trail: [],        // the steps opened after the ledger: {target, title, opener, html, scroll}
  shown: 0,         // how many of them are on screen: the last one shown is the trail's shown-th, 0 the ledger
  nav: 0,           // each navigation's number: a response for an earlier one is dropped
  storeSeq: 0,      // each change to the store's number
  refreshSeq: 0,    // each refresh of the step on screen
  ledgerScroll: 0,  // where the ledger was left
  left: null,       // what opened the step just left, marked in the view returned to
  keyboard: false,  // whether the last navigation came from the keyboard
  loaded: false,    // whether the page's first reading of the server has succeeded
  chips: new Map(),
  live: null,       // the turn being written: {turn, el, work, text}
  files: [],        // attachments waiting in the composer
  labels: {},       // what a member calls each id a call names
  rank: "emove",    // the ledger's order: by expected move, or worst case first
};

// ---------------------------------------------------------------- small helpers

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

async function api(path) {
  const r = await fetch(path, { cache: "no-store" });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

async function post(path, body = {}) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function notice(text) {
  $("#notice").textContent = text || "";
}

function duration(ms) {
  if (!(ms > 0)) return "";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

function nearBottom() {
  const el = $("#scroll");
  return el.scrollHeight - el.scrollTop - el.clientHeight < 140;
}

function toBottom(force) {
  const el = $("#scroll");
  if (force || S.stick) el.scrollTop = el.scrollHeight;
}

// ---------------------------------------------------------------- markdown, our own

function inline(text) {
  const codes = [];
  let s = text.replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
  s = esc(s);
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  // a signed figure stays whole: a line never breaks between its sign and its number
  s = s.replace(/(^|[\s(\[\/*_])([−+\-±~≈]\s?[$€£]?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|pp|bp|k|m|bn|x|×)(?![\w]))?)/g, '$1<span class="nw">$2</span>');
  s = s.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>").replace(/__([^_]+?)__/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*(?!\s)([^*]+?)\*(?!\w)/g, "$1<em>$2</em>").replace(/(^|[^_\w])_(?!\s)([^_]+?)_(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
}

const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
// a figure, alone or as a range, with its sign, currency, and unit: "−$40.4k", "20%", "≥ 15%", "4.0%–32%"
const FIG = "[~≈±+\\-−≥≤<>]?\\s?[$€£]?\\d[\\d,]*(?:\\.\\d+)?\\s?(?:%|pp|bp|k|m|bn|x|×)?";
const FIGURE = new RegExp(`^${FIG}(?:\\s?[–—-]\\s?${FIG})?(?:\\s?\\(${FIG}\\))?$`, "i");

function md(src) {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let i = 0;
  const blockStart = (l) => /^\s*(```|#{1,6}\s|>|(---|\*\*\*|___)\s*$)/.test(l) || LIST.test(l) || /^\s*\|.*\|\s*$/.test(l);
  while (i < lines.length) {
    const line = lines[i];
    let m;
    if (/^\s*$/.test(line)) { i++; continue; }
    if (/^\s*```/.test(line)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`);
      continue;
    }
    if ((m = line.match(/^\s*(#{1,6})\s+(.*?)\s*#*\s*$/))) {
      const n = Math.min(m[1].length, 4);
      out.push(`<h${n}>${inline(m[2])}</h${n}>`);
      i++;
      continue;
    }
    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
      // a column whose every entry is a figure reads as one: right-aligned, each figure whole
      const blank = (c) => /^(|—|–|-|n\/a)$/i.test((c || "").replace(/\*\*|__/g, "").trim());
      const num = head.map((_, k) => rows.some((r) => !blank(r[k])) && rows.every((r) => blank(r[k]) || FIGURE.test(r[k].replace(/\*\*|__/g, "").trim())));
      const at = (k) => (num[k] ? ' class="num"' : "");
      out.push(`<table><thead><tr>${head.map((c, k) => `<th${at(k)}>${inline(c)}</th>`).join("")}</tr></thead><tbody>` +
        rows.map((r) => `<tr>${r.map((c, k) => `<td${at(k)}>${inline(c)}</td>`).join("")}</tr>`).join("") + "</tbody></table>");
      continue;
    }
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote>${md(buf.join("\n"))}</blockquote>`);
      continue;
    }
    if (LIST.test(line)) {
      const [html, next] = list(lines, i, LIST.exec(line)[1].length);
      out.push(html);
      i = next;
      continue;
    }
    const buf = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !(buf.length && blockStart(lines[i]))) buf.push(lines[i++].trim());
    out.push(`<p>${inline(buf.join(" "))}</p>`);
  }
  return out.join("");
}

function list(lines, i, indent) {
  const first = LIST.exec(lines[i]);
  const ordered = /\d/.test(first[2]);
  const start = ordered ? parseInt(first[2], 10) : 1;
  const items = [];
  const blank = (k) => k < lines.length && /^\s*$/.test(lines[k]);
  const indentOf = (l) => l.match(/^\s*/)[0].length;
  while (i < lines.length) {
    if (blank(i)) {
      let k = i;
      while (blank(k)) k++;
      if (k >= lines.length) break;
      const m = LIST.exec(lines[k]);
      // a blank line between items, or before a deeper list, keeps the list going; so does a paragraph indented
      // under the item, which stays in it (CommonMark), and the numbering with it
      if (m && m[1].length >= indent) { i = k; continue; }
      if (!m && items.length && indentOf(lines[k]) > indent) {
        items[items.length - 1].paras.push([]);
        i = k;
        while (i < lines.length && !blank(i) && !LIST.test(lines[i])) items[items.length - 1].paras.at(-1).push(lines[i++].trim());
        continue;
      }
      break;
    }
    const m = LIST.exec(lines[i]);
    if (!m || m[1].length < indent) break;
    if (m[1].length > indent) {
      const [nested, next] = list(lines, i, m[1].length);
      if (items.length) items[items.length - 1].nested += nested;
      i = next;
      continue;
    }
    items.push({ paras: [[m[3]]], nested: "" });
    i++;
    while (i < lines.length && !blank(i) && !LIST.test(lines[i]) && /^\s+/.test(lines[i])) {
      items[items.length - 1].paras[0].push(lines[i++].trim());
    }
  }
  const tag = ordered ? "ol" : "ul";
  const body = (it) => it.paras.length === 1 ? inline(it.paras[0].join(" "))
    : it.paras.map((p) => `<p>${inline(p.join(" "))}</p>`).join("");
  return [`<${tag}${ordered && start !== 1 ? ` start="${start}"` : ""}>${items.map((it) => `<li>${body(it)}${it.nested}</li>`).join("")}</${tag}>`, i];
}

// ---------------------------------------------------------------- ids in the analyst's words, as chips

const TICKER = "[A-Z0-9][A-Z0-9.\\-]*(?::[A-Z0-9.\\-]+)?";
const ID = `(?:prop:\\d+|channel:\\d+|src:\\d+|note:\\d+|link:\\d+|cmp:\\d+|jdg:\\d+|market:(?:polymarket|kalshi|hyperliquid):[A-Za-z0-9_.#\\-]+|metric:price:${TICKER}|metric:[a-z][a-z0-9_]*|name:${TICKER})`;
const ID_IN_TEXT = new RegExp(`(?<![\\w:/])${ID}`, "g");
const ID_WHOLE = new RegExp(`^${ID}$`);
const trimId = (s) => s.replace(/[.\-:]+$/, "");

function chipEl(id) {
  const a = document.createElement("a");
  a.className = "chip";
  a.tabIndex = 0;
  a.setAttribute("role", "link");
  a.dataset.go = id;
  a.dataset.chip = id;
  a.dataset.id = id;
  fillChip(a);
  return a;
}

function fillChip(a) {
  const c = S.chips.get(a.dataset.chip);
  if (!c) { a.textContent = ""; return; }
  a.classList.toggle("gone", !c.exists);
  a.title = c.title || c.label;
  a.innerHTML = `<span class="chip-t">${esc(c.label)}</span>${c.value ? `<span class="chip-v">${esc(c.value)}</span>` : ""}`;
}

// `typing`: the text is still arriving, so an id at its very end may be part of a longer one, and waits
function decorate(root, typing = false) {
  for (const el of $$("[data-md]", root)) {  // a note: its markdown, rendered as a reply's is
    if (el._md === el.dataset.md) continue;
    el.innerHTML = md(el.dataset.md);
    el._md = el.dataset.md;
  }
  for (const code of $$("code", root)) {
    if (code.closest("pre")) continue;
    const t = code.textContent.trim();
    if (ID_WHOLE.test(t)) code.replaceWith(chipEl(t));
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => n.parentElement.closest("pre, code, a, .chip, .id") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  const texts = [];
  while (walker.nextNode()) texts.push(walker.currentNode);
  const last = texts[texts.length - 1];
  for (const node of texts) {
    const s = node.nodeValue;
    ID_IN_TEXT.lastIndex = 0;
    if (!ID_IN_TEXT.test(s)) continue;
    ID_IN_TEXT.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let at = 0, m;
    while ((m = ID_IN_TEXT.exec(s))) {
      if (typing && node === last && m.index + m[0].length === s.length) break;
      const id = trimId(m[0]);
      frag.append(s.slice(at, m.index), chipEl(id));
      at = m.index + id.length;
    }
    frag.append(s.slice(at));
    node.replaceWith(frag);
  }
  focusable(root);
  wantChips();
}

// New ids are named as they appear, at a steady pace while text streams in, and none is asked for twice; when the
// store changes, every chip on the page is asked for again, once the changes settle.
const CHIP_EVERY = 200;
const chipAsked = new Set();
let chipTimer = null, chipRefresh = null, chipRetry = 0;
function wantChips(refresh = false) {
  if (refresh) {
    clearTimeout(chipRefresh);
    chipRefresh = setTimeout(() => fetchChips(true), 250);
  } else if (!chipTimer) {
    chipTimer = setTimeout(() => { chipTimer = null; fetchChips(false); }, CHIP_EVERY);
  }
}

async function fetchChips(refresh) {
  if (!S.state.store) return;
  const ids = [...new Set($$("[data-chip]").map((a) => a.dataset.chip))]
    .filter((id) => refresh || (!S.chips.has(id) && !chipAsked.has(id)));
  if (!ids.length) return;
  for (const id of ids) chipAsked.add(id);
  try {
    const got = await api(`/api/chips?ids=${encodeURIComponent(ids.join(","))}`);
    for (const [id, c] of Object.entries(got)) S.chips.set(id, c);
    for (const a of $$("[data-chip]")) if (got[a.dataset.chip]) fillChip(a);
  } catch (e) {
    for (const id of ids) chipAsked.delete(id);  // the chip keeps its id, and is asked for again, a little later each time
    chipRetry = Math.min(chipRetry ? chipRetry * 2 : 1000, 30000);
    setTimeout(() => fetchChips(refresh), chipRetry);
    return;
  }
  chipRetry = 0;
}

// ---------------------------------------------------------------- the work line

const VENUE = { polymarket: "Polymarket", kalshi: "Kalshi", hyperliquid: "Hyperliquid" };
// each kind of a company's record, as the work line names it
const RECORD = { news: "news", earnings: "earnings", guidance: "guidance", ratings: "analysts' ratings",
  risk_factors: "risk factors", annual_report: "annual report", current_reports: "current reports" };
// each call counts as one kind of step, in this order
const KIND = { search_markets: "market", search_web: "web", fetch_source: "page", search_company: "record",
  add: "change", link: "change", note: "change", edit: "change", remove: "change" };
const KINDS = [["web", "web search", "web searches"], ["page", "page read", "pages read"],
  ["record", "company record", "company records"],
  ["market", "market search", "market searches"], ["change", "change", "changes"], ["lookup", "lookup", "lookups"]];
const TYPES = { Proposition: "events and questions", Market: "markets", Metric: "levels", Name: "holdings",
  Channel: "factors", Source: "pages", Note: "notes", Portfolio: "portfolio" };

function host(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url || "a page"; }
}

// an id as a member reads it: an event or a market in quotes, a level, a holding, or a factor by its name
function said(id, fallback) {
  const l = id && S.labels[id];
  if (!l) return fallback;
  return /^(prop|market):/.test(id) ? `“${l}”` : l;
}

function describe(c) {
  const i = c.input || {};
  const f = i.fields || {};
  switch (c.tool) {
    case "add":
      if (i.type === "Portfolio") return "Pricing the portfolio";
      if (i.type === "Proposition") return f.title ? `Defining “${f.title}”` : "Defining an event";
      if (i.type === "Metric") return f.name ? `Following ${f.name}` : "Following a new level";
      if (i.type === "Channel") return f.name ? `Adding the factor ${f.name}` : "Adding a factor";
      return "Adding to its work";
    case "link": {
      const r = i.relation;
      const a = said(i.from_id, null), b = said(i.to_id, null);
      if (r === "watches") return b ? `Adding ${b} to the events` : "Adding an event";
      if (r === "quoted_by") return a && b ? `Matching ${a} to the market ${b}` : "Matching a question to a market";
      if (r === "implies") return a && b ? `Relating ${a} to ${b}` : "Relating two questions";
      if (r === "and" || r === "or" || r === "then") return a ? `Breaking ${a} into smaller questions` : "Breaking an event into smaller questions";
      if (r === "evidence") return a && b ? `Quoting ${b} for ${a}` : "Quoting a page";
      if (r === "consequence") return a && b ? `Estimating the shock to ${b} from ${a}` : "Estimating an event's shock to a price";
      if (r === "tracks") return a && b ? `Tying ${a} to ${b}` : "Tying a level to a factor";
      return a && b ? `Connecting ${a} and ${b}` : "Connecting two parts of its work";
    }
    case "note": {
      const on = said((i.about_ids || [])[0], null);
      return on ? `Writing a note on ${on}` : "Writing a note";
    }
    case "edit": return said(i.id, null) ? `Revising ${said(i.id)}` : "Revising its work";
    case "remove": return said(i.id, null) ? `Removing ${said(i.id)}` : "Removing part of its work";
    case "search_markets": {
      const q = Object.values(i.params || {}).find((v) => typeof v === "string" && v.trim());
      return `Searching ${VENUE[i.venue] || "a market"}${q ? ` for “${q}”` : ""}`;
    }
    case "search_web": return i.text ? `Searching the web for “${i.text}”` : "Searching the web";
    case "fetch_source": return `Reading ${host(i.url)}`;
    case "search_company": {
      const what = RECORD[i.kind];
      return i.ticker ? `Reading ${i.ticker}'s ${what || "record"}` : "Reading a company's record";
    }
    case "exposure": return "Reading the portfolio's dollar beta to each factor";
    case "window_returns": return i.start ? `Looking at how the factors moved from ${i.start} to ${i.end}` : "Looking at how the factors moved in a past stretch";
    case "get": return said(i.id, null) ? `Reading ${said(i.id)}` : "Reading its work";
    case "query": return `Looking through its ${TYPES[i.type] || "work"}`;
    case "neighbors": return said(i.id, null) ? `Reading what connects to ${said(i.id)}` : "Reading what connects";
    case "ledger": return "Reading the events";
    case "check": return "Checking its work for gaps";
    default: return "Working";
  }
}

function stageText(p) {
  if (p.stage === "holdings") return "Pricing the holdings";
  if (p.stage === "funds") {
    if (p.about) return p.of > 1 ? `Looking inside ${p.about}, ${p.done} of ${p.of} names` : `Looking inside ${p.about}`;
    if (p.kind === "constituents" && p.subject) return `Looking inside ${p.subject}`;
    return "Looking inside the funds";
  }
  if (p.stage === "prices") return `Loading two years of ${p.kind === "dividends" ? "dividends" : "prices"}, ${p.done} of ${p.of}`;
  if (p.stage === "exposures") return "Fitting the holdings' betas";
  if (p.stage === "news") return "Reading the news on the largest holdings";
  return null;
}

function steps(n) {
  return `${n} step${n === 1 ? "" : "s"}`;
}

function tally(calls) {
  const counts = {};
  let n = 0;
  for (const c of calls) {
    if (!c.tool) continue;
    const kind = KIND[c.tool] || "lookup";
    counts[kind] = (counts[kind] || 0) + 1;
    n++;
  }
  const words = KINDS.filter(([k]) => counts[k]).map(([k, one, many]) => `${counts[k]} ${counts[k] === 1 ? one : many}`);
  return { n, words };
}

function workSummary(work) {
  const t = tally(work.calls);
  const time = duration(Date.parse(work.to) - Date.parse(work.from));
  if (!t.n) return { now: "Thought it through", right: time };
  return { now: t.words.join(" · "), right: `${steps(t.n)}${time ? " · " + time : ""}` };
}

function workEl(work) {
  const el = document.createElement("div");
  el.className = "work" + (work.live ? " live" : "");
  el.innerHTML = `<button class="line" type="button"><span class="dot"></span><span class="now"></span><span class="tally"></span>
    <svg class="chev" viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5l5 5-5 5"/></svg></button><div class="body"></div>`;
  el._work = work;
  $(".line", el).addEventListener("click", () => {
    el.classList.toggle("open");
    if (el.classList.contains("open")) fillWorkBody(el);
  });
  updateWork(el);
  return el;
}

function updateWork(el) {
  const work = el._work;
  el.classList.toggle("live", !!work.live);
  if (work.live) {
    $(".now", el).textContent = work.now ? describe(work.now) : work.status || "Thinking";
    const n = work.calls.filter((c) => c.tool).length;
    $(".tally", el).textContent = n ? steps(n) : "";
  } else {
    const s = workSummary(work);
    $(".now", el).textContent = s.now;
    $(".tally", el).textContent = s.right;
  }
  if (el.classList.contains("open")) fillWorkBody(el);
}

function fillWorkBody(el) {
  const body = $(".body", el);
  const work = el._work;
  const have = body.children.length;
  // entries are only ever added, so the body grows in place and keeps what is open
  for (let k = have; k < work.calls.length; k++) body.append(entryEl(work, work.calls[k]));
  for (let k = 0; k < Math.min(have, work.calls.length); k++) {
    const entry = work.calls[k];
    const node = body.children[k];
    if (entry.thinking !== undefined) {
      const text = work.thinking[entry.thinking].trim();
      if (node.textContent !== text) node.textContent = text;
    } else {
      const sum = $("summary", node);
      sum.className = entry.not_run ? "notrun" : entry.ok === false ? "err" : "";
      $(".what", sum).textContent = describe(entry);
      if (node.open && node._shown !== (entry.call ?? null)) showCall(node);
    }
  }
}

function entryEl(work, entry) {
  if (entry.thinking !== undefined) {
    const p = document.createElement("p");
    p.className = "think";
    p.textContent = work.thinking[entry.thinking].trim();
    return p;
  }
  const d = document.createElement("details");
  d.innerHTML = `<summary class="${entry.not_run ? "notrun" : entry.ok === false ? "err" : ""}"><span class="what">${esc(describe(entry))}</span></summary><div class="io"></div>`;
  d._entry = entry;
  d.addEventListener("toggle", () => { if (d.open) showCall(d); });
  return d;
}

// an open call shows its input and what it returned, read again once the call that was running has finished
async function showCall(d) {
  const entry = d._entry;
  d._shown = entry.call ?? null;
  const io = $(".io", d);
  let input = entry.input, result = entry.preview, error = false;
  if (entry.call) {
    try {
      const got = await api(`/api/call?n=${entry.call}`);
      input = got.input;
      result = got.result !== undefined && got.result !== null ? got.result : got.error;
      error = got.result === undefined || got.result === null;
    } catch { /* the preview stands */ }
  }
  const show = (v) => esc(typeof v === "string" ? v : JSON.stringify(v, null, 2));
  io.innerHTML = `<div class="label">Input</div><pre>${show(input)}</pre>` +
    (result !== undefined ? `<div class="label">${error ? "Refused" : entry.not_run ? "Not run" : "Result"}</div><pre>${show(result)}</pre>` : "");
}

// ---------------------------------------------------------------- the conversation

function turnEl(turn) {
  const el = document.createElement("div");
  el.className = "turn";
  el.dataset.turn = turn.turn;
  el.dataset.session = turn.session;
  el._turn = turn;
  const files = (turn.files || []).map((f) =>
    `<a class="file" href="/api/file?path=${encodeURIComponent(f.path)}" target="_blank" rel="noopener">${FILE_ICON}${esc(f.name)}</a>`).join("");
  el.innerHTML = `<div class="you"><div class="bubble">${files ? `<div class="files">${files}</div>` : ""}<span class="said"></span></div>
    <div class="actions"><button class="linkish" data-act="edit" type="button" title="${EDIT_SAYS}">Edit</button></div></div><div class="reply"></div>
    <div class="actions after"><button class="linkish" data-act="retry" type="button" title="${RETRY_SAYS}">${RETRY_ICON}Retry</button></div>`;
  $(".said", el).textContent = turn.text;
  const reply = $(".reply", el);
  for (const item of turn.items) reply.append(itemEl(item));
  if (turn.stopped) reply.append(stoppedEl());
  decorate(reply);
  return el;
}

// what Edit and Retry do, said where they are offered
const EDIT_SAYS = "Change this message and send it again: the conversation and the ledger go back to before it";
const RETRY_SAYS = "Send this message again: the conversation and the ledger go back to before it";
const FILE_ICON = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M11.5 3H6a1.5 1.5 0 0 0-1.5 1.5v11A1.5 1.5 0 0 0 6 17h8a1.5 1.5 0 0 0 1.5-1.5V7z"/><path d="M11.5 3v4h4"/></svg>';
const RETRY_ICON = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M15.5 10a5.5 5.5 0 1 1-1.6-3.9"/><path d="M15.5 4v3h-3"/></svg>';

function itemEl(item) {
  if (item.kind === "work") return workEl(item);
  const d = document.createElement("div");
  d.className = "md" + (item.partial ? " partial" : "");
  d.innerHTML = md(item.text);
  d._item = item;
  return d;
}

function stoppedEl() {
  const d = document.createElement("div");
  d.className = "stopped";
  d.textContent = "Stopped";
  return d;
}

function renderConversation() {
  const box = $("#conversation");
  box.replaceChildren(...S.turns.map(turnEl));
  S.live = null;
  updateActions();
  $("#app").classList.toggle("empty", !S.turns.length && !S.state.busy && !S.state.waiting.length);
  placeholder();
  markOpeners();
}

function updateActions() {
  const idle = !S.state.busy && S.state.can_reply;
  const turns = $$("#conversation .turn");
  turns.forEach((el, k) => {
    const t = el._turn;
    const can = idle && t && t.editable;
    for (const node of [$('[data-act="edit"]', el), $(".actions.after", el)]) if (node) node.hidden = !can;
  });
}

async function loadConversation() {
  const got = await api("/api/conversation");
  Object.assign(S.labels, got.labels || {});
  S.turns = got.turns || [];
  renderConversation();
  requestAnimationFrame(() => toBottom(true));
}

// the live turn, from the analyst's events
function liveTurn(session, turn) {
  let el = $(`#conversation .turn[data-turn="${turn}"][data-session="${session}"]`);
  if (!el) return null;
  if (!S.live || S.live.el !== el) S.live = { el, work: null, workEl: null, text: null, textEl: null };
  return S.live;
}

function currentWork(L) {
  if (L.work && L.work.live && L.el.querySelector(".reply").lastElementChild === L.workEl) return L;
  const work = { kind: "work", live: true, thinking: [], calls: [], status: "Thinking", from: new Date().toISOString(), to: null };
  L.el._turn.items.push(work);
  L.work = work;
  L.workEl = workEl(work);
  $(".reply", L.el).append(L.workEl);
  L.text = null;
  L.textEl = null;
  return L;
}

function onAnalyst(ev) {
  if (ev.type === "user") {
    const turn = { session: ev.session, turn: ev.turn, text: ev.text, files: (ev.files || []).map((f) => ({ name: f.name, path: f.path })), items: [], stopped: false, editable: true };
    S.turns.push(turn);
    $("#conversation").append(turnEl(turn));
    $("#app").classList.remove("empty");
    S.stick = true;
    updateActions();
    placeholder();
    requestAnimationFrame(() => toBottom(true));
    return;
  }
  const L = S.live || (ev.session ? liveTurn(ev.session, ev.turn) : liveTurn(S.state.session, lastTurn()));
  if (!L) return;
  S.stick = nearBottom();
  switch (ev.type) {
    case "block_start": {
      const b = ev.block;
      if (b.type === "thinking") {
        currentWork(L);
        L.work.thinking.push("");
        L.work.calls.push({ thinking: L.work.thinking.length - 1 });
        L.work.now = null;
        L.work.status = "Thinking";
      } else if (b.type === "text") {
        if (L.work) { L.work.live = false; L.work.to = new Date().toISOString(); updateWork(L.workEl); }
        const item = { kind: "text", text: "" };
        L.el._turn.items.push(item);
        L.text = item;
        L.textEl = itemEl(item);
        L.textEl._shown = 0;
        $(".reply", L.el).append(L.textEl);
        L.work = null;
      } else if (b.type === "tool_use") {
        currentWork(L);
        const call = { tool_use_id: b.id, tool: b.name, input: null, pending: true };
        L.work.calls.push(call);
        L.work.now = { tool: b.name, input: {} };
      }
      if (L.workEl) updateWork(L.workEl);
      break;
    }
    case "delta":
      if (ev.kind === "thinking" && L.work) {
        L.work.thinking[L.work.thinking.length - 1] += ev.delta;
        if (L.workEl.classList.contains("open")) fillWorkBody(L.workEl);
      } else if (ev.kind === "text" && L.text) {
        L.text.text += ev.delta;
        typewriter(L.textEl);
      }
      break;
    case "block_stop":
      if (ev.block.type === "tool_use" && L.work) {
        Object.assign(S.labels, ev.labels || {});
        const c = L.work.calls.find((x) => x.tool_use_id === ev.block.id);
        if (c) { c.input = ev.block.input; L.work.now = c; updateWork(L.workEl); }
      }
      if (ev.block.type === "text" && L.textEl) typewriter(L.textEl, true);
      break;
    case "call_start": {
      if (!L.work || !L.work.live) currentWork(L);
      Object.assign(S.labels, ev.labels || {});
      const c = L.work.calls.find((x) => x.tool_use_id === ev.tool_use_id) || {};
      c.running = true;
      L.work.now = { tool: ev.tool, input: ev.input };
      updateWork(L.workEl);
      break;
    }
    case "call": {
      if (!L.work) break;
      Object.assign(S.labels, ev.labels || {});
      const c = L.work.calls.find((x) => x.tool_use_id === ev.tool_use_id);
      if (c) Object.assign(c, { call: ev.call, ok: ev.ok, preview: ev.preview, running: false, input: ev.input, tool: ev.tool });
      L.work.to = new Date().toISOString();
      updateWork(L.workEl);
      break;
    }
    case "stop":
      L.el._turn.stopped = true;
      if (L.textEl) { L.textEl.classList.add("partial"); typewriter(L.textEl, true); }
      finishLive(L);
      $(".reply", L.el).append(stoppedEl());
      break;
    case "answer":
      finishLive(L);
      break;
  }
  requestAnimationFrame(() => toBottom());
}

function lastTurn() {
  return S.turns.length ? S.turns[S.turns.length - 1].turn : 0;
}

function finishLive(L) {
  for (const w of $$(".work", L.el)) {
    // a call with no result when the turn ends was answered as not run: the turn was stopped before it
    for (const c of w._work.calls) if (c.tool && c.call === undefined && !c.not_run) { c.not_run = true; c.running = false; }
    if (w._work.live) { w._work.live = false; w._work.to = w._work.to || new Date().toISOString(); }
    updateWork(w);
  }
  if (L.textEl) typewriter(L.textEl, true);
  decorate($(".reply", L.el));
  S.live = null;
  updateActions();
}

function onProgress(p) {
  const L = S.live;
  if (!L || !L.work) return;
  const text = stageText(p);
  if (text) { L.work.now = null; L.work.status = text; updateWork(L.workEl); }
}

// text streams smoothly: what has arrived is shown a little ahead of the last frame
function typewriter(el, flush = false) {
  const item = el._item;
  if (flush || reducedMotion.matches) {
    el._shown = item.text.length;
    el.innerHTML = md(item.text);
    decorate(el);
    return;
  }
  if (el._raf) return;
  const step = () => {
    el._raf = null;
    const target = item.text.length;
    if (el._shown >= target) return;
    el._shown = Math.min(target, el._shown + Math.max(2, Math.ceil((target - el._shown) / 9)));
    el.innerHTML = md(item.text.slice(0, el._shown));
    decorate(el, true);  // block_stop flushes the whole text, and decorates it whole
    toBottom();
    if (el._shown < target) el._raf = requestAnimationFrame(step);
  };
  el._raf = requestAnimationFrame(step);
}

// ---------------------------------------------------------------- edit and retry

$("#conversation").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-act]");
  if (!btn) return;
  const el = btn.closest(".turn");
  const turn = el._turn;
  if (btn.dataset.act === "retry") {
    try { await post("/api/retry", { turn: turn.turn }); } catch (e) { notice(e.message); }
    return;
  }
  if (btn.dataset.act === "edit") {
    // an edited message keeps the files it was sent with, each shown as the composer shows one, and removable
    const you = $(".you", el);
    let kept = (turn.files || []).slice();
    you.classList.add("editing");
    you.innerHTML = `<div class="files"></div><textarea></textarea><div class="row"><button class="btn" type="button" data-edit="cancel">Cancel</button>
      <button class="btn dark" type="button" data-edit="send">Send</button></div>`;
    const showKept = () => {
      $(".files", you).innerHTML = kept.map((f, k) =>
        `<span class="file">${FILE_ICON}${esc(f.name)}<button type="button" data-unkeep="${k}" title="Remove">×</button></span>`).join("");
    };
    showKept();
    const ta = $("textarea", you);
    ta.value = turn.text;
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
    you.addEventListener("click", async (e2) => {
      const gone = e2.target.closest("[data-unkeep]");
      if (gone) { kept.splice(+gone.dataset.unkeep, 1); showKept(); ta.focus(); return; }
      const b = e2.target.closest("[data-edit]");
      if (!b) return;
      if (b.dataset.edit === "cancel") { el.replaceWith(turnEl(turn)); updateActions(); return; }
      try { await post("/api/edit", { turn: turn.turn, text: ta.value, files: kept.map((f) => f.path) }); } catch (e) { notice(e.message); }
    });
  }
});

// ---------------------------------------------------------------- the composer

const input = $("#input");

function placeholder() {
  input.placeholder = !S.state.can_reply ? "" : S.turns.length ? "Message the analyst" : "Paste holdings, or attach a statement";
}

function grow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, innerHeight * 0.4) + "px";
  canSend();
}

function canSend() {
  $("#send").disabled = !S.state.can_reply || (!input.value.trim() && !S.files.length);
}

input.addEventListener("input", grow);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
});

$("#composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  if ($("#send").disabled) return;
  const text = input.value;
  const files = S.files.slice();
  input.value = "";
  S.files = [];
  renderAttachments();
  grow();
  notice("");
  try {
    await post("/api/send", { text, files });
  } catch (err) {
    if (!input.value.trim() && !S.files.length) {  // nothing written since: the message is back in the composer
      input.value = text;
      S.files = files;
      renderAttachments();
      grow();
      notice(err.message);
    } else {  // a newer draft stays, and the message that failed is a click away
      S.unsent = { text, files };
      $("#notice").innerHTML = `${esc(err.message)} <button class="linkish" type="button" data-unsent>Put back the message that wasn't sent</button>`;
    }
  }
});

$("#notice").addEventListener("click", (e) => {
  if (!e.target.closest("[data-unsent]") || !S.unsent) return;
  const { text, files } = S.unsent;
  S.unsent = null;
  input.value = input.value.trim() ? `${input.value.replace(/\s+$/, "")}\n\n${text}` : text;
  S.files = S.files.concat(files);
  renderAttachments();
  grow();
  notice("");
  input.focus();
});

$("#stop").addEventListener("click", () => post("/api/stop").catch((e) => notice(e.message)));
$("#file").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });

const READABLE = /\.(pdf|png|jpe?g|gif|webp|csv|txt|tsv|md|json)$/i;

async function addFiles(list) {
  for (const f of list) {
    if (!READABLE.test(f.name)) { notice(`${f.name} can't be read: drop a PDF, an image (PNG, JPEG, GIF, WebP), or text (CSV, TXT).`); continue; }
    const data = await new Promise((ok, fail) => {
      const r = new FileReader();
      r.onload = () => ok(String(r.result).split(",", 2)[1] || "");
      r.onerror = fail;
      r.readAsDataURL(f);
    });
    S.files.push({ name: f.name, data });
  }
  renderAttachments();
  canSend();
  input.focus();
}

function renderAttachments() {
  $("#attachments").innerHTML = S.files.map((f, k) =>
    `<span class="file">${FILE_ICON}${esc(f.name)}<button type="button" data-drop-file="${k}" title="Remove">×</button></span>`).join("");
}

$("#attachments").addEventListener("click", (e) => {
  const b = e.target.closest("[data-drop-file]");
  if (!b) return;
  S.files.splice(+b.dataset.dropFile, 1);
  renderAttachments();
  canSend();
});

let dragDepth = 0;
addEventListener("dragenter", (e) => { if ([...e.dataTransfer.types].includes("Files")) { dragDepth++; $("#drop").hidden = false; } });
addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $("#drop").hidden = true; });
addEventListener("dragover", (e) => { if ([...e.dataTransfer.types].includes("Files")) e.preventDefault(); });
addEventListener("drop", (e) => {
  if (!e.dataTransfer.files.length) return;
  e.preventDefault();
  dragDepth = 0;
  $("#drop").hidden = true;
  addFiles(e.dataTransfer.files);
});

function renderWaiting() {
  $("#waiting").innerHTML = S.state.waiting.map((w) =>
    `<div class="you"><div class="bubble">${w.files.length ? `<div class="files">${w.files.map((n) => `<span class="file">${FILE_ICON}${esc(n)}</span>`).join("")}</div>` : ""}${esc(w.text)}</div>
     <div class="label">Queued · <button class="linkish" data-cancel="${w.id}" type="button">Cancel</button></div></div>`).join("");
}

$("#waiting").addEventListener("click", (e) => {
  const b = e.target.closest("[data-cancel]");
  if (b) post("/api/cancel", { id: +b.dataset.cancel }).catch((err) => notice(err.message));
});

function applyState(st) {
  S.state = st;
  $("#stop").hidden = !st.busy;
  // a missing key's line, where it stops something, and a finished example's, above the composer
  $("#keys").innerHTML = (st.missing || []).map((l) => `<div>${esc(l)}</div>`).join("");
  $("#composer").classList.toggle("off", !st.can_reply);
  input.disabled = !st.can_reply;
  $("#file").disabled = !st.can_reply;
  $(".attach").classList.toggle("off", !st.can_reply);
  renderWaiting();
  canSend();
  updateActions();
  placeholder();
  $$('#menu-pop [data-menu="download"]').forEach((b) => (b.disabled = !st.store));
  // a finished example's menu offers only what it can do: its ledger to download
  $$('#menu-pop [data-menu="new"], #menu-pop [data-menu="open"]').forEach((b) => (b.hidden = !!st.read_only));
  if (st.error) notice(st.error);
  if (!st.busy && S.live) finishLive(S.live);
  $("#app").classList.toggle("empty", !S.turns.length && !st.busy && !st.waiting.length && !st.store);
}

// ---------------------------------------------------------------- charts: one small line chart, drawn from its points

// a chart's points are [date, value]; its kind says how a value reads: a price in dollars, or a chance
const RANGES = [["1M", 1], ["6M", 6], ["1Y", 12], ["2Y", 24]];
const CW = 400, CH = 120;

function chartValue(kind, v) {
  if (kind === "chance") {
    const t = Math.round(v * 1000) / 10;
    return t < 10 ? `${t.toFixed(1)}%` : `${Math.round(v * 100)}%`;
  }
  return (v < 0 ? "\u2212" : "") + "$" + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function chartDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

function monthsBefore(iso, n) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1 - n, d)).toISOString().slice(0, 10);
}

// the ranges a series offers: each it covers, and the first it falls inside, which shows all of it
function chartRanges(points) {
  const last = points[points.length - 1][0], out = [];
  for (const [label, n] of RANGES) {
    out.push(label);
    if (points[0][0] >= monthsBefore(last, n)) break;
  }
  return out.length > 1 ? out : [];
}

function drawCharts(root) {
  for (const el of $$("[data-chart]", root)) if (el._chart !== el.dataset.chart) drawChart(el);
}

function drawChart(el) {
  el._chart = el.dataset.chart;
  const spec = JSON.parse(el.dataset.chart);
  if (el.classList.contains("spark")) { el.innerHTML = sparkSvg(spec.points); return; }
  const ranges = spec.ranges ? chartRanges(spec.points) : [];
  if (!ranges.includes(el._range)) el._range = ranges.includes(spec.range) ? spec.range : ranges[ranges.length - 1];
  el.innerHTML =
    `<div class="ch-top"><div class="ch-ranges" role="group" aria-label="Range">${ranges.map((r) =>
      `<button type="button" data-range="${r}">${r}</button>`).join("")}</div>` +
    `<div class="ch-read"><span class="ch-d"></span> <span class="ch-v n"></span></div></div>` +
    `<div class="ch-body"><div class="ch-plot"></div><div class="ch-y"><span class="n"></span><span class="n"></span></div></div>` +
    `<div class="ch-x"><span></span><span></span></div>`;
  for (const b of $$("[data-range]", el)) b.onclick = () => { el._range = b.dataset.range; plot(el, spec); };
  plot(el, spec);
}

function plot(el, spec) {
  for (const b of $$("[data-range]", el)) b.setAttribute("aria-pressed", String(b.dataset.range === el._range));
  const last = spec.points[spec.points.length - 1][0];
  const months = (RANGES.find(([r]) => r === el._range) || [])[1];
  let pts = months ? spec.points.filter((p) => p[0] >= monthsBefore(last, months)) : spec.points;
  if (pts.length < 2) pts = spec.points.slice(-2);
  const band = spec.band && spec.band.every((v) => typeof v === "number") ? spec.band : null;
  const vals = pts.map((p) => p[1]).concat(band || []);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 1e-9) { lo -= 0.5 * Math.abs(lo || 1) * 0.01; hi += 0.5 * Math.abs(hi || 1) * 0.01; }
  const right = band ? 14 : 2, pad = 6;
  const x = (i) => 2 + (i * (CW - 2 - right)) / (pts.length - 1);
  const y = (v) => pad + ((hi - v) / (hi - lo)) * (CH - 2 * pad);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  const tone = spec.kind === "price" ? (pts[pts.length - 1][1] >= pts[0][1] ? "up" : "down") : "flat";
  let marked = "";
  if (spec.mark) {
    const a = pts.findIndex((p) => p[0] >= spec.mark.from);
    let b = -1;
    pts.forEach((p, i) => { if (p[0] <= spec.mark.to) b = i; });
    if (a >= 0 && b >= a) marked = `<rect class="ch-mark" x="${x(a).toFixed(1)}" y="0" width="${Math.max(2, x(b) - x(a)).toFixed(1)}" height="${CH}"><title>${esc(spec.mark.label || "")}</title></rect>`;
  }
  const bandEl = band ? `<rect class="ch-band" x="${(CW - 8).toFixed(1)}" y="${y(band[1]).toFixed(1)}" width="6" height="${Math.max(2, y(band[0]) - y(band[1])).toFixed(1)}"><title>Implied range today</title></rect>` : "";
  const box = $(".ch-plot", el);
  box.className = `ch-plot ${tone}`;
  box.innerHTML = `<svg viewBox="0 0 ${CW} ${CH}" preserveAspectRatio="none" aria-hidden="true">${marked}${bandEl}` +
    `<path d="${d}"/></svg><i class="ch-cursor" hidden></i><i class="ch-dot"></i>`;
  const [yHi, yLo] = $$(".ch-y span", el);
  yHi.textContent = chartValue(spec.kind, hi);
  yLo.textContent = chartValue(spec.kind, lo);
  const [x0, x1] = $$(".ch-x span", el);
  x0.textContent = chartDate(pts[0][0]);
  x1.textContent = chartDate(pts[pts.length - 1][0]);
  const dot = $(".ch-dot", el), cursor = $(".ch-cursor", el);
  const show = (i, hover) => {
    $(".ch-d", el).textContent = chartDate(pts[i][0]);
    $(".ch-v", el).textContent = chartValue(spec.kind, pts[i][1]);
    dot.style.left = `${(100 * x(i)) / CW}%`;
    dot.style.top = `${(100 * y(pts[i][1])) / CH}%`;
    cursor.hidden = !hover;
    cursor.style.left = `${(100 * x(i)) / CW}%`;
  };
  show(pts.length - 1, false);
  box.onpointermove = (ev) => {
    const r = box.getBoundingClientRect();
    const at = ((ev.clientX - r.left) / r.width) * CW;
    const i = Math.max(0, Math.min(pts.length - 1, Math.round(((at - 2) * (pts.length - 1)) / (CW - 2 - right))));
    show(i, true);
  };
  box.onpointerleave = () => show(pts.length - 1, false);
}

// a line of a month, small enough to sit in a ledger line
function sparkSvg(points) {
  const w = 64, h = 16, vals = points.map((p) => p[1]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 0.005) { const m = (hi + lo) / 2; lo = m - 0.0025; hi = m + 0.0025; }
  const x = (i) => 1 + (i * (w - 4)) / (points.length - 1);
  const y = (v) => 2 + ((hi - v) / (hi - lo)) * (h - 4);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  const n = points.length - 1;
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true"><path d="${d}"/>` +
    `<circle cx="${x(n).toFixed(1)}" cy="${y(points[n][1]).toFixed(1)}" r="1.8"/></svg>`;
}

// ---------------------------------------------------------------- merging a panel into place

const TRANSIENT = ["enter", "changed", "all", "open"];

// `live` keeps its own attributes when it is the panel itself (`root`): only what is inside it is merged
function morph(live, next, root = true) {
  if (!root) syncAttrs(live, next);
  if (!root && next.dataset.md !== undefined && live._md === next.dataset.md) return;  // a note, rendered as it stands
  if (!root && next.dataset.chart !== undefined && live._chart === next.dataset.chart) return;  // a chart, drawn as it stands
  const lc = [...live.childNodes];
  const nc = [...next.childNodes];
  const keyed = new Map();
  for (const c of lc) if (c.nodeType === 1 && c.dataset.k) keyed.set(c.tagName + "|" + c.dataset.k, c);
  const used = new Set();
  let result = [];
  let pos = 0;
  for (const n of nc) {
    let match = null;
    if (n.nodeType === 1 && n.dataset.k) {
      match = keyed.get(n.tagName + "|" + n.dataset.k) || null;
    } else {
      while (pos < lc.length && (used.has(lc[pos]) || (lc[pos].nodeType === 1 && lc[pos].dataset.k))) pos++;
      const c = lc[pos];
      if (c && c.nodeType === n.nodeType && (c.nodeType !== 1 || c.tagName === n.tagName)) { match = c; pos++; }
    }
    if (match && !used.has(match)) {
      used.add(match);
      if (n.nodeType === 3) {
        if (match.nodeValue !== n.nodeValue) { match.nodeValue = n.nodeValue; flash(match.parentElement); }
      } else if (n.nodeType === 1) {
        morph(match, n, false);
      }
      result.push(match);
    } else {
      const fresh = document.importNode(n, true);
      if (fresh.nodeType === 1 && fresh.dataset.k && !reducedMotion.matches) fresh.classList.add("enter");
      result.push(fresh);
    }
  }
  if (!root && live.hasAttribute("data-hold")) {
    // rows hold their place: those already shown keep their order, and new ones join at the end
    const old = result.filter((n) => lc.includes(n)).sort((a, b) => lc.indexOf(a) - lc.indexOf(b));
    result = [...old, ...result.filter((n) => !lc.includes(n))];
  }
  for (const c of lc) if (!used.has(c)) c.remove();
  result.forEach((node, k) => {
    const at = live.childNodes[k];
    if (at !== node) live.insertBefore(node, at || null);
  });
}

// what the page itself sets on a link that opens a step: it is reachable from the keyboard, and marked while open
const OWN = new Set(["tabindex", "role", "aria-current", "data-opened"]);

function syncAttrs(a, b) {
  for (const { name } of [...a.attributes]) {
    if (b.hasAttribute(name) || name === "class") continue;
    if (name === "open" && a.tagName === "DETAILS") continue;
    if (OWN.has(name) && a.hasAttribute("data-go")) continue;
    a.removeAttribute(name);
  }
  for (const { name, value } of [...b.attributes]) {
    if (name === "class") continue;
    if (a.getAttribute(name) !== value) a.setAttribute(name, value);
  }
  const keep = TRANSIENT.filter((c) => a.classList.contains(c));
  const want = [...b.classList, ...keep];
  if ([...a.classList].sort().join(" ") !== [...new Set(want)].sort().join(" ")) a.className = [...new Set(want)].join(" ");
}

function flash(el) {
  const v = el && el.closest(".v");
  if (!v || reducedMotion.matches) return;
  v.classList.remove("changed");
  void v.offsetWidth;
  v.classList.add("changed");
}

document.addEventListener("animationend", (e) => {
  if (e.target.classList) e.target.classList.remove("enter", "changed");
});

function setPanel(name, html) {
  const box = name === "portfolio" ? $("#portfolio") : $("#ledger");
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  const next = document.createElement("div");
  next.append(tpl.content);
  morph(box, next);
  drawCharts(box);
  focusable(box);
  layout();
  markOpeners();
  if (name === "ledger") markRank();
}

// the events in rank: by expected move, or worst case first; with `glide`, each moves to its place
function rankRows(glide) {
  const ol = $("#ledger ol.events");
  if (!ol) return;
  const rows = $$(":scope > li[data-k]", ol);
  const key = S.rank === "worst" ? "worst" : "emove";
  const sorted = rows.slice().sort((a, b) => (+a.dataset[key]) - (+b.dataset[key]));
  if (sorted.every((r, k) => r === rows[k])) return;
  const moving = glide && !reducedMotion.matches;
  const before = moving ? new Map(rows.map((r) => [r, r.getBoundingClientRect().top])) : null;
  for (const r of sorted) ol.append(r);
  if (!moving) return;
  for (const r of sorted) {
    const dy = before.get(r) - r.getBoundingClientRect().top;
    if (dy && !r.classList.contains("enter")) {
      r.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: 520, easing: "cubic-bezier(.2,.7,.2,1)" });
    }
  }
}

function markRank() {
  $("#ledger").dataset.rank = S.rank;  // each line shows the figure the ledger is ranked by
  for (const b of $$("#ledger [data-rank]")) b.setAttribute("aria-pressed", String(b.dataset.rank === S.rank));
}

$("#ledger").addEventListener("click", (e) => {
  const b = e.target.closest("[data-rank]");
  if (!b) return;
  e.preventDefault();
  S.rank = b.dataset.rank;
  markRank();
  rankRows(true);
});

// a list shown in part: the rest of it, on request
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-all]");
  if (!b) return;
  e.preventDefault();
  b.closest("table, ul").classList.add("all");
});

// text shown in part, a company's profile or a note: the whole of it, on request
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-more]");
  if (!b) return;
  e.preventDefault();
  b.closest(".profile, .fold").classList.add("open");
});

// a fold whose text fits shows it whole, with no button
function folds(root) {
  for (const f of $$(".fold", root)) {
    const inner = $(".fold-in", f);
    f.classList.toggle("fits", !f.classList.contains("open") && inner.scrollHeight <= inner.clientHeight + 2);
  }
}

// ---------------------------------------------------------------- the right panel: a trail of steps

// The trail starts at the ledger. What opens from inside the open view goes a step deeper; what opens from the
// portfolio or the conversation starts the trail again; what is already on the trail returns to its step. Back
// returns one step, and a step returned to opens where it was left, at its scroll position, while its figures are
// read again. Every response is for one navigation: one that a later navigation, Back, a breadcrumb, or a new
// conversation replaced is dropped. One thing is marked as selected (markOpeners).

const SCOPES = { portfolio: "#portfolio", ledger: "#ledger", detail: "#detail", conversation: "#conversation" };

function scopeOf(el) {
  for (const [name, sel] of Object.entries(SCOPES)) if (el.closest(sel)) return name;
  return null;
}

function opener(el) {
  return el ? { el, go: el.dataset.go, scope: scopeOf(el) } : null;
}

// the view on screen keeps its scroll position: a step its own, the ledger the ledger's
function keepPlace() {
  const at = $("#right-scroll").scrollTop;
  if (S.shown > 0 && S.trail[S.shown - 1]) S.trail[S.shown - 1].scroll = at;
  else S.ledgerScroll = at;
}

async function openStep(target, title, { push = true, from = null } = {}) {
  if (!S.state.store) return;
  const token = ++S.nav;
  const seq = S.storeSeq;
  keepPlace();
  if (push) {
    // what opens from inside the open view goes a step deeper; what opens from the portfolio or the conversation
    // starts the trail again, so one thing is selected at a time; and what is already on the trail returns to it
    const depth = from && scopeOf(from) === "detail" ? S.shown : 0;
    const again = S.trail.slice(0, depth).findIndex((s) => s.target === target);
    if (again >= 0) { returnTo(again); return; }
    S.trail = S.trail.slice(0, depth);
    S.trail.push({ target, title: title || target, opener: opener(from) });
    S.left = null;
  }
  const step = S.trail[S.trail.length - 1];
  renderTrail();
  markOpeners();
  try {
    const got = await api(`/api/view?id=${encodeURIComponent(target)}`);
    if (token !== S.nav) return;  // a later navigation replaced this one
    step.title = got.title || step.title;
    showStep(step, got.html, 0);
    if (seq !== S.storeSeq) refreshStep();  // the store changed while it loaded: its figures are read again
  } catch (e) {
    if (token !== S.nav) return;
    notice(e.message);
    if (push) { S.trail = S.trail.slice(0, S.shown); renderTrail(); markOpeners(); }  // the view on screen stays
    return;
  }
  if (narrow()) { $("#app").classList.remove("show-left"); $("#app").classList.add("show-right"); }
}

function showStep(step, html, scroll) {
  const detail = $("#detail");
  step.html = html;
  detail.innerHTML = html;
  decorate(detail);
  drawCharts(detail);
  requestAnimationFrame(() => folds(detail));
  detail.hidden = false;
  $("#ledger").hidden = true;
  S.shown = S.trail.indexOf(step) + 1;
  renderTrail();
  layout();
  markOpeners();
  const box = $("#right-scroll");
  box.scrollTop = scroll;
  requestAnimationFrame(() => { box.scrollTop = scroll; });  // once its charts and folds have their height
  const mark = !scroll && $("mark", detail);  // a passage, marked in its page, on its first opening
  if (mark) requestAnimationFrame(() => mark.scrollIntoView({ block: "center" }));
  if (S.keyboard) detail.focus({ preventScroll: true });
}

function showLedger() {
  $("#detail").hidden = true;
  $("#detail").innerHTML = "";
  $("#ledger").hidden = false;
  S.shown = 0;
  renderTrail();
  layout();
  markOpeners();
  const box = $("#right-scroll");
  box.scrollTop = S.ledgerScroll || 0;
  if (narrow() && !$("#ledger").innerHTML.trim()) $("#app").classList.remove("show-right");
}

// return to step k of the trail, or to the ledger at -1, as it was left; its figures are then read again
function returnTo(k) {
  S.nav++;
  keepPlace();
  const left = S.trail[k + 1];
  S.left = left ? left.opener : null;
  S.trail = S.trail.slice(0, k + 1);
  if (k < 0) showLedger();
  else showStep(S.trail[k], S.trail[k].html || "", S.trail[k].scroll || 0);
  if (k >= 0) refreshStep();
  if (S.keyboard) focusOpener(S.left);
}

function back() {
  if (S.trail.length > S.shown) {  // a step still loading: it is dropped, and the view on screen stays
    S.nav++;
    S.trail = S.trail.slice(0, S.shown);
    renderTrail();
    markOpeners();
    return;
  }
  returnTo(S.shown - 2);
}

// a new conversation: the trail goes, and the ledger shows from its top
function resetTrail() {
  S.nav++;
  S.trail = [];
  S.left = null;
  S.ledgerScroll = 0;
  showLedger();
}

async function refreshStep() {
  const step = S.trail[S.shown - 1];
  if (!S.shown || !step) return;
  const token = S.nav, seq = ++S.refreshSeq;
  try {
    const got = await api(`/api/view?id=${encodeURIComponent(step.target)}`);
    if (token !== S.nav || seq !== S.refreshSeq || S.trail[S.shown - 1] !== step) return;  // superseded
    step.html = got.html;
    step.title = got.title || step.title;
    const tpl = document.createElement("template");
    tpl.innerHTML = got.html;
    const next = document.createElement("div");
    next.append(tpl.content);
    morph($("#detail"), next);
    decorate($("#detail"));
    drawCharts($("#detail"));
    requestAnimationFrame(() => folds($("#detail")));
    renderTrail();
    markOpeners();
  } catch { /* the step stays as it was */ }
}

function renderTrail() {
  const crumbs = $("#crumbs");
  // the ledger is one click away from every step
  const all = [{ title: "Events" }, ...S.trail];
  crumbs.innerHTML = all.map((s, k) =>
    `${k ? '<span class="sep">/</span>' : ""}<button type="button" data-crumb="${k - 1}"${k === S.shown ? ' aria-current="page"' : ""}>${esc(s.title)}</button>`).join("");
  $("#trail").hidden = !S.trail.length;
}

$("#crumbs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-crumb]");
  if (!b) return;
  const k = +b.dataset.crumb;
  if (k === S.shown - 1 && S.trail.length === S.shown) return;  // the step on screen
  returnTo(k);
});

$("#back").addEventListener("click", back);

// One thing is marked as selected: what started the open trail from the portfolio or the conversation, while the
// trail is open; and, in a view returned to, what opened the step just left there, so the place is kept.
function markOpeners() {
  for (const el of $$("[data-opened]")) { el.removeAttribute("data-opened"); el.removeAttribute("aria-current"); }
  const shown = S.shown > 0 ? "detail" : "ledger";
  const root = S.shown > 0 && S.trail[0] ? S.trail[0].opener : null;
  const marks = [];
  if (root && (root.scope === "portfolio" || root.scope === "conversation")) marks.push(root);
  if (S.left && S.left.scope === shown) marks.push(S.left);
  for (const o of marks) {
    const el = findOpener(o);
    if (el) { el.dataset.opened = ""; el.setAttribute("aria-current", "true"); }
  }
}

function findOpener(o) {
  if (o.el && o.el.isConnected && o.el.closest(SCOPES[o.scope])) return o.el;
  const root = $(SCOPES[o.scope]);
  o.el = root ? root.querySelector(`[data-go="${CSS.escape(o.go)}"]`) : null;
  return o.el;
}

function focusOpener(o) {
  const el = o && findOpener(o);
  if (el && el.offsetParent !== null) el.focus({ preventScroll: false });
  else ($("#detail").hidden ? $("#ledger") : $("#detail")).focus({ preventScroll: true });
}

// a pointer to a note shown once, further down the same panel: it scrolls there and marks it a moment
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-to]");
  if (!a) return;
  e.preventDefault();
  const note = $(`#detail [data-note="${CSS.escape(a.dataset.to)}"]`);
  if (!note) return;
  $(".fold", note)?.classList.add("open");
  note.scrollIntoView({ block: "center", behavior: reducedMotion.matches ? "auto" : "smooth" });
  note.classList.remove("marked");
  void note.offsetWidth;
  note.classList.add("marked");
});

document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-go]");
  if (!a || a.closest("#menu")) return;
  e.preventDefault();
  S.keyboard = e.detail === 0;  // a click the keyboard made
  // what the breadcrumb says until the step's own title arrives: an event's title, a holding's name, the figure
  const named = $(".chip-t, .et, .tk", a);
  const label = (named ? named.textContent : a.textContent).trim() || a.dataset.go;
  openStep(a.dataset.go, label.slice(0, 60), { from: a });
});

// Every figure, name, and event that opens a step is reached and opened from the keyboard: a link with no href is
// made one, Enter opens it, and focus shows.
function focusable(root = document) {
  for (const a of $$("[data-go]", root)) {
    if (a.hasAttribute("href") || a.closest("#menu")) continue;
    if (a.getAttribute("tabindex") !== "0") a.setAttribute("tabindex", "0");
    if (a.getAttribute("role") !== "link") a.setAttribute("role", "link");
  }
}

document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.isComposing) return;
  const a = e.target instanceof Element && e.target.closest("[data-go]");
  if (!a || a !== e.target || a.hasAttribute("href")) return;
  e.preventDefault();
  a.click();
});

document.addEventListener("pointerdown", () => { S.keyboard = false; }, true);
document.addEventListener("keydown", (e) => { if (e.key === "Tab" || e.key === "Enter" || e.key === " ") S.keyboard = true; }, true);

// ---------------------------------------------------------------- layout and narrow windows

// On a window narrower than a laptop's both panels fold into toggles (style.css, max-width 1100px). Where both panels
// would leave the conversation narrower than a comfortable reading width, about 560px, the portfolio folds into its
// toggle and the events stay docked: the panels and their padding take 788px, so below 1350px (`fold-left`).
const NARROW = matchMedia("(max-width: 1100px)");
const CROWDED = matchMedia("(max-width: 1349px)");
const narrow = () => NARROW.matches;

function layout() {
  const app = $("#app");
  const hasLeft = !!$("#portfolio").innerHTML.trim();
  const hasRight = !!$("#ledger").innerHTML.trim() || S.trail.length > 0;
  app.classList.toggle("has-left", hasLeft);
  app.classList.toggle("has-right", hasRight);
  const foldLeft = !narrow() && CROWDED.matches && hasLeft && hasRight;
  app.classList.toggle("fold-left", foldLeft);
  const left = hasLeft && (narrow() || foldLeft), right = hasRight && narrow();
  $('[data-toggle="left"]').hidden = !left;
  $('[data-toggle="right"]').hidden = !right;
  if (!left) app.classList.remove("show-left");
  if (!right) app.classList.remove("show-right");
}

NARROW.addEventListener("change", layout);
CROWDED.addEventListener("change", layout);
const folded = () => narrow() || $("#app").classList.contains("fold-left");

$("#toggles").addEventListener("click", (e) => {
  const b = e.target.closest("[data-toggle]");
  if (!b) return;
  const side = b.dataset.toggle;
  const app = $("#app");
  const other = side === "left" ? "right" : "left";
  app.classList.remove(`show-${other}`);
  app.classList.toggle(`show-${side}`);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { $("#app").classList.remove("show-left", "show-right"); closeMenu(); }
});

$("#center").addEventListener("click", (e) => {
  if (!folded() || e.target.closest("#toggles, [data-go]")) return;
  $("#app").classList.remove("show-left", "show-right");
});

// ---------------------------------------------------------------- the menu

const menuButton = $("#menu-button");
const menuPop = $("#menu-pop");

menuButton.addEventListener("click", (e) => {
  e.stopPropagation();
  const open = menuPop.hidden;
  menuPop.hidden = !open;
  menuButton.setAttribute("aria-expanded", String(open));
  $("#stores").hidden = true;
});

document.addEventListener("click", (e) => {
  if (!e.target.closest("#menu")) { menuPop.hidden = true; menuButton.setAttribute("aria-expanded", "false"); }
});

menuPop.addEventListener("click", async (e) => {
  const item = e.target.closest("[data-menu], [data-store]");
  if (!item) return;
  e.stopPropagation();
  try {
    if (item.dataset.menu === "new") { closeMenu(); await post("/api/new"); }
    else if (item.dataset.menu === "download") { closeMenu(); location.href = "/api/download"; }
    else if (item.dataset.menu === "open") {
      const list = await api("/api/stores");
      const box = $("#stores");
      box.innerHTML = list.length ? list.map((s) =>
        `<button type="button" data-store="${esc(s.name)}"><span>${esc(s.name)}</span><span class="n">${s.events} event${s.events === 1 ? "" : "s"}</span></button>`).join("")
        : '<div class="empty">No saved conversations yet.</div>';
      box.hidden = false;
    } else if (item.dataset.store) { closeMenu(); await post("/api/open", { name: item.dataset.store }); }
  } catch (err) {
    notice(err.message);
  }
});

function closeMenu() {
  menuPop.hidden = true;
  menuButton.setAttribute("aria-expanded", "false");
}

// ---------------------------------------------------------------- events from the server

let refreshTimer = null;
function storeChanged() {
  S.storeSeq++;
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    if (S.shown) refreshStep();
    wantChips(true);
  }, 300);
}

// a quiet line above the composer: the server out of reach, with a way to try again, or the stream reconnecting
function status(text, action) {
  const el = $("#status");
  el.innerHTML = text ? `${esc(text)}${action ? ` <button class="linkish" type="button" data-status>${esc(action.label)}</button>` : ""}` : "";
  el.hidden = !text;
  el._action = action ? action.run : null;
}

$("#status").addEventListener("click", (e) => {
  if (e.target.closest("[data-status]") && $("#status")._action) $("#status")._action();
});

async function start() {
  if (!$("#status").hidden) status("Connecting…");  // trying again
  try {
    await loadAll();
    S.loaded = true;
    status("");
  } catch {
    status("The page can't reach its server.", { label: "Try again", run: start });
  }
}

let stream = null;
function connect() {
  const es = stream = new EventSource("/api/events");
  let first = true;
  es.onopen = () => {
    if (!first || !S.loaded) start();  // a reconnection reads everything again, and so does a start that failed
    else status("");
    first = false;
  };
  es.onerror = () => {
    if (S.loaded) status("Reconnecting…");
    if (es.readyState === EventSource.CLOSED && stream === es) setTimeout(connect, 3000);  // the browser gave up: again
  };
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "state") applyState(ev);
    else if (ev.type === "analyst") onAnalyst(ev.event);
    else if (ev.type === "progress") onProgress(ev);
    else if (ev.type === "panel") setPanel(ev.name, ev.html);
    else if (ev.type === "store") storeChanged();
    else if (ev.type === "rank") rankRows(true);
    else if (ev.type === "conversation") { S.chips.clear(); chipAsked.clear(); resetTrail(); loadConversation(); }
  };
}

async function loadAll() {
  const [state, panels] = await Promise.all([api("/api/state"), api("/api/panels")]);
  applyState(state);
  setPanel("portfolio", panels.portfolio || "");
  setPanel("ledger", panels.ledger || "");
  rankRows(false);
  await loadConversation();
}

S.stick = true;
$("#scroll").addEventListener("scroll", () => { S.stick = nearBottom(); });
connect();
start();
input.focus();
