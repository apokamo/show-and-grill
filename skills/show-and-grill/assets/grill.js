// Renders a show-and-grill explanation page.
// It draws the Markdown in <script type="text/markdown" id="src">. Figures are SVG (class="dg")
// written directly in the Markdown, and screen layout wireframes (class="wf").
// It loads as a classic script so it also works from file://, and imports the libraries
// from a CDN at pinned versions. The fixed text follows <html lang> (en or ja).
(function () {
  "use strict";

  const LIBS = {
    marked: "https://cdn.jsdelivr.net/npm/marked@18.1.1/lib/marked.esm.js",
    purify: "https://cdn.jsdelivr.net/npm/dompurify@3.4.16/dist/purify.es.mjs",
  };

  const STRINGS = {
    en: {
      loading: "Loading…",
      index: "Index",
      toDark: "Dark",
      toLight: "Light",
      toDarkTitle: "Switch to the dark theme",
      toLightTitle: "Switch to the light theme",
      cdnError: "Could not load the rendering libraries from the CDN (offline?). Showing the Markdown as is.",
    },
    ja: {
      loading: "読み込み中…",
      index: "一覧",
      toDark: "ダーク",
      toLight: "ライト",
      toDarkTitle: "ダークの表示に切り替える",
      toLightTitle: "ライトの表示に切り替える",
      cdnError: "描画ライブラリを CDN から読み込めませんでした（オフライン？）。Markdown をそのまま表示します。",
    },
  };
  const lang = (document.documentElement.lang || "").toLowerCase().startsWith("ja") ? "ja" : "en";
  const T = STRINGS[lang];

  // Web fonts (Google Fonts, SIL Open Font License). English pages load only Latin fonts;
  // Japanese pages also load BIZ UDPGothic. Without the network, grill.css falls back to system fonts.
  const FONT_CSS =
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;700" +
    (lang === "ja" ? "&family=BIZ+UDPGothic:wght@400;700" : "") +
    "&family=JetBrains+Mono:wght@400;700&display=swap";

  function addFonts() {
    for (const origin of ["https://fonts.googleapis.com", "https://fonts.gstatic.com"]) {
      const pre = document.createElement("link");
      pre.rel = "preconnect";
      pre.href = origin;
      if (origin.includes("gstatic")) pre.crossOrigin = "anonymous";
      document.head.appendChild(pre);
    }
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = FONT_CSS;
    document.head.appendChild(link);
  }
  addFonts();

  // Light and dark theme. The first view follows the OS; the button switches between the two and the choice is kept
  // in this browser (over http, only while the server keeps the same port). grill.css reads <html data-theme>.
  const THEME_KEY = "show-and-grill-theme";
  const root = document.documentElement;

  function savedTheme() {
    try {
      const v = localStorage.getItem(THEME_KEY);
      return v === "light" || v === "dark" ? v : null;
    } catch (e) {
      return null;
    }
  }

  function currentTheme() {
    if (root.dataset.theme === "light" || root.dataset.theme === "dark") return root.dataset.theme;
    return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  const initialTheme = savedTheme();
  if (initialTheme) root.dataset.theme = initialTheme;

  // The button sits on the title line, at the right end. Without a title (the CDN failed), it goes in the nav.
  function addThemeToggle(main) {
    const button = document.createElement("button");
    button.type = "button";
    button.id = "theme-toggle";
    const label = () => {
      const toDark = currentTheme() === "light";
      button.textContent = toDark ? T.toDark : T.toLight;
      button.title = toDark ? T.toDarkTitle : T.toLightTitle;
    };
    button.addEventListener("click", () => {
      const next = currentTheme() === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch (e) {
        // Storage is blocked: the switch still works on this page.
      }
      label();
    });
    label();
    // With no saved choice the page follows the OS, so keep the label right when the OS theme changes.
    if (window.matchMedia) matchMedia("(prefers-color-scheme: dark)").addEventListener("change", label);
    const h1 = main.querySelector(":scope > h1");
    if (h1) {
      const row = document.createElement("div");
      row.className = "title-row";
      h1.before(row);
      row.append(h1, button);
      return;
    }
    const nav = document.getElementById("nav");
    if (nav) nav.appendChild(button);
    else document.body.prepend(button);
  }

  // Drop leading blank lines and the indent shared by all lines (so an indent does not become a code block).
  function dedent(text) {
    const lines = text.replace(/^\s*\n/, "").replace(/\s+$/, "").split("\n");
    const indents = lines.filter((l) => l.trim()).map((l) => l.match(/^ */)[0].length);
    const cut = indents.length ? Math.min(...indents) : 0;
    return lines.map((l) => l.slice(cut)).join("\n");
  }

  // q03.html → "Q3", a link to the previous question, and (over http) a link to the index.
  function buildNav() {
    const nav = document.getElementById("nav");
    if (!nav) return;
    const m = location.pathname.match(/q(\d+)\.html$/);
    const parts = [];
    if (m) {
      const n = parseInt(m[1], 10);
      parts.push(`<span class="qno">Q${n}</span>`);
      if (n > 1) {
        const prev = String(n - 1).padStart(m[1].length, "0");
        parts.push(`<a href="q${prev}.html">← Q${n - 1}</a>`);
      }
      if (location.protocol.startsWith("http")) parts.push(`<a href="./">${T.index}</a>`);
    }
    nav.innerHTML = parts.join(" ");
  }

  // Arrow markers for SVG figures, once per page. Their size does not follow the line width. CSS uses them through
  // marker-end: url(#dg-arrow) and, for the key line, url(#dg-arrow-key).
  function addArrowMarkers() {
    if (document.getElementById("dg-arrow")) return;
    const marker = (id) =>
      `<marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="11" markerHeight="11" ` +
      'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"/></marker>';
    const holder = document.createElement("div");
    holder.innerHTML =
      '<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>' +
      marker("dg-arrow") +
      marker("dg-arrow-key") +
      "</defs></svg>";
    document.body.appendChild(holder.firstChild);
  }

  function showError(main, message, source) {
    const box = document.createElement("div");
    box.className = "load-error";
    box.textContent = message;
    main.replaceChildren(box);
    if (source) {
      const pre = document.createElement("pre");
      pre.textContent = source;
      main.appendChild(pre);
    }
  }

  async function render() {
    buildNav();
    const main = document.getElementById("content");
    const srcEl = document.getElementById("src");
    const md = dedent(srcEl ? srcEl.textContent : "");

    const loading = document.createElement("p");
    loading.className = "loading";
    loading.textContent = T.loading;
    main.replaceChildren(loading);

    let marked, DOMPurify;
    try {
      [{ marked }, { default: DOMPurify }] = await Promise.all([import(LIBS.marked), import(LIBS.purify)]);
    } catch (e) {
      showError(main, T.cdnError, md);
      addThemeToggle(main);
      return;
    }

    main.innerHTML = DOMPurify.sanitize(marked.parse(md));
    addArrowMarkers();
    addThemeToggle(main);

    const h1 = main.querySelector("h1");
    if (h1) document.title = h1.textContent;
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", render);
  else render();
})();
