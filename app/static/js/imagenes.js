/* Imágenes dentro del texto de un ticket (descripción y comentarios), no como anexo.
   <textarea data-imagenes="<id del ticket o vacío>">: se puede pegar una captura (Ctrl+V), arrastrar una imagen o usar
   «Insertar imagen». La imagen se sube a /tickets-img y en el texto queda ![imagen](/tickets-img/N) en el lugar del
   cursor; abajo del campo se ven las miniaturas y al guardar se muestra en línea, en el orden en que se escribió. */
(function () {
  "use strict";
  var RE = /!\[[^\]\n]{0,80}\]\((\/(?:tickets-img|clientes\/pendientes-img)\/\d+)\)/g;

  function insertar(ta, texto, ini, fin) {
    var v = ta.value, antes = v.slice(0, ini), despues = v.slice(fin);
    var pre = antes && !/\n$/.test(antes) ? "\n" : "", post = despues && !/^\n/.test(despues) ? "\n" : "\n";
    ta.value = antes + pre + texto + post + despues;
    var pos = (antes + pre + texto + post).length;
    ta.setSelectionRange(pos, pos);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function subir(ta, archivo) {
    if (!/^image\/(png|jpeg|gif|webp)$/.test(archivo.type)) { aviso(ta, "Solo imágenes PNG, JPG, GIF o WEBP; otros archivos van como anexo."); return; }
    if (archivo.size > 10 * 1024 * 1024) { aviso(ta, "La imagen pasa de 10 MB."); return; }
    var marca = "![subiendo imagen…]()", ini = ta.selectionStart, fin = ta.selectionEnd;
    insertar(ta, marca, ini, fin);
    var fd = new FormData();
    fd.append("imagen", archivo, archivo.name || "captura.png");
    var dueno = (ta.dataset.imagenes || "").split(":"), url = "/tickets-img";
    if (dueno[0] === "pendiente") { url = "/clientes/pendientes-img"; if (dueno[1]) fd.append("pendiente_id", dueno[1]); }
    else if (dueno[0]) fd.append("ticket_id", dueno[0]);
    fetch(url, { method: "POST", body: fd, headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        ta.value = ta.value.replace(marca, d.ok ? d.md : "");
        if (!d.ok) aviso(ta, d.mensaje || "No se pudo subir la imagen.");
        ta.dispatchEvent(new Event("input", { bubbles: true }));
      })
      .catch(function () { ta.value = ta.value.replace(marca, ""); aviso(ta, "No se pudo subir la imagen."); vista(ta); });
  }

  function aviso(ta, msg) {
    var c = caja(ta), a = c.querySelector(".tkimg-aviso");
    a.textContent = msg; a.hidden = false;
    setTimeout(function () { a.hidden = true; }, 6000);
  }

  function caja(ta) {
    if (ta._caja) return ta._caja;
    var c = document.createElement("div");
    c.className = "tkimg-barra";
    c.innerHTML = '<button type="button" class="btn sm ghost tkimg-btn" title="También puedes pegar una captura (Ctrl+V) o arrastrar la imagen al texto">' +
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/></svg>' +
      'Insertar imagen</button><span class="muted small">o pega una captura con Ctrl+V</span>' +
      '<input type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden data-sin-arrastre>' +
      '<span class="small tkimg-aviso" hidden></span><div class="tkimg-prev"></div>';
    ta.insertAdjacentElement("afterend", c);
    var inp = c.querySelector("input");
    c.querySelector(".tkimg-btn").addEventListener("click", function () { inp.click(); });
    inp.addEventListener("change", function () {
      Array.prototype.forEach.call(inp.files, function (f) { subir(ta, f); });
      inp.value = "";
    });
    ta._caja = c;
    return c;
  }

  function vista(ta) {
    var prev = caja(ta).querySelector(".tkimg-prev"), ids = [], m;
    RE.lastIndex = 0;
    while ((m = RE.exec(ta.value))) ids.push(m[1]);
    prev.innerHTML = ids.map(function (id) {
      return '<a href="' + id + '" target="_blank" rel="noopener" title="Quítala borrando su línea del texto"><img src="' + id + '" alt=""></a>';
    }).join("");
  }

  function preparar(ta) {
    if (ta._imgs) return;
    ta._imgs = true;
    caja(ta);
    ta.addEventListener("paste", function (e) {
      var items = (e.clipboardData || {}).items || [], files = [];
      for (var i = 0; i < items.length; i++) if (items[i].kind === "file" && /^image\//.test(items[i].type)) files.push(items[i].getAsFile());
      if (!files.length) return;
      e.preventDefault();
      files.forEach(function (f) { subir(ta, f); });
    });
    ta.addEventListener("dragover", function (e) {
      if (e.dataTransfer && Array.prototype.some.call(e.dataTransfer.types || [], function (t) { return t === "Files"; })) { e.preventDefault(); ta.classList.add("tkimg-drop"); }
    });
    ta.addEventListener("dragleave", function () { ta.classList.remove("tkimg-drop"); });
    ta.addEventListener("drop", function (e) {
      ta.classList.remove("tkimg-drop");
      var fs = Array.prototype.filter.call((e.dataTransfer || {}).files || [], function (f) { return /^image\//.test(f.type); });
      if (!fs.length) return;
      e.preventDefault();
      e.stopPropagation();  // la imagen va dentro del texto, no como anexo
      ta.focus();
      fs.forEach(function (f) { subir(ta, f); });
    });
    ta.addEventListener("input", function () { vista(ta); });
    vista(ta);
  }

  function todos() { document.querySelectorAll("textarea[data-imagenes]").forEach(preparar); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", todos); else todos();
})();
