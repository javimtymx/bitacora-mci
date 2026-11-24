/* Sugerencia de fecha al planear un ticket (como «horarios disponibles» de un calendario).
   En el modal de planear: con las horas y el ingeniero, el sistema calcula su carga (≈4 h hábiles por día, cola por
   prioridad) y propone la fecha de entrega; muestra cuándo podría cada ingeniero y qué tickets se recorrerían.
   Los recorridos solo se aplican si se dejan marcados al guardar (con motivo). */
(function () {
  "use strict";
  var DIAS = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
  function f(iso) { if (!iso) return "—"; var p = iso.split("-"); var d = new Date(+p[0], +p[1] - 1, +p[2]); return DIAS[d.getDay()] + " " + p[2] + "/" + p[1]; }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function preparar(caja) {
    var form = caja.closest("form"), h = form.querySelector("[name=horas]"), fe = form.querySelector("[name=fecha_entrega]"),
        a = form.querySelector("[name=asignado_id]"), timer = null, ultimo = "";
    function tid() { var m = (form.getAttribute("action") || "").match(/\/tickets\/(\d+)\/planear/); return m ? m[1] : null; }
    function pedir() {
      var id = tid(), hv = (h.value || "").replace(",", ".");
      if (!id || !(parseFloat(hv) > 0)) { caja.innerHTML = '<span class="muted small">Escribe las horas y el sistema te sugiere la fecha según la carga del equipo.</span>'; return; }
      var url = "/api/tickets/" + id + "/sugerencia?horas=" + encodeURIComponent(hv) + (a.value ? "&asignado_id=" + a.value : "");
      if (url === ultimo) return;
      ultimo = url;
      caja.innerHTML = '<span class="muted small">Calculando carga…</span>';
      fetch(url, { headers: { Accept: "application/json" } }).then(function (r) { return r.json(); }).then(pintar)
        .catch(function () { caja.innerHTML = '<span class="muted small">No se pudo calcular la carga.</span>'; });
    }
    function pintar(d) {
      if (!d.ok) { caja.innerHTML = '<span class="muted small">' + esc(d.mensaje) + "</span>"; return; }
      var e = d.elegido, html = "";
      if (e) {
        html += '<div class="pla-sug-top"><div><b>Sugerida: ' + f(e.fecha) + "</b> <span class=\"muted small\">· empieza " + f(e.inicio) +
          " · " + esc(e.nombre) + " tiene " + e.horas_en_cola + " h en cola (hoy " + e.carga_hoy + "/" + e.capacidad + " h)</span></div>" +
          '<button type="button" class="btn sm soft" data-usar="' + e.fecha + '">Usar esta fecha</button></div>';
        if (!fe.value) fe.value = e.fecha;
      } else {
        html += '<div class="muted small">Elige el ingeniero para ver su fecha, o toma una de estas:</div>';
      }
      html += '<div class="pla-sug-gente">' + d.opciones.map(function (o) {
        return '<button type="button" class="pla-chip' + (e && o.ingeniero === e.ingeniero ? " on" : "") + '" data-ing="' + o.ingeniero +
          '" data-fecha="' + o.fecha + '" title="' + o.horas_en_cola + ' h en cola · ' + o.capacidad + ' h/día">' + esc(o.nombre) + " · <b>" + f(o.fecha) + "</b></button>";
      }).join("") + "</div>";
      if (e && e.afectados.length) {
        var tarde = e.afectados.filter(function (x) { return x.tarde; });
        html += '<div class="pla-afect"><b>Se recorren ' + e.afectados.length + " ticket(s) de " + esc(e.nombre) + "</b>" +
          (tarde.length ? ' <span class="small">· ' + tarde.length + " ya no llegarían a su fecha comprometida. Marca los que quieras reprogramar:</span>" : ' <span class="small muted">· todos siguen llegando a su fecha.</span>') +
          e.afectados.map(function (x) {
            var lin = '<span class="prio ' + esc(x.prioridad) + '">' + esc(x.prioridad || "—") + "</span> <b>" + esc(x.folio) + "</b> " + esc(x.titulo.slice(0, 60)) +
              ' <span class="muted small">termina ' + f(x.fin_antes) + " → " + f(x.fin_nuevo) + (x.entrega ? " · entrega " + f(x.entrega) : "") + "</span>";
            return x.tarde ? '<label class="check pla-af"><input type="checkbox" name="fecha_' + x.id + '" value="' + x.fin_nuevo + '" checked>' + lin + "</label>"
                           : '<div class="pla-af">' + lin + "</div>";
          }).join("") +
          (tarde.length ? '<input class="input" name="motivo_carga" maxlength="300" placeholder="Motivo del cambio de fechas (ej. entró un P1 de SAFE)" style="margin-top:6px">' : "") +
          '<a class="small" href="/tickets-tablero?vista=cargas&persona=' + e.ingeniero + '" target="_blank">Ver y mover la carga de ' + esc(e.nombre) + " →</a></div>";
      }
      caja.innerHTML = html;
    }
    caja.addEventListener("click", function (ev) {
      var u = ev.target.closest("[data-usar]"), c = ev.target.closest("[data-ing]");
      if (u) fe.value = u.dataset.usar;
      if (c) { a.value = c.dataset.ing; fe.value = c.dataset.fecha; pedir(); }
    });
    function luego() { clearTimeout(timer); timer = setTimeout(pedir, 350); }
    h.addEventListener("input", luego);
    a.addEventListener("change", pedir);
    caja._refrescar = function () { ultimo = ""; pedir(); };
    // al abrir el modal (el tablero cambia la acción y los valores antes de abrirlo)
    var ov = caja.closest(".overlay");
    if (ov) new MutationObserver(function () { if (ov.classList.contains("open")) caja._refrescar(); })
      .observe(ov, { attributes: true, attributeFilter: ["class", "style", "hidden"] });
    pedir();
  }
  function todos() { document.querySelectorAll("[data-sugerencia]").forEach(preparar); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", todos); else todos();
})();
