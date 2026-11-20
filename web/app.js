// The page's browser code: our own small layer, no framework and no build step. It streams the
// conversation, merges each updated panel into place, and opens what the member opens beside the conversation, a
// figure's working beside the view it came from.
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
  seen: new Map(),  // each view as it was last shown, by its target: what opening it again shows at once
  live: null,       // the turn being written: {turn, el, work, text}
  files: [],        // attachments waiting in the composer
  labels: {},       // what a member calls each id a call names
  rank: "volatility",    // the ledger's order: by volatility, or worst outcome first
  rows: "moves",         // the assumptions grid's lower rows: each price's move, or each price's level there
  tab: "outcomes",       // an opened event's view: its outcomes, its probability, or its assumptions
  payoff: "now",         // payoff card mode: each outcome's P&L today ("now") or the distribution on the event's date ("dated")
  stage: "portfolio",    // active main workspace tab: the portfolio until the first event, then "events" once
  stagePicked: false,    // whether the member explicitly chose a workspace tab
  chatClosed: false,     // whether the right analyst rail is collapsed on wide screens
  weightBy: "company",   // which list of where the portfolio's weight is the portfolio panel shows
  outcome: null,         // the outcome selected, {event, id}: marked in the picture, each grid and the history
  stick: true,           // whether the conversation follows its newest line: it does while it is at its end
};

// ---------------------------------------------------------------- small helpers

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// A request that did not give the page what it asked for: no answer came, so the server is out of reach, or the server
// answered with a refusal or a failure, in its own words. Anything else thrown while the page works is its own script's
// error, and is left to the browser, which reports it with its file and line.
class RequestError extends Error {}
const UNREACHABLE = "The page can't reach its server.";

async function request(path, init) {
  let r;
  try {
    r = await fetch(path, init);
  } catch {  // no answer came
    throw new RequestError(UNREACHABLE);
  }
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new RequestError(j.error || r.statusText || `${path} answered ${r.status}`);
  }
  return r;
}

async function api(path) {
  return (await request(path, { cache: "no-store" })).json();
}

async function post(path, body = {}) {
  const r = await request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json().catch(() => ({}));
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

// a link relative to the page a text was read from (md's `base`), as an address of its own; none where there is no page
let mdBase = null;
function absolute(href) {
  if (!mdBase) return null;
  try {
    const u = new URL(href.replace(/&amp;/g, "&"), mdBase);
    return /^https?:$/.test(u.protocol) ? u.href : null;
  } catch { return null; }
}

function inline(text) {
  const codes = [], lit = [];
  let s = text.replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
  s = s.replace(/\\([!-\/:-@\[-`{-~])/g, (_, c) => `\u0001${lit.push(c) - 1}\u0001`);  // a character markdown escapes
  // a picture: a page's text keeps its words and not its pictures, as its passages are read (store.words)
  s = s.replace(/!\[[^\]]*\]\([^)]*\)/g, "").replace(/\[\s*\]\([^)]*\)/g, "");
  s = esc(s);
  // a link's words can hold brackets of their own, as a page's footnote does: [[24]](#ref-24)
  s = s.replace(/\[((?:[^\[\]]|\[[^\]]*\])+)\]\((https?:\/\/[^\s)]+)(?:\s+&quot;[^&]*&quot;)?\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  s = s.replace(/\[((?:[^\[\]]|\[[^\]]*\])+)\]\(([^\s)]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (_, t, href) => {
    const u = absolute(href);
    return u ? `<a href="${esc(u)}" target="_blank" rel="noopener">${t}</a>` : t;
  });
  // a signed figure stays whole: a line never breaks between its sign and its number
  s = s.replace(/(^|[\s(\[\/*_])([−+\-±~≈]\s?[$€£]?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|pp|bp|k|m|bn|x|×)(?![\w]))?)/g, '$1<span class="nw">$2</span>');
  s = s.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>").replace(/__([^_]+?)__/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*(?!\s)([^*]+?)\*(?!\w)/g, "$1<em>$2</em>").replace(/(^|[^_\w])_(?!\s)([^_]+?)_(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  s = s.replace(/\u0001(\d+)\u0001/g, (_, i) => esc(lit[+i]));
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
}

const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
// a figure, alone or as a range, with its sign, currency, and unit: "−$40.4k", "20%", "≥ 15%", "4.0%–32%"
const FIG = "[~≈±+\\-−≥≤<>]?\\s?[$€£]?\\d[\\d,]*(?:\\.\\d+)?\\s?(?:%|pp|bp|k|m|bn|x|×)?";
// a figure may carry one word before it, as "about −$35" does, and still read in a column of figures
const FIGURE = new RegExp(`^(?:[a-z]+\\s)?${FIG}(?:\\s?[–—-]\\s?${FIG})?(?:\\s?\\(${FIG}\\))?$`, "i");

function md(src, base) {
  if (base !== undefined) {
    const before = mdBase;
    mdBase = base || null;
    try { return md(src); } finally { mdBase = before; }
  }
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

// Where a passage quoted from a page begins and ends in its text, as the page's renderer finds it (render._page_text):
// U+E000 and U+E001 around a passage quoted, U+E002 and U+E003 around the one opened. Each stretch of text between
// them is marked, across whatever the markdown made of it, and the characters themselves go.
const QUOTE_MARK = /[\uE000-\uE003]/;
function markQuoted(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const texts = [];
  while (walker.nextNode()) texts.push(walker.currentNode);
  let quoted = 0, here = 0;
  for (const node of texts) {
    const s = node.nodeValue;
    if (!quoted && !here && !QUOTE_MARK.test(s)) continue;
    const frag = document.createDocumentFragment();
    let run = "";
    const flush = () => {
      if (!run) return;
      if (quoted || here) {
        const m = document.createElement("mark");
        if (here) m.className = "here";
        m.textContent = run;
        frag.append(m);
      } else frag.append(run);
      run = "";
    };
    for (const ch of s) {
      if (ch === "\uE000") { flush(); quoted++; }
      else if (ch === "\uE001") { flush(); quoted = Math.max(0, quoted - 1); }
      else if (ch === "\uE002") { flush(); here++; }
      else if (ch === "\uE003") { flush(); here = Math.max(0, here - 1); }
      else run += ch;
    }
    flush();
    node.replaceWith(frag);
  }
}

// ---------------------------------------------------------------- ids in the analyst's words, as chips

const TICKER = "[A-Z0-9][A-Z0-9.&\\-]*(?::[A-Z0-9.\\-]+)?";  // as harness/schema.py's TICKER: "M&M.NS" is a symbol
const ID = `(?:prop:\\d+|factor:\\d+|src:\\d+|note:\\d+|link:\\d+|cmp:\\d+|market:(?:polymarket_us|polymarket|kalshi):[A-Za-z0-9_.#\\-]+|metric:price:${TICKER}|metric:[a-z][a-z0-9_]*|name:${TICKER}|cash:[A-Z]{3}|industry:[a-z0-9]+(?:-[a-z0-9]+)*|country:[A-Z]{2})`;  // cash:{CURRENCY}, as schema.CASH_ID_RE; a group by its id (derive/owned.py)
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
  a.innerHTML = `<span class="chip-t">${esc(c.label)}</span>${c.value ? `<span class="chip-v${c.tone ? " " + c.tone : ""}">${esc(c.value)}</span>` : ""}`;
}

// `typing`: the text is still arriving, so an id at its very end may be part of a longer one, and waits
function decorate(root, typing = false) {
  for (const el of $$("[data-md]", root)) {  // a note, or a page's text: its markdown, rendered as a reply's is
    if (el._md === el.dataset.md) continue;
    el.innerHTML = md(el.dataset.md, el.dataset.base);
    if (QUOTE_MARK.test(el.dataset.md)) markQuoted(el);
    el._md = el.dataset.md;
  }
  for (const code of $$("code", root)) {
    if (code.closest("pre")) continue;
    const t = code.textContent.trim();
    if (ID_WHOLE.test(t)) code.replaceWith(chipEl(t));
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    // a page's own words are the page's: an id in them is no object of the conversation's
    acceptNode: (n) => n.parentElement.closest("pre, code, a, .chip, .id, .page") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
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
  let got;
  try {
    got = await api(`/api/chips?ids=${encodeURIComponent(ids.join(","))}`);
  } catch (e) {
    if (!(e instanceof RequestError)) throw e;
    for (const id of ids) chipAsked.delete(id);  // the chip keeps its id, and is asked for again, a little later each time
    chipRetry = Math.min(chipRetry ? chipRetry * 2 : 1000, 30000);
    setTimeout(() => fetchChips(refresh), chipRetry);
    return;
  }
  chipRetry = 0;
  for (const [id, c] of Object.entries(got)) S.chips.set(id, c);
  for (const a of $$("[data-chip]")) if (got[a.dataset.chip]) fillChip(a);
}

// ---------------------------------------------------------------- the work line

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
  Factor: "factors", Source: "pages", Note: "notes", Portfolio: "portfolio" };

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
      if (i.type === "Factor") return f.name ? `Adding the factor ${f.name}` : "Adding a factor";
      return "Adding to its work";
    case "link": {
      const r = i.relation;
      const a = said(i.from_id, null), b = said(i.to_id, null);
      if (r === "watches") return b ? `Adding ${b} to the events` : "Adding an event";
      if (r === "asks") return a && b ? `Matching ${a} to the market ${b}` : "Matching a question to a market";
      if (r === "implies") return a && b ? `Relating ${a} to ${b}` : "Relating two questions";
      if (r === "and" || r === "or" || r === "then") return a ? `Breaking ${a} into smaller questions` : "Breaking an event into smaller questions";
      if (r === "evidence") return a && b ? `Quoting ${b} for ${a}` : "Quoting a page";
      if (r === "consequence") return a && b ? `Estimating the shock to ${b} from ${a}` : "Estimating an event's shock to a price";
      if (r === "way") return a && b ? `Adding ${b} as a way ${a} can turn out` : "Adding a way an event can turn out";
      if (r === "worth") return a && b ? `Valuing ${b} in ${a}` : "Valuing a holding in an outcome";
      return a && b ? `Connecting ${a} and ${b}` : "Connecting two parts of its work";
    }
    case "note": {
      const on = said((i.about_ids || [])[0], null);
      return on ? `Writing a note on ${on}` : "Writing a note";
    }
    case "edit": return said(i.id, null) ? `Revising ${said(i.id)}` : "Revising its work";
    case "remove": return said(i.id, null) ? `Removing ${said(i.id)}` : "Removing part of its work";
    case "search_markets": return i.text ? `Searching the markets for “${i.text}”` : "Searching the markets";
    case "search_web": return i.text ? `Searching the web for “${i.text}”` : "Searching the web";
    case "fetch_source": return `Reading ${host(i.url)}`;
    case "search_company": {
      const what = RECORD[i.kind];
      return i.ticker ? `Reading ${i.ticker}'s ${what || "record"}` : "Reading a company's record";
    }
    case "exposure": return "Reading the portfolio's dollar beta to each factor";
    case "window_returns": {
      // the factors, and each company or fund it names
      const all = ["the factors", ...(i.names || []).map((n) => said(n, String(n).replace(/^name:/, "")))];
      const what = all.length > 1 ? `${all.slice(0, -1).join(", ")} and ${all.at(-1)}` : all[0];
      return i.start ? `Looking at how ${what} moved from ${i.start} to ${i.end}` : `Looking at how ${what} moved in a past stretch`;
    }
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
  if (p.stage === "industries") {  // where each company is listed, then its profile: its industry and its home
    return p.kind === "profile" && p.of ? `Reading each company's industry and country, ${p.done} of ${p.of}`
      : "Reading each company's industry and country";
  }
  if (p.stage === "prices") return `Loading two years of ${p.kind === "dividends" ? "dividends" : "prices"}, ${p.done} of ${p.of}`;
  if (p.stage === "exposures") return "Fitting the holdings' betas";
  if (p.stage === "distribution") return "Working out each event's outcomes";  // on each event's own date (MATH.md 1.8)
  if (p.stage === "reports") return "Reading the reports of the largest holdings";  // MATH.md 3.1, a company's reports
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
    let got = null;
    try {
      got = await api(`/api/call?n=${entry.call}`);
    } catch (e) {
      if (!(e instanceof RequestError)) throw e;  // the preview stands
    }
    if (got) {
      input = got.input;
      result = got.result !== undefined && got.result !== null ? got.result : got.error;
      error = got.result === undefined || got.result === null;
    }
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
    `<a class="file" href="/api/file?path=${encodeURIComponent(f.path)}" target="_blank" rel="noopener"${PDF.test(f.name) ? " data-view" : ""}>${FILE_ICON}${esc(f.name)}</a>`).join("");
  el.innerHTML = `<div class="you"><div class="bubble">${files ? `<div class="files">${files}</div>` : ""}<span class="said"></span></div>
    <div class="actions"><button class="linkish" data-act="edit" type="button" title="${EDIT_SAYS}">Edit</button></div></div><div class="reply"></div>
    <div class="actions after"><button class="linkish" data-act="retry" type="button" title="${RETRY_SAYS}">${RETRY_ICON}Retry</button></div>`;
  $(".said", el).textContent = turn.text;
  const reply = $(".reply", el);
  for (const item of turn.items) reply.append(itemEl(item));
  if (turn.stopped) reply.append(stoppedEl());
  if (turn.ended) reply.append(endedEl(turn.ended));
  decorate(reply);
  return el;
}

// A PDF the member sent opens in a viewer over the whole page, where the browser shows PDFs itself; any other file,
// and a PDF where the browser has no viewer of its own, opens by its link.
const PDF = /\.pdf$/i;
let viewer = null;

function openViewer(href, name) {
  if (!viewer) {
    viewer = document.createElement("dialog");
    viewer.id = "viewer";
    viewer.innerHTML = `<header><span class="v-name"></span><button type="button" class="icon v-close" title="Close" aria-label="Close">` +
      `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15"/></svg></button></header><iframe></iframe>`;
    viewer.addEventListener("click", (ev) => { if (ev.target === viewer || ev.target.closest(".v-close")) viewer.close(); });
    viewer.addEventListener("close", () => { $("iframe", viewer).src = "about:blank"; });
    document.body.append(viewer);
  }
  $(".v-name", viewer).textContent = name;
  const frame = $("iframe", viewer);
  frame.title = name;
  frame.src = href + "#toolbar=0&navpanes=0&view=FitH";
  viewer.showModal();
  $(".v-close", viewer).focus();
}

$("#conversation").addEventListener("click", (ev) => {
  const a = ev.target.closest("a.file[data-view]");
  if (!a || navigator.pdfViewerEnabled === false || ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
  ev.preventDefault();
  openViewer(a.getAttribute("href"), a.textContent);
});

// what Edit and Retry do, said where they are offered
const EDIT_SAYS = "Edit and resend this message. The conversation and ledger restart from this point.";
const RETRY_SAYS = "Resend this message. The conversation and ledger restart from this point.";
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

// a reply that ends without its whole answer, by why it ended (server.py ENDED)
const ENDED = {
  refusal: "The analyst declined to reply.",
  max_tokens: "The reply was cut off at its length limit.",
  model_context_window_exceeded: "The reply was cut off: the conversation filled the analyst's context window.",
};

function endedEl(why) {
  const d = document.createElement("div");
  d.className = "stopped";
  d.textContent = ENDED[why] || "";
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
  turns.forEach((el) => {
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
  resumeLive();
  requestAnimationFrame(() => toBottom(true));
}

// A page opened while a reply runs: the last stretch of that reply's work is the one under way, live as it is from
// its first event, and the analyst's next events continue it rather than start another line beneath it.
function resumeLive() {
  if (!S.state || !S.state.busy) return;
  const turn = S.turns[S.turns.length - 1];
  if (!turn || turn.session !== S.state.session) return;
  const el = $(`#conversation .turn[data-turn="${turn.turn}"][data-session="${turn.session}"]`);
  const last = (turn.items || [])[(turn.items || []).length - 1];
  const node = el && $(".reply", el) && $(".reply", el).lastElementChild;
  if (!last || last.kind !== "work" || !node || node._work !== last) return;
  last.live = true;
  last.status = last.status || "Thinking";
  S.live = { el, work: last, workEl: node, text: null, textEl: null };
  updateWork(node);
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
    case "assistant":
      if (ENDED[ev.stop_reason] && !L.el._turn.ended) {
        L.el._turn.ended = ev.stop_reason;
        if (L.textEl) { L.textEl.classList.add("partial"); typewriter(L.textEl, true); }
        $(".reply", L.el).append(endedEl(ev.stop_reason));
      }
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
  input.placeholder = !S.state.can_reply ? "" : S.turns.length ? "Message the analyst" : "Paste holdings or attach a statement";
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

// Where the figures shown are not the store's now (Page.stale): why, and the moment they are from, in New York as the
// page's other moments are, with its day where that is not today. Where no render has finished since the store
// opened, there is no earlier moment, and the first sentence is the line.
const STALE = {
  full: ["The disk is full, so nothing new can be saved.", "Figures are as of"],
  io: ["Nothing new can be saved.", "Figures are as of"],
  render: ["Figures couldn't be updated.", "They're as of"],
};
const NY_TIME = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
const NY_DAY = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short", day: "numeric" });

function staleLine(stale) {
  const words = stale && STALE[stale.why];
  if (!words) return "";
  const t = stale.figures_at ? new Date(stale.figures_at) : null;
  if (!t || isNaN(t)) return words[0];
  const at = NY_TIME.format(t) + (NY_DAY.format(t) === NY_DAY.format(new Date()) ? "" : ` on ${NY_DAY.format(t)}`);
  return `${words[0]} ${words[1]} ${at}.`;
}

function applyState(st) {
  S.state = st;
  $("#stop").hidden = !st.busy;
  // above the composer: where the figures are from an earlier moment, and a missing key's line where it stops
  // something, or a finished example's
  const stale = staleLine(st.stale);
  $("#keys").innerHTML = (stale ? `<div class="stale" role="status">${esc(stale)}</div>` : "")
    + (st.missing || []).map((l) => `<div>${esc(l)}</div>`).join("");
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
  syncTopbar();
  layout();
}

// ---------------------------------------------------------------- charts: one small line chart, drawn from its points

// a chart's points are [date, value]; its kind says how a value reads: a price in dollars, or a chance
const RANGES = [["1M", 1], ["6M", 6], ["1Y", 12], ["2Y", 24]];
const CW = 400, CH = 120;

function chartValue(kind, v) {
  if (kind === "chance") return `${(Math.round(v * 1000) / 10).toFixed(1)}%`;  // a probability: one decimal, always
  if (kind === "cents") {  // a market's price: whole cents, with a tenth where it has one
    const c = Math.round(v * 1000) / 10;
    return `${Number.isInteger(c) ? c.toFixed(0) : c.toFixed(1)}\u00a2`;
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
  for (const scale of $$(".pt", root)) {
    scale._w = scale.clientWidth;
    if (!scale._w) continue;
    for (const label of $$(".pt-v", scale)) {
      const width = label.parentElement.clientWidth;
      const x = width * parseFloat(label.style.left) / 100;
      label.classList.toggle("pt-l", x > width / 2 && x + label.offsetWidth + 2 > width);
    }
  }
  for (const el of $$("[data-chart]", root)) if (el._chart !== el.dataset.chart) drawChart(el);
  for (const el of $$("[data-dist]", root)) if (el._dist !== el.dataset.dist || el._w !== el.clientWidth) drawDist(el);
  for (const el of $$("[data-history]", root)) if (el._w !== el.clientWidth) el._history = null;
  applyRows();
  applyOutcome();
}

function drawChart(el) {
  el._chart = el.dataset.chart;
  const spec = JSON.parse(el.dataset.chart);
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
  const vals = pts.map((p) => p[1]);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 1e-9) { lo -= 0.5 * Math.abs(lo || 1) * 0.01; hi += 0.5 * Math.abs(hi || 1) * 0.01; }
  const right = 2, pad = 6;
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
  const box = $(".ch-plot", el);
  box.className = `ch-plot ${tone}`;
  box.innerHTML = `<svg viewBox="0 0 ${CW} ${CH}" preserveAspectRatio="none" aria-hidden="true">${marked}` +
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

// ---------------------------------------------------------------- an event's outcomes on its date

// The picture: one row for each outcome, its own range on the event's date at its own height, so a rare outcome's
// range reads as whole as a likely one's; its probability beside its title; its P&L were it known today a line, and
// its 5th to 95th percentiles beneath; then all of them together, quietly. Every row is the engine's draws counted
// on one set of bins and shares one dollar axis, today at zero; losses rust and gains teal either side of it. Each
// row's height is its own peak, which is drawing and no figure. Each is drawn at the width it has.
let clipSeq = 0;

function drawDist(el) {
  el._dist = el.dataset.dist;
  const w = el._w = el.clientWidth;
  if (!w) return;
  const spec = JSON.parse(el.dataset.dist);
  const X = (v) => ((v - spec.lo) / (spec.hi - spec.lo)) * w;
  const z = X(0), PH = 28, H = PH + 10;
  const centres = spec.edges.slice(0, -1).map((a, i) => (a + spec.edges[i + 1]) / 2);
  const shape = (mass) => {
    const top = Math.max(...mass) || 1;
    const pts = centres.map((c, i) => `L${X(c).toFixed(1)},${(PH - (mass[i] / top) * (PH - 2)).toFixed(1)}`).join("");
    return `M${X(spec.edges[0]).toFixed(1)},${PH}${pts}L${X(spec.edges[spec.edges.length - 1]).toFixed(1)},${PH}Z`;
  };
  const plot = (row, cls) => {
    const k = ++clipSeq, d = shape(row.mass);
    const spread = row.q05 != null && row.q95 != null
      ? `<path class="pr" d="M${X(row.q05).toFixed(1)},${PH + 3}v4M${X(row.q05).toFixed(1)},${PH + 5}H${X(row.q95).toFixed(1)}M${X(row.q95).toFixed(1)},${PH + 3}v4"/>` : "";
    const at = row.centre != null ? row.centre : row.mean;
    return `<svg class="dr dist-plot" style="width:${w}px;height:${H}px" viewBox="0 0 ${w} ${H}" aria-hidden="true">` +
      `<clipPath id="dl${k}"><rect x="0" y="0" width="${Math.max(z, 0).toFixed(1)}" height="${PH}"/></clipPath>` +
      `<clipPath id="dg${k}"><rect x="${z.toFixed(1)}" y="0" width="${Math.max(w - z, 0).toFixed(1)}" height="${PH}"/></clipPath>` +
      `<line class="base" x1="0" x2="${w}" y1="${PH}" y2="${PH}"/>` +
      (cls === "all" ? `<path class="m all" d="${d}"/>` :
        `<path class="m loss" d="${d}" clip-path="url(#dl${k})"/><path class="m gain" d="${d}" clip-path="url(#dg${k})"/>`) +
      `<path class="edge" d="${d}"/>` +
      `<line class="zero" x1="${z.toFixed(1)}" x2="${z.toFixed(1)}" y1="0" y2="${H}"/>` +
      (at != null ? `<line class="ctr" x1="${X(at).toFixed(1)}" x2="${X(at).toFixed(1)}" y1="2" y2="${PH}"/>` : "") +
      spread + "</svg>";
  };
  let html = "";
  for (const row of spec.rows) {
    html += `<div class="dist-row" data-outcome="${esc(row.id)}" tabindex="0"><div class="dist-l"><span class="dist-t">${esc(row.title)}</span>` +
      `${row.base ? '<span class="otag">baseline</span>' : ""}<span class="dist-p n">${esc(row.p)}</span></div>${plot(row, "")}</div>`;
  }
  if (spec.all && spec.all.mass) {
    html += `<div class="dist-row all"><div class="dist-l"><span class="dist-t">All outcomes</span></div>${plot(spec.all, "all")}</div>`;
  }
  const n = spec.ticks.length;
  html += `<div class="dist-axis">` + spec.ticks.map(([t, label], i) =>
    `<span class="${i === 0 ? "start" : i === n - 1 ? "end" : "mid"}" style="left:${(X(t) / w * 100).toFixed(3)}%">${esc(label)}</span>`).join("") + "</div>";
  el.innerHTML = html;
}

// The outcomes grid's lower rows, by the reader's choice: each price's move, each price's level, or each holding's
// P&L; the choice holds from one event to the next, and through a refresh.
function applyRows() {
  for (const t of $$("#detail table.og, #event-bg table.og")) {
    if (t.dataset.fixed) continue;  // a grid of one view shows it
    const scope = t.closest("[data-pane]") || t.closest(".ev") || document;
    const have = $$("button[data-rows]", scope).map((b) => b.dataset.rows);
    const want = have.includes(S.rows) ? S.rows : have[0];
    if (want) t.dataset.rows = want;
    for (const b of $$("button[data-rows]", scope)) b.setAttribute("aria-pressed", String(b.dataset.rows === want));
  }
  applyPayoff();
  applyTabs();
}

function applyPayoff() {
  for (const card of $$("#detail .po-card, #event-bg .po-card")) {
    const dated = !!$('[data-payoff-pane="dated"]', card);
    const mode = dated && S.payoff === "dated" ? "dated" : "now";
    for (const p of $$("[data-payoff-pane]", card)) p.hidden = p.dataset.payoffPane !== mode;
    for (const b of $$("button[data-payoff]", card)) b.setAttribute("aria-pressed", String(b.dataset.payoff === mode));
  }
}

// An opened event's view by the reader's choice, held from one event to the next
function applyTabs() {
  for (const ev of $$("#detail .ev.ws, #event-bg .ev.ws")) {
    ev.dataset.tab = S.tab;
    for (const b of $$("button[data-tab]", ev)) b.setAttribute("aria-selected", String(b.dataset.tab === S.tab));
  }
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("#detail button[data-tab], #event-bg button[data-tab]");
  if (!b) return;
  e.preventDefault();
  S.tab = b.dataset.tab;
  applyTabs();
  for (const el of $$("[data-history]")) el._history = null;
  drawCharts($("#event-bg"));
  drawCharts($("#detail"));
});

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-switch-tab]");
  if (!b) return;
  e.preventDefault();
  S.tab = b.dataset.switchTab;
  applyTabs();
  const nav = $("#detail .tabs") || $("#event-bg .tabs");
  if (nav) nav.scrollIntoView({ block: "nearest", behavior: reducedMotion.matches ? "auto" : "smooth" });
  drawCharts($("#event-bg"));
  drawCharts($("#detail"));
});

document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-payoff]");
  if (!b) return;
  e.preventDefault();
  S.payoff = b.dataset.payoff;
  applyPayoff();
  for (const el of $$("[data-dist]")) el._dist = null;
  drawCharts($("#event-bg"));
  drawCharts($("#detail"));
});

document.addEventListener("click", (e) => {
  const b = e.target.closest("#detail button[data-rows], #event-bg button[data-rows]");
  if (!b) return;
  e.preventDefault();
  S.rows = b.dataset.rows;
  applyRows();
});

// ---------------------------------------------------------------- a probability's history

// How the forecast got here: as it stood at each reply and now (`spec.ours`), each value standing until the next,
// drawn as steps on a time axis; the selected outcome's in its place, the forecast faint behind it; beside them each
// market that informs it, from its daily closes to now; and the market-implied range today at the right edge.
const HH = 120, HTOP = 8;

function stamp(iso) {
  return Date.parse(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
}

function when(t, span) {
  const d = new Date(t);
  const day = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return span < 2 * 864e5 ? `${day}, ${d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}` : day;
}

function pctTicks(lo, hi) {
  const span = hi - lo, steps = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.25];
  const step = steps.find((s) => span / s <= 4) || 0.25;
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 1e4) / 1e4);
  return out;
}

// the markets beside a history, each its own dash: a line, a dash, a dot
const DASHES = ["", "5 3", "1.2 2.6"];
function dashKey(i) {
  const d = DASHES[i % DASHES.length];
  return `<svg class="k-dash" viewBox="0 0 16 6" aria-hidden="true"><line x1="0" x2="16" y1="3" y2="3"${d ? ` stroke-dasharray="${d}"` : ""}/></svg>`;
}

function drawHistory(el) {
  const step = S.trail[S.shown - 1];
  const sel = S.outcome && step && S.outcome.event === step.target ? S.outcome.id : null;
  el._history = el.dataset.history + "|" + sel;
  el._w = el.clientWidth;
  if (!el._w) return;
  const style = getComputedStyle(el);
  const w = el._w - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  const spec = JSON.parse(el.dataset.history);
  const picked = sel && spec.outcomes[sel], main = picked ? picked.points : spec.ours;
  const lines = [main, ...(main !== spec.ours ? [spec.ours] : []), ...spec.markets.map((m) => m.points)];
  const all = lines.flat();
  let t0 = Math.min(...all.map((p) => stamp(p[0]))), t1 = Math.max(...all.map((p) => stamp(p[0])));
  if (t1 - t0 < 6e4) { t0 -= 3e4; t1 += 3e4; }
  const vals = all.map((p) => p[1]).concat(spec.band || []);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max(0.01, (hi - lo) * 0.12);
  lo = Math.max(0, lo - pad); hi = Math.min(1, hi + pad);
  const left = 40, right = spec.band ? 16 : 6, base = HTOP + HH;
  const x = (t) => left + ((stamp(t) - t0) / (t1 - t0)) * (w - left - right);
  const y = (v) => HTOP + ((hi - v) / (hi - lo)) * HH;
  const steps = (pts) => pts.map((p, i) => (i ? `H${x(p[0]).toFixed(1)}V${y(p[1]).toFixed(1)}` : `M${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`)).join("");
  const linear = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  let svg = "";
  for (const v of pctTicks(lo, hi)) {
    svg += `<line class="grid" x1="${left}" x2="${w - right}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"/>` +
      `<text class="tick" x="${left - 6}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end">${chartValue("chance", v)}</text>`;
  }
  // each market its own line, told apart by its dash, the same dash beside its name below and its figure above
  spec.markets.forEach((m, i) => { svg += `<path class="mkt" d="${linear(m.points)}"${DASHES[i % DASHES.length] ? ` stroke-dasharray="${DASHES[i % DASHES.length]}"` : ""}><title>${esc(m.label)}</title></path>`; });
  if (main !== spec.ours) svg += `<path class="ours faint" d="${steps(spec.ours)}"/>`;
  svg += `<path class="ours" d="${steps(main)}"/>`;
  main.forEach((p, i) => {
    svg += `<circle class="ours${i === main.length - 1 ? " now" : ""}" cx="${x(p[0]).toFixed(1)}" cy="${y(p[1]).toFixed(1)}" r="3"/>`;
  });
  if (spec.band) svg += `<rect class="band" x="${(w - right + 6).toFixed(1)}" y="${y(spec.band[1]).toFixed(1)}" width="6" height="${Math.max(2, y(spec.band[0]) - y(spec.band[1])).toFixed(1)}"><title>Market range today</title></rect>`;
  svg += `<text class="tick" x="${left}" y="${base + 16}">${esc(when(t0, t1 - t0))}</text>` +
    `<text class="tick" x="${w - right}" y="${base + 16}" text-anchor="end">${esc(when(t1, t1 - t0))}</text>` +
    `<line class="cursor" y1="${HTOP}" y2="${base}" x1="-9" x2="-9"/>`;
  const names = [picked ? picked.title : "Forecast at each reply", ...spec.markets.map((m) => m.label)];
  el.innerHTML = `<div class="h-read"></div><svg class="dr" style="width:${w}px;height:${base + 22}px" viewBox="0 0 ${w} ${base + 22}">${svg}</svg>` +
    `<div class="h-key">${names.map((n, i) => i ? `<span class="k-mkt">${dashKey(i - 1)}${esc(n)}</span>` : `<span class="k-ours">${esc(n)}</span>`).join("")}` +
    `${picked ? '<span class="k-event">Event forecast at each reply</span>' : ""}</div>`;
  // what stood at a moment: each line's value as it stood then, the forecast a step that holds until the next reply
  const at = (pts, t) => { let v = null; for (const p of pts) if (stamp(p[0]) <= t) v = p[1]; return v; };
  const read = $(".h-read", el), cursor = $("line.cursor", el), svgEl = $("svg", el);
  const show = (t, hover) => {
    const own = main !== spec.ours ? "This outcome" : "Forecast";
    const parts = [["k-ours", own, at(main, t)],
      // "Market" is the event's own market; a market on what the event rests on is its dash, named in the key below
      ...spec.markets.map((m, i) => ["k-mkt", spec.markets.length > 1 || m.label !== m.short ? dashKey(i) : esc("Market"),
                                     at(m.points, t)])];
    read.innerHTML = `<span class="ch-d">${esc(when(t, t1 - t0))}</span>` + parts.filter(([, , v]) => v !== null)
      .map(([cls, n, v]) => ` <span class="${cls}">${cls === "k-ours" ? esc(n) : n} <span class="n">${chartValue("chance", v)}</span></span>`).join("");
    const cx = hover ? left + ((t - t0) / (t1 - t0)) * (w - left - right) : -9;
    cursor.setAttribute("x1", cx); cursor.setAttribute("x2", cx);
    cursor.style.visibility = hover ? "visible" : "hidden";  // drawn only where the pointer reads
  };
  show(t1, false);
  svgEl.onpointermove = (ev) => {
    const r = svgEl.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (ev.clientX - r.left - left) / (w - left - right)));
    show(t0 + f * (t1 - t0), true);
  };
  svgEl.onpointerleave = () => show(t1, false);
}

// One outcome is selected at a time, and shows as selected wherever it appears in the open event: its row in the
// picture, its column in each grid, and its probability's history.
function applyOutcome() {
  const step = activeEventStep() || S.trail[S.shown - 1];
  const id = S.outcome && step && S.outcome.event === step.target ? S.outcome.id : null;
  for (const el of $$("#detail [data-outcome], #event-bg [data-outcome]")) el.classList.toggle("sel", el.dataset.outcome === id);
  for (const el of $$("#detail [data-history], #event-bg [data-history]")) if (el._history !== el.dataset.history + "|" + id) drawHistory(el);
}

function selectOutcome(id) {
  const step = activeEventStep() || S.trail[S.shown - 1];
  if (!step) return;
  S.outcome = S.outcome && S.outcome.event === step.target && S.outcome.id === id ? null : { event: step.target, id };
  applyOutcome();
}

document.addEventListener("click", (e) => {
  const o = e.target.closest("#detail [data-outcome], #event-bg [data-outcome]");
  if (!o || e.target.closest("[data-go]")) return;
  selectOutcome(o.dataset.outcome);
});
document.addEventListener("keydown", (e) => {
  const o = e.target instanceof Element && e.target.matches("#detail [data-outcome][tabindex], #event-bg [data-outcome][tabindex]") ? e.target : null;
  if (!o || (e.key !== "Enter" && e.key !== " ")) return;
  e.preventDefault();
  selectOutcome(o.dataset.outcome);
});

// a drawing follows the width it has, as a panel opens, widens or folds
const resized = new ResizeObserver((entries) => {
  for (const { target } of entries) if (target._w !== target.clientWidth) drawCharts(target.parentElement || target);
});
const watchSizes = (root) => { for (const el of $$(".pt, [data-dist], [data-history]", root)) resized.observe(el); };

// ---------------------------------------------------------------- merging a panel into place

const TRANSIENT = ["enter", "changed", "all", "open"];

// A figure shown stays until its new value arrives. A step opened anew shows its placeholders: there is nothing
// shown before it.
const waits = (el) => el.matches(".wait") || !!el.querySelector(".wait");

// a figure on screen stays until its replacement is a figure: where the new rendering has only a placeholder in its
// place (a value worked out for the first time elsewhere on the page, or a render that could not give it), the element
// shown stays as it is, at the innermost element that holds the placeholder, so the figures around it still change
function keepsShown(live, next) {
  if (!waits(next) || waits(live)) return false;
  if (next.dataset.k) return ![...next.querySelectorAll("[data-k]")].some(waits);  // a keyed element inside it holds it
  return next.matches(".wait") || [...next.children].some((c) => c.matches(".wait"));
}

// `live` keeps its own attributes when it is the panel itself (`root`): only what is inside it is merged
function morph(live, next, root = true) {
  if (!root) syncAttrs(live, next);
  if (!root && next.dataset.md !== undefined && live._md === next.dataset.md) return;  // a note, rendered as it stands
  if (!root && next.dataset.chart !== undefined && live._chart === next.dataset.chart) return;  // a chart, drawn as it stands
  if (!root && next.dataset.dist !== undefined && live._dist === next.dataset.dist) return;  // a picture, drawn as it stands
  if (!root && next.dataset.history !== undefined && (live._history || "").startsWith(next.dataset.history + "|")) return;
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
      if (n.nodeType === 1 && keepsShown(match, n)) {
        // what is shown stays; the view read again (later) or the next render brings its new value
      } else if (n.nodeType === 3) {
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
  const first = name === "ledger" && !$("[data-ranked]", box);  // the list's first showing
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  const next = document.createElement("div");
  next.append(tpl.content);
  morph(box, next);
  drawCharts(box);
  watchSizes(box);
  focusable(box);
  if (name === "ledger") markRank();
  if (first && S.state && !S.state.busy) rankRows(false);
  if (name === "portfolio") markWeightBy();
  syncTopbar();
  syncEventRail();
  // until the member picks a view: the portfolio, where the first read shows, and Events once, when the first event
  // arrives; a view the member picks stays theirs
  if (!S.stagePicked) S.stage = $("#ledger ol.events > li.event") ? "events" : "portfolio";
  layout();
  markOpeners();
  if (name === "ledger" && S.stage === "matrix") loadMatrix();
}

// where the portfolio's weight is, one list at a time: by company, by industry, or by country
function markWeightBy() {
  $("#portfolio").dataset.wt = S.weightBy;
  for (const b of $$("#portfolio [data-wt]")) b.setAttribute("aria-pressed", String(b.dataset.wt === S.weightBy));
}

$("#portfolio").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-wt]");
  if (!b) return;
  e.preventDefault();
  S.weightBy = b.dataset.wt;
  markWeightBy();
});

// the events in rank: by volatility, or worst outcome first; with `glide`, each moves to its place. The ledger's list
// and By holding are each a list in rank, its rows carrying their place in both orders.
function rankRows(glide) {
  for (const ol of $$("#ledger [data-ranked], #detail [data-ranked], #matrix [data-ranked]")) rankList(ol, glide);
  syncEventRail();
}

function rankList(ol, glide) {
  const rows = $$(":scope > [data-k]", ol);
  const key = S.rank === "worst" ? "worst" : "volatility";
  const sorted = rows.slice().sort((a, b) => (+a.dataset[key]) - (+b.dataset[key]));
  if (sorted.every((r, k) => r === rows[k])) return;
  const moving = glide && !reducedMotion.matches;
  const before = moving ? new Map(rows.map((r) => [r, r.getBoundingClientRect().top])) : null;
  for (const r of sorted) ol.append(r);
  if (!moving) return;
  for (const r of sorted) {
    const dy = before.get(r) - r.getBoundingClientRect().top;
    if (dy && !r.classList.contains("enter")) {
      r.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: 420, easing: "cubic-bezier(.2,.7,.2,1)" });
    }
  }
}

function markRank() {
  for (const box of [$("#ledger"), $("#detail"), $("#matrix")]) if (box) box.dataset.rank = S.rank;
  for (const b of $$("[data-rank]")) if (b.tagName === "BUTTON") b.setAttribute("aria-pressed", String(b.dataset.rank === S.rank));
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-rank]");
  if (!b) return;
  e.preventDefault();
  S.rank = b.dataset.rank;
  markRank();
  rankRows(true);
});

// By holding's switch back to the list: the ledger, as it was left
document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-ledger]");
  if (!b) return;
  e.preventDefault();
  setStage("events", { manual: true });
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

// ---------------------------------------------------------------- workspace topbar, matrix & master-detail navigation

const SCOPES = {
  portfolio: "#portfolio",
  ledger: "#ledger",
  matrix: "#matrix",
  detail: "#detail",
  eventBg: "#event-bg",
  conversation: "#conversation",
  topbar: "#topbar",
};

function scopeOf(el) {
  for (const [name, sel] of Object.entries(SCOPES)) if (el && el.closest(sel)) return name;
  return null;
}

function opener(el) {
  return el ? { el, go: el.dataset.go, scope: scopeOf(el) } : null;
}

const isWatchedEvent = (id) => !!(id && $(`#ledger li.event[data-k="${CSS.escape(id)}"]`));
const activeEventStep = () => (S.shown >= 1 && S.trail[0] && S.trail[0].surface === "event" ? S.trail[0] : null);
const inDrawer = () => S.shown >= 1 && S.trail[S.shown - 1] && S.trail[S.shown - 1].surface !== "event";

function syncTopbar() {
  const st = S.state || {};
  $("#ws-name").textContent = st.store || "New portfolio";
  $("#tb-chat-dot").classList.toggle("live", !!st.busy);
  $("#chat-dot").classList.toggle("live", !!st.busy);

  const badge = $("#tb-badge");
  if (st.read_only) { badge.textContent = "Read-only"; badge.hidden = false; }
  else if (st.busy) { badge.textContent = "Working…"; badge.hidden = false; }
  else { badge.textContent = ""; badge.hidden = true; }

  $("#tb-export").hidden = !st.store;

  const ph = $("#portfolio header.ph");
  const metrics = $("#tb-metrics");
  if (ph && (ph.dataset.total || ph.dataset.asof)) {
    metrics.hidden = false;
    $("#tb-total").textContent = ph.dataset.total || "—";
    const mv = ph.dataset.move || "";
    const mvWrap = $("#tb-move-wrap"), mvEl = $("#tb-move");
    mvWrap.hidden = !mv;
    mvEl.textContent = mv;
    mvEl.className = `tb-val ${ph.dataset.moveTone || ""}`.trim();
    const vol = ph.dataset.vol || "";
    $("#tb-vol-wrap").hidden = !vol;
    $("#tb-vol").textContent = vol;
    $("#tb-asof").textContent = ph.dataset.asof || "";
    focusable(metrics);
  } else {
    metrics.hidden = true;
  }

  const lh = $("#ledger header.ph");
  const count = lh && lh.dataset.eventCount ? +lh.dataset.eventCount : $$("#ledger ol.events > li.event").length;
  const evBadge = $("#ev-count");
  evBadge.textContent = count ? String(count) : "";
  evBadge.hidden = !count;
}

function syncEventRail() {
  const list = $("#event-rail-list");
  if (!list) return;
  const items = $$("#ledger ol.events > li.event");
  const cur = activeEventStep()?.target || null;
  $("#rail-count").textContent = items.length ? `${items.length}` : "";
  list.innerHTML = items.map((li) => {
    const a = $("a.evline", li);
    if (!a) return "";
    const id = li.dataset.k || a.dataset.go || "";
    const title = a.dataset.title || $(".et", a)?.textContent || id;
    const prob = a.dataset.prob || "";
    const yes = a.dataset.yes || "";
    const tone = a.dataset.tone || "";
    return `<button type="button" class="rail-item${id === cur ? " active" : ""}" data-rail-event="${esc(id)}">` +
      `<span class="rail-t">${esc(title)}</span>` +
      `<span class="rail-figs"><span class="rail-p">${esc(prob)}</span>` +
      `<span class="${esc(tone)}">${esc(yes)}</span></span></button>`;
  }).join("");
}

$("#event-rail-list").addEventListener("click", (e) => {
  const b = e.target.closest("[data-rail-event]");
  if (!b) return;
  const id = b.dataset.railEvent;
  const title = $(".rail-t", b)?.textContent || id;
  openStep(id, title, { push: true, from: null });
});

$("#all-events").addEventListener("click", () => {
  returnTo(-1);
});

async function loadMatrix() {
  if (!S.state.store) return;
  try {
    const got = await api("/api/view?id=byholding");
    const box = $("#matrix");
    const tpl = document.createElement("template");
    tpl.innerHTML = got.html || "";
    const next = document.createElement("div");
    next.append(tpl.content);
    morph(box, next);
    decorate(box);
    markRank();
    for (const ol of $$("[data-ranked]", box)) rankList(ol, false);
    focusable(box);
  } catch (e) {
    if (!(e instanceof RequestError)) throw e;
  }
}

function setStage(stage, { manual = false } = {}) {
  if (manual) S.stagePicked = true;
  S.stage = stage;
  if (stage === "matrix") loadMatrix();
  layout();
}

$("#stage-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-stage]");
  if (!b) return;
  const targetStage = b.dataset.stage;
  if (targetStage === "events" && S.stage === "events" && activeEventStep()) {
    returnTo(-1);
    return;
  }
  if (inDrawer() && !activeEventStep()) closeInspector();
  setStage(targetStage, { manual: true });
});

function currentScroller() {
  if (inDrawer()) return $("#inspector-scroll");
  if (activeEventStep()) return $("#event-stage");
  return $("#ledger-wrap");
}

function keepPlace() {
  const scroller = currentScroller();
  const at = scroller ? scroller.scrollTop : 0;
  if (S.shown > 0 && S.trail[S.shown - 1]) S.trail[S.shown - 1].scroll = at;
  else S.ledgerScroll = at;
}

async function openStep(target, title, { push = true, from = null } = {}) {
  if (!S.state.store) return;
  if (target === "byholding") {
    if (inDrawer()) closeInspector();
    setStage("matrix", { manual: true });
    return;
  }
  const token = ++S.nav;
  const seq = S.storeSeq;
  keepPlace();
  const targetIsEvent = isWatchedEvent(target);
  if (push) {
    const sc = from ? scopeOf(from) : null;
    let depth = 0;
    if (targetIsEvent) {
      depth = 0;
    } else if (sc === "detail") {
      depth = S.shown;
    } else if (sc === "eventBg") {
      depth = 1;
    } else if (activeEventStep() && S.stage === "events") {
      depth = 1;
    }
    const again = S.trail.slice(0, depth).findIndex((s) => s.target === target);
    if (again >= 0) { returnTo(again); return; }
    S.trail = S.trail.slice(0, depth);
    S.trail.push({
      target,
      title: title || target,
      opener: opener(from),
      surface: targetIsEvent ? "event" : undefined,
    });
    S.left = null;
  }
  const step = S.trail[S.trail.length - 1];
  renderTrail();
  markOpeners();
  const seen = S.seen.get(target);
  if (seen) {
    step.title = seen.title || step.title;
    step.wide = seen.wide;
    step.surface = seen.surface || step.surface || (targetIsEvent ? "event" : "inspector");
    showStep(step, seen.html, 0);
    refreshStep();
    return;
  }
  const opening = setTimeout(() => { if (token === S.nav && S.trail.includes(step)) showOpening(step); }, 250);
  let got;
  try {
    got = await api(`/api/view?id=${encodeURIComponent(target)}`);
  } catch (e) {
    clearTimeout(opening);
    if (!(e instanceof RequestError)) throw e;
    if (token !== S.nav) return;
    notice(e.message);
    if (push) { S.trail = S.trail.slice(0, S.shown); renderTrail(); markOpeners(); }
    if (S.opening) restoreShown();
    return;
  }
  clearTimeout(opening);
  if (token !== S.nav) return;
  if (got.surface === "matrix") {
    S.trail = S.trail.slice(0, S.shown);
    setStage("matrix", { manual: true });
    return;
  }
  step.title = got.title || step.title;
  step.wide = !!got.wide;
  step.surface = got.surface || (targetIsEvent ? "event" : "inspector");
  showStep(step, got.html, 0);
  if (seq !== S.storeSeq) refreshStep();
  else readAgainLater(step, got);
}

function mountDetail(inInspector) {
  const trail = $("#trail");
  const detail = $("#detail");
  if (inInspector) {
    const head = $("#inspector-head");
    const closeBtn = $("#close-inspector");
    if (trail.parentElement !== head) head.insertBefore(trail, closeBtn);
    const sc = $("#inspector-scroll");
    if (detail.parentElement !== sc) sc.append(detail);
    $("#inspector").hidden = false;
  } else {
    const bar = $("#event-bar");
    if (trail.parentElement !== bar) bar.append(trail);
    const stage = $("#event-stage");
    if (detail.parentElement !== stage) stage.append(detail);
    $("#inspector").hidden = true;
  }
}

function syncEventBackground() {
  const ev = activeEventStep();
  const bg = $("#event-bg");
  if (!ev || !inDrawer() || !ev.html) {
    if (bg.dataset.target) { bg.innerHTML = ""; delete bg.dataset.target; }
    bg.hidden = true;
    return;
  }
  if (bg.dataset.target !== ev.target || bg._html !== ev.html) {
    bg.innerHTML = ev.html;
    bg._html = ev.html;
    bg.dataset.target = ev.target;
    decorate(bg);
    drawCharts(bg);
    watchSizes(bg);
    focusable(bg);
    requestAnimationFrame(() => {
      folds(bg);
      $("#event-stage").scrollTop = ev.scroll || 0;
    });
  }
  bg.hidden = false;
}

function showOpening(step) {
  const detail = $("#detail");
  S.opening = true;
  const asEvent = step.surface === "event" && S.trail.indexOf(step) === 0;
  if (asEvent) S.stage = "events";
  mountDetail(!asEvent);
  detail.innerHTML = `<p class="opening"><span class="wait" aria-hidden="true"></span><span>Opening ${esc(step.title)}</span></p>`;
  detail.hidden = false;
  layout();
}

function restoreShown() {
  S.opening = false;
  if (S.shown > 0) { const s = S.trail[S.shown - 1]; showStep(s, s.html || "", s.scroll || 0); } else showLedger();
}

// Keep a wide calculation table scrollable inside the drawer while its heading and evidence stay in place.
function detailContent(html, drawer) {
  const content = document.createElement("div");
  content.innerHTML = html;
  if (drawer) {
    for (const table of $$("table", content)) {
      const wrap = document.createElement("div");
      wrap.className = "drawer-table";
      table.replaceWith(wrap);
      wrap.append(table);
    }
  }
  return content;
}

function showStep(step, html, scroll) {
  const detail = $("#detail");
  S.opening = false;
  step.html = html;
  step.surface = step.surface || (isWatchedEvent(step.target) ? "event" : "inspector");
  S.seen.set(step.target, { html, title: step.title, wide: step.wide, surface: step.surface });
  S.shown = S.trail.indexOf(step) + 1;
  const drawer = step.surface !== "event" || S.shown > 1;
  if (!drawer) S.stage = "events";
  mountDetail(drawer);
  syncEventBackground();
  layout();
  detail.replaceChildren(...detailContent(html, drawer).childNodes);
  decorate(detail);
  drawCharts(detail);
  watchSizes(detail);
  focusable(detail);
  requestAnimationFrame(() => folds(detail));
  detail.hidden = false;
  markRank();
  rankRows(false);
  renderTrail();
  syncEventRail();
  markOpeners();
  const box = drawer ? $("#inspector-scroll") : $("#event-stage");
  box.scrollTop = scroll;
  requestAnimationFrame(() => { box.scrollTop = scroll; });
  // a passage opened, where its page's text shows it; a page's text whole, at the first passage quoted from it
  const mark = !scroll && ($("mark.here", detail) || $(".page mark", detail));
  if (mark) requestAnimationFrame(() => mark.scrollIntoView({ block: "center" }));
  if (S.keyboard) detail.focus({ preventScroll: true });
}

function showLedger() {
  S.opening = false;
  $("#detail").hidden = true;
  $("#detail").innerHTML = "";
  $("#event-bg").hidden = true;
  $("#event-bg").innerHTML = "";
  delete $("#event-bg").dataset.target;
  mountDetail(false);
  S.shown = 0;
  renderTrail();
  syncEventRail();
  layout();
  markOpeners();
  const box = $("#ledger-wrap");
  if (box) box.scrollTop = S.ledgerScroll || 0;
}

function closeInspector() {
  if (!inDrawer()) return;
  if (activeEventStep()) returnTo(0);
  else returnTo(-1);
}

$("#close-inspector").addEventListener("click", closeInspector);

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
  if (S.trail.length > S.shown) {
    S.nav++;
    S.trail = S.trail.slice(0, S.shown);
    renderTrail();
    markOpeners();
    if (S.opening) restoreShown();
    return;
  }
  returnTo(S.shown - 2);
}

function resetTrail() {
  S.nav++;
  S.trail = [];
  S.seen.clear();
  S.left = null;
  S.ledgerScroll = 0;
  S.stagePicked = false;
  showLedger();
}

async function refreshStep({ now = false } = {}) {
  const step = S.trail[S.shown - 1];
  if (!S.shown || !step || S.opening) return;
  const token = S.nav, seq = ++S.refreshSeq;
  let got;
  try {
    got = await api(`/api/view?id=${encodeURIComponent(step.target)}${now ? "&now=1" : ""}`);
  } catch (e) {
    if (!(e instanceof RequestError)) throw e;
    return;
  }
  if (token !== S.nav || seq !== S.refreshSeq || S.trail[S.shown - 1] !== step) return;
  step.html = got.html;
  step.title = got.title || step.title;
  step.wide = !!got.wide;
  step.surface = got.surface || step.surface;
  S.seen.set(step.target, { html: got.html, title: step.title, wide: step.wide, surface: step.surface });
  const detail = $("#detail");
  morph(detail, detailContent(got.html, !!detail.closest("#inspector")));
  decorate($("#detail"));
  for (const ol of $$("#detail [data-ranked]")) rankList(ol, false);
  drawCharts($("#detail"));
  watchSizes($("#detail"));
  focusable($("#detail"));
  requestAnimationFrame(() => folds($("#detail")));
  renderTrail();
  syncEventRail();
  markOpeners();
  readAgainLater(step, got);
}

let laterTimer = null;
function readAgainLater(step, got) {
  clearTimeout(laterTimer);
  if (got.work) { refreshStep({ now: true }); return; }
  if (!got.later) { step.laterWait = 0; return; }
  step.laterWait = Math.min(Math.max(500, (step.laterWait || 0) * 1.5), 5000);
  laterTimer = setTimeout(() => { if (S.trail[S.shown - 1] === step) refreshStep(); }, step.laterWait);
}

// the way back is one step, Back, to where the member came from: what each view is and what it belongs to, its own
// head says, so no path of the steps above it is drawn
function renderTrail() {
  $("#trail").hidden = !S.trail.length;
}

$("#back").addEventListener("click", back);

function markOpeners() {
  for (const el of $$("[data-opened]")) { el.removeAttribute("data-opened"); el.removeAttribute("aria-current"); }
  const shown = S.shown > 0 ? "detail" : "ledger";
  const root = S.shown > 0 && S.trail[0] ? S.trail[0].opener : null;
  const marks = [];
  if (root && (root.scope === "portfolio" || root.scope === "conversation" || root.scope === "matrix" || root.scope === "ledger")) marks.push(root);
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

document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-to]");
  if (!a) return;
  e.preventDefault();
  const note = $(`#detail [data-note="${CSS.escape(a.dataset.to)}"]`) || $(`#event-bg [data-note="${CSS.escape(a.dataset.to)}"]`);
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
  S.keyboard = e.detail === 0;
  const named = $(".chip-t, .et, .tk, .nm, .drv-nm", a);
  const label = (named ? named.textContent : a.textContent).trim() || a.dataset.go;
  const mark = e.target.closest("[data-outcome]");
  if (mark && a.contains(mark)) S.outcome = { event: a.dataset.go, id: mark.dataset.outcome };
  openStep(a.dataset.go, label.slice(0, 60), { from: a });
});

function focusable(root = document) {
  if (!root) return;
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

// ---------------------------------------------------------------- fixed-geometry layout & controls

const NARROW = matchMedia("(max-width: 1080px)");
const narrow = () => NARROW.matches;

function layout() {
  const app = $("#app");
  const hasPortfolio = !!$("#portfolio").innerHTML.trim();
  const hasEvents = !!$("#ledger").innerHTML.trim();
  app.classList.toggle("has-left", hasPortfolio);
  app.classList.toggle("has-right", hasEvents || S.trail.length > 0);
  app.classList.toggle("chat-closed", !narrow() && S.chatClosed);

  const stage = S.stage;
  for (const b of $$("#stage-tabs button[data-stage]")) {
    b.setAttribute("aria-selected", String(b.dataset.stage === stage));
  }
  $("#tab-matrix").hidden = !hasEvents;

  $("#pane-events").hidden = stage !== "events";
  $("#pane-matrix").hidden = stage !== "matrix";
  $("#pane-portfolio").hidden = stage !== "portfolio";

  const eventOpen = stage === "events" && (!!activeEventStep() || (S.opening && S.trail[0]?.surface === "event"));
  $("#event-split").hidden = !eventOpen;
  $("#ledger-wrap").hidden = eventOpen;
  $("#ledger-empty").hidden = hasEvents || !hasPortfolio;

  const toggleBtn = $("#toggle-chat");
  const chatShown = narrow() ? app.classList.contains("show-chat") : !S.chatClosed;
  toggleBtn.classList.toggle("active", chatShown);
  toggleBtn.setAttribute("aria-pressed", String(chatShown));
}

NARROW.addEventListener("change", () => {
  if (!narrow()) $("#app").classList.remove("show-chat");
  layout();
});
addEventListener("resize", layout);

function toggleChat() {
  const app = $("#app");
  if (narrow()) app.classList.toggle("show-chat");
  else S.chatClosed = !S.chatClosed;
  layout();
}

$("#toggle-chat").addEventListener("click", toggleChat);
$("#collapse-chat").addEventListener("click", toggleChat);
// The ledger saves as a file, which the server names; the page stays where it is.
function downloadLedger() {
  const a = document.createElement("a");
  a.href = "/api/download";
  a.download = "";
  document.body.append(a);
  a.click();
  a.remove();
}

$("#tb-export").addEventListener("click", downloadLedger);

document.addEventListener("keydown", (e) => {
  if (viewer && viewer.open) return;  // the viewer over the page takes its own keys, Escape closing it
  if ((e.metaKey || e.ctrlKey) && e.key === "/") {
    if (!$("#app").classList.contains("empty")) { e.preventDefault(); toggleChat(); }
    return;
  }
  if (e.key === "Escape") {
    closeMenu();
    if (narrow() && $("#app").classList.contains("show-chat")) {
      $("#app").classList.remove("show-chat");
      layout();
      return;
    }
    if (inDrawer()) closeInspector();
  }
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
    else if (item.dataset.menu === "download") { closeMenu(); downloadLedger(); }
    else if (item.dataset.menu === "open") {
      const list = await api("/api/stores");
      const box = $("#stores");
      box.innerHTML = list.length ? list.map((s) =>
        `<button type="button" data-store="${esc(s.name)}"><span>${esc(s.name)}</span><span class="n">${s.events} event${s.events === 1 ? "" : "s"}</span></button>`).join("")
        : '<div class="empty">No saved conversations yet.</div>';
      box.hidden = false;
    } else if (item.dataset.store) { closeMenu(); await post("/api/open", { name: item.dataset.store }); }
  } catch (err) {
    if (!(err instanceof RequestError)) throw err;
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
    if (S.stage === "matrix") loadMatrix();
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
  } catch (e) {
    if (!(e instanceof RequestError)) { status(""); throw e; }  // the page's own script failed: the browser reports it
    status(e.message, { label: "Try again", run: start });  // out of reach, or the server's own words
    return;
  }
  S.loaded = true;
  status("");
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

// Everything read again, as at the start and after a reconnection: the state, the panels, and the conversation; and
// the open view and every chip, whose values may have moved while the page was away. Another conversation, opened
// meanwhile, starts from its ledger.
async function loadAll() {
  const before = S.state && S.state.store;
  const [state, panels] = await Promise.all([api("/api/state"), api("/api/panels")]);
  applyState(state);
  setPanel("portfolio", panels.portfolio || "");
  setPanel("ledger", panels.ledger || "");
  rankRows(false);
  await loadConversation();
  if (S.loaded && before !== state.store) {
    S.chips.clear();
    chipAsked.clear();
    resetTrail();
  } else if (S.shown) {
    refreshStep();
  }
  wantChips(true);
}

$("#scroll").addEventListener("scroll", () => { S.stick = nearBottom(); });
connect();
start();
input.focus();
