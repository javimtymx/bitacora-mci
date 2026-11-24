/* Arrastrar y soltar archivos en todos los controles de archivo (<input type="file">).
   La zona es el recuadro .drop, el campo (.field) o, si el control está oculto detrás de un botón, el formulario.
   Los archivos soltados se agregan a los que ya estaban elegidos (si el control acepta varios) y se respeta «accept».
   Las imágenes soltadas sobre un texto con imágenes en línea (data-imagenes) las maneja imagenes.js, no este. */
(function () {
  "use strict";
  function acepta(input, f) {
    var a = (input.getAttribute("accept") || "").split(",").map(function (x) { return x.trim().toLowerCase(); }).filter(Boolean);
    if (!a.length) return true;
    var n = (f.name || "").toLowerCase(), t = (f.type || "").toLowerCase();
    return a.some(function (x) {
      if (x.charAt(0) === ".") return n.slice(-x.length) === x;
      if (x.slice(-2) === "/*") return t.indexOf(x.slice(0, -1)) === 0;
      return t === x;
    });
  }
  function zona(input) {
    if (input.closest("[data-drop]")) return input.closest("[data-drop]");
    if (input.closest(".drop")) return input.closest(".drop");
    var oculto = input.hidden || input.type === "hidden" || getComputedStyle(input).display === "none";
    if (!oculto && input.closest(".field")) return input.closest(".field");
    if (!oculto) return input.parentElement;
    return input.closest("form") || input.parentElement;
  }
  function aviso(z, txt) {
    var s = z.querySelector(":scope > .arr-aviso");
    if (!s) { s = document.createElement("span"); s.className = "arr-aviso small"; z.appendChild(s); }
    s.textContent = txt; s.hidden = false;
    clearTimeout(s._t); s._t = setTimeout(function () { s.hidden = true; }, 6000);
  }
  function tieneArchivos(e) { return e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") >= 0; }

  function preparar(input) {
    if (input._arr || input.closest(".tkimg-barra") || input.hasAttribute("data-sin-arrastre")) return;
    input._arr = true;
    var z = zona(input);
    if (!z || z._arrInput) return;  // una zona, un control
    z._arrInput = input;
    z.classList.add("arr-zona");
    if (z.classList.contains("drop") && !z.querySelector(".arr-tip")) {
      var tip = document.createElement("span"); tip.className = "arr-tip small muted"; tip.textContent = "o arrastra y suelta aquí";
      z.appendChild(tip);
    }
    var cuenta = 0;
    z.addEventListener("dragenter", function (e) { if (!tieneArchivos(e)) return; cuenta++; z.classList.add("arr-sobre"); });
    z.addEventListener("dragleave", function () { if (--cuenta <= 0) { cuenta = 0; z.classList.remove("arr-sobre"); } });
    z.addEventListener("dragover", function (e) { if (tieneArchivos(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } });
    z.addEventListener("drop", function (e) {
      cuenta = 0; z.classList.remove("arr-sobre");
      if (!tieneArchivos(e) || e.defaultPrevented) return;
      e.preventDefault();
      var nuevos = Array.prototype.slice.call(e.dataTransfer.files || []);
      var ok = nuevos.filter(function (f) { return acepta(input, f); });
      if (ok.length < nuevos.length) aviso(z, (nuevos.length - ok.length) + " archivo(s) con formato no permitido se omitieron.");
      if (!ok.length) return;
      var dt = new DataTransfer();
      if (input.multiple) Array.prototype.forEach.call(input.files || [], function (f) { dt.items.add(f); });
      (input.multiple ? ok : ok.slice(0, 1)).forEach(function (f) { dt.items.add(f); });
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  function todos(raiz) { (raiz || document).querySelectorAll('input[type="file"]').forEach(preparar); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { todos(); }); else todos();
  new MutationObserver(function (ms) {
    ms.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) { if (n.matches && n.matches('input[type="file"]')) preparar(n); else if (n.querySelectorAll) todos(n); } }); });
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
