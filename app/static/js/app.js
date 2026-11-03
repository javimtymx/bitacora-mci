// Bitácora MCI · modales y revisión con IA
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---- modales
  function abrir(id) { const o = document.getElementById(id); if (o) { o.classList.add("open"); const f = $("input:not([type=hidden]),select,textarea", o); if (f) f.focus(); } }
  function cerrar(o) { o.classList.remove("open"); }
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-open]");
    if (a) {
      e.preventDefault();
      for (const at of a.attributes) if (at.name.startsWith("data-fill-")) { const t = document.getElementById(at.name.slice(10)); if (t) t.value = at.value; }
      abrir(a.dataset.open); return;
    }
    const c = e.target.closest("[data-close]"); if (c) { e.preventDefault(); cerrar(c.closest(".overlay")); return; }
    // clic en la zona gris: no se cierra (se pierde lo capturado); solo un aviso visual de que sigue abierto
    if (e.target.classList.contains("overlay")) {
      const m = e.target.querySelector(".modal");
      if (m) { m.classList.remove("attn"); void m.offsetWidth; m.classList.add("attn"); }
    }
    const x = e.target.closest(".cint .x"); if (x) x.closest(".cint").remove();
  });
  if (location.hash && document.getElementById(location.hash.slice(1))?.classList.contains("overlay")) abrir(location.hash.slice(1));

  // ---- filtro de predictivas por área
  $$("select[data-area-filtro]").forEach((sel) => {
    const pred = document.getElementById(sel.dataset.areaFiltro);
    const aplicar = () => {
      let primera = null;
      $$("option", pred).forEach((o) => {
        const ok = !o.dataset.area || o.dataset.area === sel.value;
        o.hidden = !ok; o.disabled = !ok;
        if (ok && o.value && !primera) primera = o;
      });
      if (pred.selectedOptions[0]?.disabled) pred.value = primera ? primera.value : "";
    };
    sel.addEventListener("change", aplicar); aplicar();
  });

  // ---- buscador en tablas
  $$("input[data-filtra]").forEach((inp) => {
    const tb = document.getElementById(inp.dataset.filtra);
    inp.addEventListener("input", () => {
      const q = inp.value.toLowerCase().trim();
      $$("tbody tr", tb).forEach((tr) => { tr.hidden = q && !tr.textContent.toLowerCase().includes(q); });
    });
  });

  // ---- revisión con IA
  const VEREDICTOS = {
    ok: ["g", "Listo para guardar"], mejorar: ["o", "Se puede mejorar"], repetido: ["r", "Repite lo que no funcionó"],
    no_aplica: ["r", "No mueve la medida"], debil: ["o", "Idea débil"], fuerte: ["g", "Idea fuerte"], media: ["b", "Idea media"],
  };
  const CAUSAS = {
    dependencia_interna: "Dependencia interna", dependencia_cliente: "Dependencia del cliente", capacidad: "Capacidad",
    prioridad_cambiada: "Cambio de prioridad", compromiso_mal_definido: "Compromiso mal definido", ejecucion: "Ejecución",
    externa: "Externa", otra: "Otra",
  };
  const PARA = { responsable: "al responsable", lider: "al líder", direccion: "a Dirección" };

  function pintar(form, d) {
    const box = $("[data-ia-resultado]", form);
    const tipo = form.dataset.iaTipo;
    const v = d.veredicto;
    const [cls, txt] = VEREDICTOS[v] || ["b", v];
    let h = `<div class="row"><span class="tag ${cls}">${esc(txt)}</span><span class="muted small">Revisado hace unos segundos</span></div>`;
    if (tipo === "idea") {
      h += `<div class="row small"><span class="pill">Impacto ${esc(d.impacto)}/5</span><span class="pill">Esfuerzo ${esc(d.esfuerzo)}/5</span><span class="pill">${esc(d.cuadrante)}</span></div>`;
    }
    if (tipo === "remediacion" && d.causa_categoria) {
      h += `<div class="small"><b>Tipo de causa:</b> ${esc(CAUSAS[d.causa_categoria] || d.causa_categoria)}</div>`;
    }
    if ((d.observaciones || []).length) h += `<ul class="obs">${d.observaciones.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>`;
    if ((d.preguntas || []).length) h += `<div class="lbl">Preguntas para fortalecerla</div><ul class="obs">${d.preguntas.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>`;
    if (d.duplicado_titulo) h += `<div class="flash error small">Parece duplicada de «${esc(d.duplicado_titulo)}».</div>`;
    if (d.recomendacion_historica) h += `<div class="col"><span class="lbl">Lo que dice el histórico</span><div class="small" style="line-height:1.5">${esc(d.recomendacion_historica)}</div></div>`;
    $("[data-ia-izq]", form).innerHTML = h;

    const der = $("[data-ia-der]", form);
    let s = "";
    if (d.sugerencia) s += `<span class="lbl">Sugerencia</span><div class="sug">${esc(d.sugerencia)}</div>`;
    if ((d.plan || []).length) {
      s += `<span class="lbl">Seguimiento que propone el PM virtual</span>`;
      d.plan.forEach((p, i) => {
        s += `<label class="check small"><input type="checkbox" name="plan_sel" value="${i}" checked><span><b>${esc(p.titulo)}</b> · ${esc(p.tipo)} ${esc(PARA[p.para] || "")} en ${esc(p.dias)} día(s)</span></label>`;
      });
      s += `<span class="muted small">Las acciones marcadas quedan programadas; si la automatización está apagada, esperan aprobación del líder.</span>`;
    }
    der.innerHTML = s;
    const motivo = $("[data-ia-motivo]", form);
    motivo.classList.toggle("hidden", v === "ok" || v === "fuerte" || v === "media");
    $("[name=revision_id]", form).value = d.revision_id || "";
    $("[data-btn-sug]", form).classList.toggle("hidden", !d.sugerencia || v === "ok");
    $("[data-btn-propio]", form).textContent = (v === "ok" || v === "fuerte" || v === "media") ? "Guardar" : "Guardar como está";
    box.classList.remove("hidden", "stale");
    $(".stale-aviso", box)?.remove();
    $("[data-ia-acciones]", form).classList.remove("hidden");
    $("[data-btn-revisar]", form).classList.add("hidden");
  }

  $$("form[data-ia]").forEach((form) => {
    const btn = $("[data-btn-revisar]", form);
    // si se edita después de revisar, hay que volver a revisar
    // si se edita después de revisar: la retroalimentación se queda a la vista (sirve de guía mientras se corrige),
    // pero queda marcada como desactualizada y solo se puede volver a revisar; no se puede guardar ni usar la sugerencia.
    const marcarDesactualizada = () => {
      if (!$("[name=revision_id]", form).value) return;
      $("[name=revision_id]", form).value = "";
      const box = $("[data-ia-resultado]", form);
      box.classList.add("stale");
      if (!$(".stale-aviso", box)) {
        const a = document.createElement("div");
        a.className = "stale-aviso";
        a.innerHTML = `<b>Modificaste los datos.</b> Esta revisión corresponde a la versión anterior; úsala como guía y pide que el PM virtual revise de nuevo para poder guardar.`;
        box.prepend(a);
      }
      $$("input[name=plan_sel]", box).forEach((c) => { c.disabled = true; });
      $("[data-ia-motivo]", form).classList.add("hidden");
      $("[data-ia-acciones]", form).classList.add("hidden");
      btn.innerHTML = btn.dataset.txtNuevo || btn.innerHTML;
      btn.classList.remove("hidden");
    };
    if (!btn.dataset.txtNuevo) btn.dataset.txtNuevo = btn.innerHTML.replace(/Revisar con IA|Evaluar con IA/, "Revisar de nuevo con IA");
    $$("[data-revisable]", form).forEach((el) => {
      el.addEventListener("input", marcarDesactualizada);
      el.addEventListener("change", marcarDesactualizada);
    });
    btn.addEventListener("click", async () => {
      if (!form.reportValidity()) return;
      const txt = btn.innerHTML;
      btn.disabled = true; btn.innerHTML = `<span class="spin"></span> El PM virtual está revisando…`;
      const err = $("[data-ia-error]", form); err.classList.add("hidden");
      try {
        const r = await fetch(form.dataset.ia, { method: "POST", body: new FormData(form), credentials: "same-origin" });
        const d = await r.json();
        if (d.ok) pintar(form, d);
        else {
          err.textContent = d.mensaje || "No se pudo revisar."; err.classList.remove("hidden");
          if (d.sin_ia) { $("[data-ia-acciones]", form).classList.remove("hidden"); btn.classList.add("hidden"); $("[data-btn-sug]", form).classList.add("hidden"); }
        }
      } catch (e) {
        err.textContent = "Sin conexión con el servidor."; err.classList.remove("hidden");
      } finally { btn.disabled = false; btn.innerHTML = txt; }
    });
    form.addEventListener("submit", (e) => {
      const dec = e.submitter?.value;
      const mot = $("[name=motivo]", form);
      if (dec === "propio" && !$("[data-ia-motivo]", form).classList.contains("hidden") && $("[name=revision_id]", form).value && !mot.value.trim()) {
        e.preventDefault(); mot.focus(); mot.reportValidity?.();
        $("[data-ia-error]", form).textContent = "Explica por qué guardas sin la sugerencia; ayuda a calibrar al PM virtual.";
        $("[data-ia-error]", form).classList.remove("hidden");
      }
    });
  });
})();

/* Evita envíos dobles: al enviar un formulario se bloquean sus botones y, si el proceso es largo (IA o correos),
   se muestra un aviso que cubre la pantalla hasta que el servidor responde. */
(() => {
  const LARGO = /\/(pm\/informe|pm\/recordatorios|acciones\/(nueva|ejecutar)|diagnostico\/generar|consolidar|analizar)$|\/sesiones$/;
  const MENSAJES = {
    "pm/informe": "El PM virtual está generando el informe…",
    "acciones/nueva": "El PM virtual está redactando el documento…",
    "pm/recordatorios": "Preparando recordatorios…",
    "acciones/ejecutar": "Ejecutando acciones programadas…",
    "diagnostico/generar": "La IA está preparando el diagnóstico del área…",
    "consolidar": "La IA está consolidando las iniciativas…",
    "analizar": "La IA está analizando…",
    "sesiones": "Cargando y analizando la sesión…",
  };
  const mensaje = (url) => {
    for (const k in MENSAJES) if (url.includes(k)) return MENSAJES[k];
    return "Procesando…";
  };
  function aviso(texto) {
    const d = document.createElement("div");
    d.className = "espera";
    d.setAttribute("role", "status");
    d.setAttribute("aria-live", "polite");
    d.innerHTML = `<div class="caja"><span class="spin lg"></span><div><b></b><div class="small muted">Puede tardar hasta un minuto. No cierres ni recargues la página; te llevamos al resultado al terminar.</div></div></div>`;
    d.querySelector("b").textContent = texto;
    document.body.appendChild(d);
  }
  document.addEventListener("submit", (e) => {
    const f = e.target;
    if (e.defaultPrevented || (f.getAttribute("method") || "get").toLowerCase() !== "post" || f.hasAttribute("data-sin-bloqueo")) return;
    if (f.dataset.enviando) { e.preventDefault(); return; }
    f.dataset.enviando = "1";
    const sub = e.submitter;
    if (sub && sub.name) {  // un botón deshabilitado no viaja en el formulario: se copia su valor
      const h = document.createElement("input");
      h.type = "hidden"; h.name = sub.name; h.value = sub.value; f.appendChild(h);
    }
    const accion = new URL(sub?.getAttribute("formaction") || f.action, location.href).pathname;
    const largo = LARGO.test(accion) || sub?.classList.contains("aibtn");
    f.querySelectorAll("button, input[type=submit]").forEach((b) => { if (b.type !== "button") b.disabled = true; });
    if (sub) { sub.dataset.txt = sub.innerHTML; sub.innerHTML = `<span class="spin"></span>${largo ? "Trabajando…" : "Guardando…"}`; }
    if (largo) aviso(mensaje(accion));
  });
  // si el usuario regresa con el botón "atrás", la página vuelve a quedar usable
  window.addEventListener("pageshow", (e) => {
    if (!e.persisted) return;
    document.querySelectorAll(".espera").forEach((x) => x.remove());
    document.querySelectorAll("form[data-enviando]").forEach((f) => {
      delete f.dataset.enviando;
      f.querySelectorAll("button, input[type=submit]").forEach((b) => { b.disabled = false; if (b.dataset.txt) b.innerHTML = b.dataset.txt; });
    });
  });
})();
