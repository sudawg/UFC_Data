/* ============================================================================
   UFC_Data — shared client library.
   Plain script tag, no modules, ES5-compatible (matches docs/betting.html).
   Exposes a single global: window.UFC
   ========================================================================= */
(function (window, document) {
  "use strict";

  var UFC = {};
  window.UFC = UFC;

  /* ==========================================================================
     0. BASE PATH
     Resolved from this script's own URL (…/assets/app.js) so every page works
     no matter how deep in the tree it sits, and both from a file server root
     and from a GitHub Pages project subpath.
     ====================================================================== */
  var BASE = (function () {
    var s = document.currentScript;
    if (!s) {
      var all = document.getElementsByTagName("script");
      for (var i = all.length - 1; i >= 0; i--) {
        if (all[i].src && /assets\/app\.js(\?|#|$)/.test(all[i].src)) { s = all[i]; break; }
      }
    }
    if (s && s.src) return s.src.replace(/assets\/app\.js.*$/, "");
    /* last resort: current directory */
    return location.href.replace(/[^\/]*$/, "");
  })();

  UFC.base = BASE;
  UFC.url = function (rel) { return BASE + rel; };
  var DATA_BASE = BASE + "data/";

  /* ==========================================================================
     1. PREFERENCES (theme + odds format)
     ====================================================================== */
  var LS_THEME = "ufc:theme";
  var LS_FMT = "ufc:oddsformat";

  function lsGet(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { window.localStorage.setItem(k, v); } catch (e) {} }

  var ODDS_FORMATS = [
    { id: "american",   label: "American" },
    { id: "decimal",    label: "Decimal" },
    { id: "fractional", label: "Fractional" },
    { id: "return100",  label: "Return on $100" }
  ];
  UFC.oddsFormats = ODDS_FORMATS;

  var oddsFormat = (function () {
    var v = lsGet(LS_FMT);
    for (var i = 0; i < ODDS_FORMATS.length; i++) if (ODDS_FORMATS[i].id === v) return v;
    return "american";
  })();

  UFC.oddsFormat = function () { return oddsFormat; };
  UFC.setOddsFormat = function (id) {
    for (var i = 0; i < ODDS_FORMATS.length; i++) {
      if (ODDS_FORMATS[i].id === id) {
        oddsFormat = id; lsSet(LS_FMT, id);
        var sel = document.querySelector(".sitenav select.fmt-sel");
        if (sel && sel.value !== id) sel.value = id;
        repaint("oddsformat");
        return;
      }
    }
  };

  /* fired whenever something global changes that pages must redraw for
     (charts read CSS custom properties at draw time, so a theme flip has to
     re-run every draw* function on the page). */
  function repaint(reason) {
    var ev;
    try {
      ev = new CustomEvent("ufc:repaint", { detail: { reason: reason } });
    } catch (e) { /* very old browsers */
      ev = document.createEvent("CustomEvent");
      ev.initCustomEvent("ufc:repaint", false, false, { reason: reason });
    }
    window.dispatchEvent(ev);
  }
  UFC.repaint = repaint;

  /* theme ------------------------------------------------------------- */
  function systemTheme() {
    return (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
  }
  UFC.currentTheme = function () {
    return document.documentElement.getAttribute("data-theme") || systemTheme();
  };
  /* UFC.theme()      -> toggle light <-> dark
     UFC.theme("dark") -> set explicitly */
  UFC.theme = function (next) {
    if (!next) next = UFC.currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    lsSet(LS_THEME, next);
    syncThemeButton();
    repaint("theme");
    return next;
  };
  function syncThemeButton() {
    var btn = document.querySelector(".sitenav .theme-btn");
    if (!btn) return;
    var dark = UFC.currentTheme() === "dark";
    btn.textContent = dark ? "☀ Light" : "☾ Dark";
    btn.setAttribute("aria-label", "Switch to " + (dark ? "light" : "dark") + " theme");
    btn.setAttribute("aria-pressed", dark ? "true" : "false");
  }
  /* apply the stored theme immediately (this file is loaded in <head>, before
     the body paints, so there is no flash of the wrong palette). */
  (function () {
    var t = lsGet(LS_THEME);
    if (t === "dark" || t === "light") document.documentElement.setAttribute("data-theme", t);
  })();

  /* ==========================================================================
     2. NUMBER / DATE / ODDS FORMATTING
     ====================================================================== */
  function impliedProb(odds) {
    return odds < 0 ? (-odds) / (-odds + 100) : 100 / (odds + 100);
  }
  function americanProfit(odds, won) {
    if (won === null || won === undefined) return 0;
    if (!won) return -100;
    return odds > 0 ? odds : (10000 / Math.abs(odds));
  }
  function pct(x, d) { return (x * 100).toFixed(d === undefined ? 1 : d) + "%"; }
  function money(x) {
    var sign = x < 0 ? "-" : "+";
    return sign + "$" + Math.abs(Math.round(x)).toLocaleString("en-US");
  }
  function fmtOdds(o) { return (o > 0 ? "+" : "") + o; }

  UFC.impliedProb = impliedProb;
  UFC.americanProfit = americanProfit;
  UFC.pct = pct;
  UFC.money = money;
  UFC.fmtOdds = fmtOdds;
  UFC.DASH = "—";

  var MONTHS = ["January","February","March","April","May","June",
                "July","August","September","October","November","December"];
  /* "2026-05-16" -> "May 16, 2026" (string math only: no Date(), no timezones) */
  UFC.fmtDate = function (iso) {
    if (!iso) return UFC.DASH;
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    if (!m) return iso;
    return MONTHS[+m[2] - 1].slice(0, 3) + " " + (+m[3]) + ", " + m[1];
  };
  UFC.fmtDateLong = function (iso) {
    if (!iso) return UFC.DASH;
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    if (!m) return iso;
    return MONTHS[+m[2] - 1] + " " + (+m[3]) + ", " + m[1];
  };
  UFC.year = function (iso) { return iso ? +iso.slice(0, 4) : null; };

  function gcd(a, b) { while (b) { var t = a % b; a = b; b = t; } return a; }

  /* Format American closing odds in the user's chosen notation.
     null / undefined -> em dash (odds coverage is incomplete: never fake it). */
  UFC.odds = function (american, format) {
    if (american === null || american === undefined || isNaN(american)) return UFC.DASH;
    var fmt = format || oddsFormat;
    var o = +american;
    if (fmt === "decimal") {
      return ((o > 0 ? o / 100 : 100 / -o) + 1).toFixed(2);
    }
    if (fmt === "fractional") {
      var num, den;
      if (o > 0) { num = Math.round(o); den = 100; }
      else { num = 100; den = Math.round(-o); }
      var g = gcd(num, den) || 1;
      return (num / g) + "/" + (den / g);
    }
    if (fmt === "return100") {
      return "$" + americanProfit(o, true).toFixed(0);
    }
    return fmtOdds(o);
  };
  UFC.oddsFormatLabel = function () {
    for (var i = 0; i < ODDS_FORMATS.length; i++) if (ODDS_FORMATS[i].id === oddsFormat) return ODDS_FORMATS[i].label;
    return "American";
  };

  /* "22-1-0 (1 NC)" */
  UFC.record = function (f) {
    if (!f) return UFC.DASH;
    var base = (f.w | 0) + "-" + (f.l | 0) + "-" + (f.d | 0);
    return f.nc ? base + " (" + f.nc + " NC)" : base;
  };

  UFC.slugName = function (name) {
    return String(name).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  };

  /* ==========================================================================
     3. DATA LOADING (parallel fetch + in-memory cache)
     ====================================================================== */
  var cache = null, pending = null;

  function getJSON(name) {
    return fetch(DATA_BASE + name).then(function (r) {
      if (!r.ok) throw new Error(name + " " + r.status);
      return r.json();
    });
  }

  /* Resolves to:
     { fights, fighters, events, summary, referees, byFighter, generated } */
  UFC.load = function () {
    if (cache) return Promise.resolve(cache);
    if (pending) return pending;
    pending = Promise.all([
      getJSON("fights.json"),
      getJSON("fighters.json"),
      getJSON("events.json"),
      getJSON("summary.json")
    ]).then(function (res) {
      var fightsDoc = res[0], fightersDoc = res[1], eventsDoc = res[2], summary = res[3];
      var fights = fightsDoc.fights || [];
      var fighters = fightersDoc.fighters || [];
      var events = eventsDoc.events || [];

      /* fighter index -> their fights, oldest first (fights.json is in id order,
         which is chronological). */
      var byFighter = new Array(fighters.length);
      for (var i = 0; i < fights.length; i++) {
        var f = fights[i];
        if (!byFighter[f.a]) byFighter[f.a] = [];
        if (!byFighter[f.b]) byFighter[f.b] = [];
        byFighter[f.a].push(f);
        byFighter[f.b].push(f);
      }

      /* actual closing-odds coverage window, measured from the data itself so
         the "why is this a dash?" copy can never go stale after a re-scrape */
      var oMin = null, oMax = null, priced = 0;
      for (var j = 0; j < fights.length; j++) {
        var g = fights[j];
        if (g.oa === null || g.oa === undefined || g.ob === null || g.ob === undefined) continue;
        priced++;
        if (oMin === null || g.d < oMin) oMin = g.d;
        if (oMax === null || g.d > oMax) oMax = g.d;
      }
      if (oMin && oMax) {
        UFC.oddsCoverage = { from: oMin, to: oMax, priced: priced, total: fights.length };
        UFC.ODDS_COVERAGE_NOTE =
          "No closing line on record. Moneyline coverage runs " + UFC.fmtDate(oMin) + " to " +
          UFC.fmtDate(oMax) + " (" + priced.toLocaleString() + " of " + fights.length.toLocaleString() +
          " fights priced); fights outside that window — and some inside it — have no matched price.";
      }

      cache = {
        generated: fightsDoc.generated,
        fights: fights,
        fighters: fighters,
        events: events,
        summary: summary,
        referees: fightsDoc.referees || [],
        byFighter: byFighter
      };
      pending = null;
      return cache;
    });
    return pending;
  };

  UFC.loadError = function (container, err) {
    if (!container) return;
    container.innerHTML = "";
    var d = document.createElement("div");
    d.className = "empty-state";
    d.textContent = "Could not load site data (" + (err && err.message ? err.message : err) + "). " +
      "If you opened this file directly from disk, browsers block local JSON fetches — " +
      "serve the docs/ folder with a static server, or view it on GitHub Pages.";
    container.appendChild(d);
  };

  /* convenience: which corner is this fighter, and what did they get priced at */
  UFC.side = function (fight, fighterId) { return fight.a === fighterId ? "a" : "b"; };
  UFC.oddsFor = function (fight, fighterId) { return fight.a === fighterId ? fight.oa : fight.ob; };
  UFC.opponentOf = function (fight, fighterId) { return fight.a === fighterId ? fight.b : fight.a; };
  /* "W" | "L" | "D" | "NC" */
  UFC.resultFor = function (fight, fighterId) {
    if (fight.w === "D") return "D";
    if (fight.w === "N") return "NC";
    return fight.w === UFC.side(fight, fighterId) ? "W" : "L";
  };

  /* ==========================================================================
     4. SHARED CHROME: nav, tooltip host, search overlay
     ====================================================================== */
  /* Pages that exist in docs/. Add to this list as new pages ship — every page
     picks the nav up from here, so links stay in sync site-wide. */
  var NAV_ITEMS = [
    { id: "home",         label: "Home",         href: "index.html" },
    { id: "fighters",     label: "Fighters",     href: "fighters.html" },
    { id: "events",       label: "Events",       href: "events.html" },
    { id: "betting",      label: "Odds",         href: "betting.html" },
    { id: "leaderboards", label: "Leaderboards", href: "leaderboards.html" }
  ];
  UFC.navItems = NAV_ITEMS;

  function ensureTooltip() {
    var t = document.getElementById("tooltip");
    if (!t) {
      t = document.createElement("div");
      t.className = "tt";
      t.id = "tooltip";
      t.setAttribute("role", "status");
      t.setAttribute("aria-live", "polite");
      document.body.appendChild(t);
    }
    return t;
  }

  /* Injects the sticky site nav as the first element of <body>
     (or into #sitenav-slot if the page provides one). */
  UFC.nav = function (active) {
    if (document.querySelector(".sitenav")) return;

    var wrap = document.createElement("div");
    wrap.className = "sitenav";
    var inner = document.createElement("div");
    inner.className = "inner";
    wrap.appendChild(inner);

    var brand = document.createElement("a");
    brand.className = "brand";
    brand.href = UFC.url("index.html");
    brand.innerHTML = 'UFC<span class="dot">_</span>DATA';
    inner.appendChild(brand);

    var links = document.createElement("div");
    links.className = "links";
    NAV_ITEMS.forEach(function (item) {
      var a = document.createElement("a");
      a.className = "navlink";
      a.href = UFC.url(item.href);
      a.textContent = item.label;
      if (item.id === active) a.setAttribute("aria-current", "page");
      links.appendChild(a);
    });
    inner.appendChild(links);

    var spacer = document.createElement("div");
    spacer.className = "spacer";
    inner.appendChild(spacer);

    /* search trigger */
    var sBtn = document.createElement("button");
    sBtn.type = "button";
    sBtn.className = "navbtn search-btn";
    sBtn.innerHTML = '<span aria-hidden="true">⌕</span> Search <kbd>/</kbd>';
    sBtn.setAttribute("aria-label", "Search fighters and events");
    sBtn.addEventListener("click", function () { UFC.search(); });
    inner.appendChild(sBtn);

    /* odds format */
    var fmtWrap = document.createElement("div");
    fmtWrap.className = "fmt-wrap";
    var fmtLbl = document.createElement("label");
    fmtLbl.setAttribute("for", "ufc-fmt");
    fmtLbl.textContent = "Odds";
    var sel = document.createElement("select");
    sel.className = "fmt-sel";
    sel.id = "ufc-fmt";
    sel.title = "Odds display format. \"Return on $100\" is the profit a winning $100 stake returns.";
    ODDS_FORMATS.forEach(function (f) {
      var o = document.createElement("option");
      o.value = f.id; o.textContent = f.label;
      sel.appendChild(o);
    });
    sel.value = oddsFormat;
    sel.addEventListener("change", function () { UFC.setOddsFormat(sel.value); });
    fmtWrap.appendChild(fmtLbl);
    fmtWrap.appendChild(sel);
    inner.appendChild(fmtWrap);

    /* theme */
    var tBtn = document.createElement("button");
    tBtn.type = "button";
    tBtn.className = "navbtn theme-btn";
    tBtn.addEventListener("click", function () { UFC.theme(); });
    inner.appendChild(tBtn);

    var slot = document.getElementById("sitenav-slot");
    if (slot) slot.parentNode.replaceChild(wrap, slot);
    else document.body.insertBefore(wrap, document.body.firstChild);

    syncThemeButton();
    ensureTooltip();
    buildSearch();
  };

  /* ==========================================================================
     5. SEARCH (fighters + events, keyboard driven)
     ====================================================================== */
  var searchEls = null, searchIndex = null, searchSel = 0, searchRows = [];

  function buildSearch() {
    if (searchEls) return searchEls;
    var box = document.createElement("div");
    box.className = "searchbox";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-label", "Search fighters and events");

    var scrim = document.createElement("div");
    scrim.className = "scrim";
    scrim.addEventListener("click", closeSearch);
    box.appendChild(scrim);

    var panel = document.createElement("div");
    panel.className = "panel";
    var input = document.createElement("input");
    input.type = "text";
    input.className = "q";
    input.setAttribute("placeholder", "Search fighters and events…");
    input.setAttribute("aria-label", "Search fighters and events");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-controls", "ufc-search-results");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("spellcheck", "false");
    panel.appendChild(input);

    var ul = document.createElement("ul");
    ul.id = "ufc-search-results";
    ul.setAttribute("role", "listbox");
    panel.appendChild(ul);

    var hint = document.createElement("div");
    hint.className = "hint";
    hint.innerHTML = '<span><kbd>↑</kbd><kbd>↓</kbd> navigate</span><span><kbd>↵</kbd> open</span><span><kbd>esc</kbd> close</span>';
    panel.appendChild(hint);
    box.appendChild(panel);
    document.body.appendChild(box);

    input.addEventListener("input", function () { runSearch(input.value); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); moveSel(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); moveSel(-1); }
      else if (e.key === "Home" && searchRows.length) { e.preventDefault(); setSel(0); }
      else if (e.key === "End" && searchRows.length) { e.preventDefault(); setSel(searchRows.length - 1); }
      else if (e.key === "Enter") {
        var row = searchRows[searchSel];
        if (row) { e.preventDefault(); location.href = row.href; }
      } else if (e.key === "Escape") { e.preventDefault(); closeSearch(); }
    });

    searchEls = { box: box, input: input, ul: ul };
    return searchEls;
  }

  function buildSearchIndex(data) {
    if (searchIndex) return searchIndex;
    var rows = [];
    data.fighters.forEach(function (f) {
      rows.push({
        kind: "Fighter",
        label: f.n,
        lower: f.n.toLowerCase(),
        meta: UFC.record(f) + " · " + f.nf + (f.nf === 1 ? " fight" : " fights"),
        href: UFC.url("fighter.html?id=" + f.i),
        weight: f.nf
      });
    });
    data.events.forEach(function (e) {
      rows.push({
        kind: "Event",
        label: e.n,
        lower: e.n.toLowerCase(),
        meta: UFC.fmtDate(e.d) + " · " + e.nf + " fights",
        href: UFC.url("betting.html#event-" + e.i),
        weight: 0
      });
    });
    searchIndex = rows;
    return rows;
  }

  function runSearch(q) {
    var els = searchEls;
    q = (q || "").trim().toLowerCase();
    els.ul.innerHTML = "";
    searchRows = [];
    searchSel = 0;
    if (!searchIndex) {
      els.ul.innerHTML = '<li class="noresults">Loading index…</li>';
      return;
    }
    if (!q) {
      els.ul.innerHTML = '<li class="noresults">Type a fighter or event name.</li>';
      return;
    }
    var hits = [];
    for (var i = 0; i < searchIndex.length; i++) {
      var r = searchIndex[i];
      var pos = r.lower.indexOf(q);
      if (pos < 0) continue;
      /* rank: exact > name start > word start > anywhere; then career length */
      var score = 0;
      if (r.lower === q) score = 400;
      else if (pos === 0) score = 300;
      else if (r.lower.charAt(pos - 1) === " " || r.lower.charAt(pos - 1) === "-") score = 200;
      else score = 100;
      /* fighters outrank events at the same match quality: a name query on
         "jones" should surface Jon Jones before "Jones vs Preux" */
      if (r.kind === "Fighter") score += 120;
      hits.push({ r: r, score: score + Math.min(r.weight, 30) / 100 });
      if (hits.length > 900) break;
    }
    hits.sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      return a.r.label < b.r.label ? -1 : 1;
    });
    hits = hits.slice(0, 40);
    if (!hits.length) {
      els.ul.innerHTML = '<li class="noresults">No fighter or event matches “' + q.replace(/[<>&]/g, "") + '”.</li>';
      return;
    }
    hits.forEach(function (h, idx) {
      var r = h.r;
      var li = document.createElement("li");
      li.setAttribute("role", "option");
      li.id = "ufc-sr-" + idx;
      li.setAttribute("aria-selected", idx === 0 ? "true" : "false");
      var a = document.createElement("a");
      a.href = r.href;
      var k = document.createElement("span");
      k.className = "kind"; k.textContent = r.kind;
      var nm = document.createElement("span"); nm.textContent = r.label;
      var mt = document.createElement("span"); mt.className = "meta"; mt.textContent = r.meta;
      a.appendChild(k); a.appendChild(nm); a.appendChild(mt);
      li.appendChild(a);
      li.addEventListener("mouseenter", function () { setSel(idx); });
      els.ul.appendChild(li);
      searchRows.push({ li: li, href: r.href });
    });
    setSel(0);
  }

  function setSel(i) {
    if (!searchRows.length) return;
    searchSel = Math.max(0, Math.min(i, searchRows.length - 1));
    for (var j = 0; j < searchRows.length; j++) {
      searchRows[j].li.setAttribute("aria-selected", j === searchSel ? "true" : "false");
    }
    var li = searchRows[searchSel].li;
    searchEls.input.setAttribute("aria-activedescendant", li.id);
    if (li.scrollIntoView) li.scrollIntoView({ block: "nearest" });
  }
  function moveSel(d) { setSel(searchSel + d); }

  var lastFocus = null;
  UFC.search = function (initial) {
    var els = buildSearch();
    lastFocus = document.activeElement;
    els.box.classList.add("open");
    els.input.value = initial || "";
    runSearch(els.input.value);
    els.input.focus();
    UFC.load().then(function (data) {
      buildSearchIndex(data);
      runSearch(els.input.value);
    })["catch"](function () {
      els.ul.innerHTML = '<li class="noresults">Search index unavailable — site data failed to load.</li>';
    });
  };
  function closeSearch() {
    if (!searchEls) return;
    searchEls.box.classList.remove("open");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  UFC.closeSearch = closeSearch;

  document.addEventListener("keydown", function (e) {
    var t = e.target || {};
    var tag = (t.tagName || "").toLowerCase();
    var typing = tag === "input" || tag === "textarea" || tag === "select" || t.isContentEditable;
    if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      UFC.search();
    } else if (e.key === "Escape" && searchEls && searchEls.box.classList.contains("open")) {
      closeSearch();
    }
  });

  /* ==========================================================================
     6. SVG CHART HELPERS (lifted from betting.html)
     ====================================================================== */
  var SVGNS = "http://www.w3.org/2000/svg";
  function el(tag, attrs, parent) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function css(name) {
    return getComputedStyle(document.body).getPropertyValue(name).trim();
  }

  function showTooltip(x, y, nodes) {
    var tooltipEl = ensureTooltip();
    tooltipEl.innerHTML = "";
    nodes.forEach(function (node) { tooltipEl.appendChild(node); });
    tooltipEl.classList.add("show");
    var vw = window.innerWidth, vh = window.innerHeight;
    var w = 240, h = 90;
    var left = Math.min(x + 14, vw - w - 10);
    var top = Math.min(y + 14, vh - h - 10);
    tooltipEl.style.left = Math.max(6, left) + "px";
    tooltipEl.style.top = Math.max(6, top) + "px";
  }
  function hideTooltip() {
    var t = document.getElementById("tooltip");
    if (t) t.classList.remove("show");
  }
  function ttHeader(text) { var d = document.createElement("div"); d.className = "tt-h"; d.textContent = text; return d; }
  function ttRow(label, value, color) {
    var row = document.createElement("div"); row.className = "tt-row";
    var k = document.createElement("span"); k.className = "k";
    if (color) { var sw = document.createElement("span"); sw.className = "key-swatch"; sw.style.background = color; k.appendChild(sw); }
    var kt = document.createElement("span"); kt.textContent = label; k.appendChild(kt);
    var v = document.createElement("span"); v.className = "v"; v.textContent = value;
    row.appendChild(k); row.appendChild(v);
    return row;
  }
  function ttNote(text) { var d = document.createElement("div"); d.className = "tt-note"; d.textContent = text; return d; }

  function roundedTopBar(x, y, w, h, r) {
    if (h < r) r = Math.max(h, 0);
    return "M" + x + " " + (y + h) + " L" + x + " " + (y + r) + " Q" + x + " " + y + " " + (x + r) + " " + y +
           " L" + (x + w - r) + " " + y + " Q" + (x + w) + " " + y + " " + (x + w) + " " + (y + r) +
           " L" + (x + w) + " " + (y + h) + " Z";
  }
  function roundedRightBar(x, y, w, h, r) {
    if (w < r) r = Math.max(w, 0);
    return "M" + x + " " + y + " L" + (x + w - r) + " " + y + " Q" + (x + w) + " " + y + " " + (x + w) + " " + (y + r) +
           " L" + (x + w) + " " + (y + h - r) + " Q" + (x + w) + " " + (y + h) + " " + (x + w - r) + " " + (y + h) +
           " L" + x + " " + (y + h) + " Z";
  }
  function niceTicks(min, max, count) {
    var range = max - min;
    if (!(range > 0)) return [min];
    var step = Math.pow(10, Math.floor(Math.log(range / count) / Math.LN10));
    var err = range / count / step;
    if (err >= 7.5) step *= 10; else if (err >= 3) step *= 5; else if (err >= 1.5) step *= 2;
    var start = Math.ceil(min / step) * step;
    var out = [];
    for (var v = start; v <= max + 1e-9; v += step) out.push(Math.round(v * 1000) / 1000);
    return out;
  }

  /* attach hover/focus tooltip behaviour to an SVG node */
  function hoverTip(node, build) {
    node.style.cursor = "pointer";
    if (node.tabIndex !== undefined) node.tabIndex = 0;
    function onEnter(evt) {
      var r = node.getBoundingClientRect();
      var px = (evt && evt.clientX !== undefined) ? evt.clientX : (r.left + r.width / 2);
      var py = (evt && evt.clientY !== undefined) ? evt.clientY : r.top;
      showTooltip(px, py, build());
    }
    node.addEventListener("pointerenter", onEnter);
    node.addEventListener("pointermove", onEnter);
    node.addEventListener("focus", onEnter);
    node.addEventListener("pointerleave", hideTooltip);
    node.addEventListener("blur", hideTooltip);
  }

  UFC.el = el;
  UFC.css = css;
  UFC.showTooltip = showTooltip;
  UFC.hideTooltip = hideTooltip;
  UFC.ttHeader = ttHeader;
  UFC.ttRow = ttRow;
  UFC.ttNote = ttNote;
  UFC.hoverTip = hoverTip;
  UFC.roundedTopBar = roundedTopBar;
  UFC.roundedRightBar = roundedRightBar;
  UFC.niceTicks = niceTicks;
  UFC.ensureTooltip = ensureTooltip;

  /* ==========================================================================
     7. SMALL DOM HELPERS
     ====================================================================== */
  UFC.h = function (tag, attrs, children) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === "class") e.className = attrs[k];
      else if (k === "text") e.textContent = attrs[k];
      else if (k === "html") e.innerHTML = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, attrs[k]);
    }
    if (children) children.forEach(function (c) {
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return e;
  };
  UFC.td = function (text, cls) {
    var d = document.createElement("td");
    if (cls) d.className = cls;
    d.textContent = text;
    return d;
  };

  /* odds-coverage copy reused by every page that shows a "—" price */
  UFC.ODDS_COVERAGE_NOTE =
    "No closing line on record. Moneyline coverage runs 2007-06-16 to 2023-08-05; " +
    "fights outside that window — and some inside it — have no matched price.";

})(window, document);
