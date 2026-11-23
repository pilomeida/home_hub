/* The paper viewer (PLAN step 11c, docs/STYLE.md section 4). A click on a paper's preview opens it; the person picks
   Full view (the paper fills the screen) or Side by side (the paper pinned on the left, this page on the right).
   The paper is loaded only from its own file route. No library, no outside request. */
(function () {
  var viewer = null, lastFocus = null;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }

  function setMode(mode) {
    if (!viewer) return;
    var side = mode === "side";
    viewer.classList.toggle("side", side);
    document.body.classList.toggle("pv-side", side);
    viewer.querySelector("[data-mode=full]").setAttribute("aria-pressed", String(!side));
    viewer.querySelector("[data-mode=side]").setAttribute("aria-pressed", String(side));
  }

  function close() {
    if (!viewer) return;
    document.body.classList.remove("pv-side", "pv-open");
    viewer.remove();
    viewer = null;
    if (lastFocus) lastFocus.focus();
  }

  function open(figure) {
    close();
    lastFocus = document.activeElement;
    var file = figure.getAttribute("data-pv-file"), kind = figure.getAttribute("data-pv-kind"),
        name = figure.getAttribute("data-pv-name");
    viewer = el("div", "pv-viewer");
    viewer.setAttribute("role", "dialog");
    viewer.setAttribute("aria-label", name);
    var bar = el("div", "pv-bar");
    bar.appendChild(el("span", "pv-title", name));
    var modes = el("div", "pv-modes");
    var full = el("button", "", "Full view"); full.type = "button"; full.setAttribute("data-mode", "full");
    var side = el("button", "pv-side-btn", "Side by side"); side.type = "button"; side.setAttribute("data-mode", "side");
    modes.appendChild(full); modes.appendChild(side);
    bar.appendChild(modes);
    var tab = el("a", "btn", "Open in a new tab"); tab.href = file; tab.target = "_blank"; tab.rel = "noopener";
    bar.appendChild(tab);
    var shut = el("button", "pv-close", "Close"); shut.type = "button";
    bar.appendChild(shut);
    viewer.appendChild(bar);
    var body = el("div", "pv-body");
    var paper = kind === "image" ? el("img") : el("iframe");
    paper.src = file;
    if (kind === "image") paper.alt = name; else paper.title = name;
    body.appendChild(paper);
    viewer.appendChild(body);
    document.body.appendChild(viewer);
    document.body.classList.add("pv-open");
    full.addEventListener("click", function () { setMode("full"); });
    side.addEventListener("click", function () { setMode("side"); });
    shut.addEventListener("click", close);
    setMode("full");
    full.focus();
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-pv-open]");
    if (b) { e.preventDefault(); open(b.closest(".pv")); }
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  // A preview picture that can't be drawn says so in words instead of a broken image.
  document.addEventListener("error", function (e) {
    var img = e.target;
    if (!img.matches || !img.matches("[data-pv-img]")) return;
    var fig = img.closest(".pv");
    fig.querySelector("[data-pv-open]").hidden = true;
    fig.querySelector("[data-pv-failed]").hidden = false;
  }, true);
})();
