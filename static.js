// The page's server, for a finished conversation published as files (tools/site/export.py): every request the page
// makes of /api is answered from what the export wrote under data/, which is what the page's own server answered.
// The page's own code runs unchanged after this file; nothing here draws anything.
(() => {
  "use strict";
  const base = new URL(".", document.currentScript.src).href;
  const net = window.fetch.bind(window);
  const read = (rel) => net(base + rel).then((r) => {
    if (!r.ok) throw new Error(`${rel} answered ${r.status}`);
    return r.json();
  });
  const site = read("data/site.json");
  const answer = (obj, status = 200) =>
    new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

  // `live`: what the page reads while the replay plays, in place of the finished conversation's answers.
  // `emit`: an event as the server would send it, to the page's own handler.
  const SITE = (window.SITE = {
    base, read, site, live: null, stream: null,
    emit(event) {
      const s = SITE.stream;
      if (s && s.onmessage) s.onmessage({ data: JSON.stringify(event) });
    },
  });

  async function api(url, init) {
    const d = await site, q = url.searchParams, path = url.pathname, now = SITE.live;
    if (init && init.method === "POST") return answer({ error: d.state.missing[0] || "This example is read-only." }, 409);
    if (path === "/api/state") return answer(now ? now.state : d.state);
    if (path === "/api/panels") return answer(now ? now.panels : d.panels);
    if (path === "/api/conversation") return answer(now ? now.conversation : d.conversation);
    if (path === "/api/stores") return answer([]);
    if (path === "/api/chips") {
      const out = {};
      for (const id of (q.get("ids") || "").split(",").filter(Boolean)) {
        out[id] = d.chips[id] || { label: id, value: "", exists: false };
      }
      return answer(out);
    }
    if (path === "/api/view") {
      const n = d.views[q.get("id") || ""];
      return answer(await read(`data/view/${n === undefined ? "none" : n}.json`));
    }
    if (path === "/api/call") return answer(await read(`data/call/${Number(q.get("n"))}.json`));
    return answer({ error: "not found" }, 404);
  }

  window.fetch = (input, init) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    return url.pathname.startsWith("/api/") ? api(url, init) : net(input, init);
  };

  // The server's stream of events: none arrive on a finished conversation; the replay sends its own (SITE.emit).
  window.EventSource = class {
    constructor() { this.readyState = 1; SITE.stream = this; }
    close() { this.readyState = 2; }
    static get CLOSED() { return 2; }
  };
})();
