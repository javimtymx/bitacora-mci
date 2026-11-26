/* Lienzo estratégico: al pasar el cursor por un punto se ve el proveedor; al hacer clic en un punto, en el número del
   factor o en la lista, se abre el detalle: qué hace mejor cada competidor y qué puede hacer OneCore para competir. */
(function () {
  "use strict";
  var fig = document.querySelector(".bm-lienzo");
  if (!fig) return;
  var tip = fig.querySelector(".bm-tip"), cuerpo = document.getElementById("fx-cuerpo");
  fig.style.position = "relative";
  fig.addEventListener("mousemove", function (e) {
    var p = e.target.closest(".bm-pt");
    if (!p) { tip.hidden = true; return; }
    var t = p.getAttribute("aria-label"), r = fig.getBoundingClientRect();
    tip.textContent = t; tip.hidden = false;
    tip.style.left = Math.min(e.clientX - r.left + 12, r.width - tip.offsetWidth - 4) + "px";
    tip.style.top = (e.clientY - r.top - 34) + "px";
  });
  fig.addEventListener("mouseleave", function () { tip.hidden = true; });
  function cargar(url, opts) {
    cuerpo.innerHTML = '<div class="small muted" style="padding:20px">Cargando…</div>';
    return fetch(url, opts || {}).then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
      .then(function (h) { cuerpo.innerHTML = h; })
      .catch(function () { cuerpo.innerHTML = '<div class="small" style="padding:20px;color:var(--red)">No se pudo cargar el detalle. Intenta de nuevo.</div>'; });
  }
  function abrir(k) {
    document.getElementById("fx-abrir").click();
    cargar("/clientes/benchmark/factor?k=" + encodeURIComponent(k));
  }
  fig.addEventListener("click", function (e) {
    var el = e.target.closest("[data-factor]");
    if (el) abrir(el.dataset.factor);
  });
  fig.addEventListener("keydown", function (e) {
    var el = e.target.closest && e.target.closest("[data-factor]");
    if (el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); abrir(el.dataset.factor); }
  });
  // después de corregir o confirmar algo desde la ventana, se regresa con ?factor=… y se vuelve a abrir
  var q = new URLSearchParams(location.search).get("factor");
  if (q) { abrir(q); history.replaceState(null, "", location.pathname); }
  cuerpo.addEventListener("submit", function (e) {
    var f = e.target.closest("[data-fx-explicar]");
    if (!f) return;
    e.preventDefault();
    f.querySelector("button").disabled = true;
    f.querySelector("[data-fx-esp]").hidden = false;
    fetch("/clientes/benchmark/factor/explicar", { method: "POST", body: new FormData(f) })
      .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
      .then(function (h) { cuerpo.innerHTML = h; })
      .catch(function () {
        f.querySelector("button").disabled = false;
        f.querySelector("[data-fx-esp]").textContent = "No se pudo conectar con GuardIA. Intenta de nuevo.";
      });
  });
})();
