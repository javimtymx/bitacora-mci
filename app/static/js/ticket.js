/* GuardIA Ticket · captura híbrida.
   El usuario llena el formulario y da «Levantar ticket». GuardIA revisa: si está completo se levanta directo;
   si falta algo se abre el panel lateral y pregunta solo eso. A la primera falla de GuardIA, modo manual. */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const datos = JSON.parse($("#tk-datos").textContent);
  const form = $("#tk-form"), lay = $("#tk-lay"), panel = $("#tk-panel"), tab = $("#tk-tab");
  const msgs = $("#tk-msgs"), chat = $("#tk-chat"), btn = $("#tk-levantar"), btnTxt = $("#tk-levantar span");
  const input = $("#tk-archivos"), err = $("#tk-error");
  const CAMPOS = ["descripcion", "producto", "menu", "pantalla", "tipo", "impacto", "titulo", "pasos", "esperado",
                  "obtenido", "referencia", "cliente"];
  const st = { modo: datos.ia ? "ia" : "manual", historial: [], faltan: [], completo: false, ocupado: false };
  let archivos = new DataTransfer();
  const BORRADOR = "tk-borrador";

  // ------------------------------------------------------------ utilidades
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const campo = (n) => form.elements[n];
  function valores() { const o = {}; CAMPOS.forEach((n) => { o[n] = (campo(n).value || "").trim(); }); return o; }
  function nombresAnexos() { return Array.from(archivos.files).map((f) => f.name); }
  function error(t) { if (!t) { err.hidden = true; return; } err.textContent = t; err.hidden = false; err.scrollIntoView({ behavior: "smooth", block: "center" }); }
  function ocupado(si, texto) {
    st.ocupado = si; btn.disabled = si; $("#tk-enviar").disabled = si;
    btnTxt.textContent = si ? (texto || "GuardIA revisa…") : (st.completo ? "Levantar ticket ✓" : "Levantar ticket");
  }

  // ------------------------------------------------------------ panel lateral
  function abrir() { panel.hidden = false; tab.hidden = true; lay.classList.add("open"); tab.setAttribute("aria-expanded", "true"); }
  function cerrar() { panel.hidden = true; if (st.modo === "ia") tab.hidden = false; lay.classList.remove("open"); tab.setAttribute("aria-expanded", "false"); }
  tab.addEventListener("click", () => { abrir(); chat.focus(); });
  $("#tk-ocultar").addEventListener("click", cerrar);

  function mensaje(de, html) {
    const d = document.createElement("div");
    d.className = "tkt-msg " + de;
    d.innerHTML = html;
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
    return d;
  }
  function pensando() { return mensaje("g wait", '<span class="tkt-dots"><i></i><i></i><i></i></span>'); }

  // ------------------------------------------------------------ marcas en el formulario
  function marcar() {
    $$("[data-req]", form).forEach((el) => { el.classList.remove("tkt-falta"); const x = $(".tkt-falta-txt", el); if (x) x.remove(); });
    st.faltan.forEach((f) => {
      const els = $$(`[data-req="${f.campo}"]`, form);
      els.forEach((el, i) => {
        el.classList.add("tkt-falta");
        if (i === els.length - 1) { const s = document.createElement("span"); s.className = "tkt-falta-txt"; s.textContent = "GuardIA lo pide · ver panel"; el.appendChild(s); }
      });
    });
  }
  function llenar(nuevos) {
    Object.entries(nuevos || {}).forEach(([k, v]) => {
      const el = campo(k);
      if (!el || !v) return;
      if (k === "titulo" || !el.value.trim()) {
        el.value = v;
        el.classList.add("tkt-lleno");
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
    guardarBorrador();
  }

  function pintarRespuesta(r) {
    llenar(Object.assign({}, r.campos, r.titulo ? { titulo: r.titulo } : {}));
    if (datos.prompt_version !== r.prompt_version && r.prompt_version) datos.prompt_version = r.prompt_version;
    st.faltan = r.faltan || [];
    st.completo = !st.faltan.length;
    marcar();
    let html = esc(r.mensaje || (st.completo ? "Listo, tu ticket tiene lo necesario." : "Para que el equipo lo atienda a la primera me falta:"));
    if (st.faltan.length) {
      html += "<ul>" + st.faltan.map((f) => `<li><b>${esc(f.pregunta)}</b>${f.por_que ? `<div class="tkt-why">Por qué: ${esc(f.por_que)}</div>` : ""}</li>`).join("") + "</ul>";
      const q = ['<div class="tkt-quick">'];
      if (st.faltan.some((f) => f.campo === "anexo" || f.campo === "referencia")) q.push('<button type="button" data-q="adjuntar">📎 Adjuntar archivo</button>');
      q.push('<button type="button" data-q="no">No lo tengo</button><button type="button" data-q="asi">Levantar así</button></div>');
      html += q.join("");
    } else {
      html += '<div class="tkt-quick"><button type="button" data-q="ya">Levantar ticket</button></div>';
    }
    if ((r.similares || []).length) {
      html += '<div class="tkt-sim"><b>¿Es alguno de estos? Ya se resolvieron:</b>' + r.similares.map((s) =>
        `<a href="/tickets/${s.id}" target="_blank" rel="noopener">${esc(s.folio)} · ${esc(s.titulo)}</a><span>${esc((s.solucion || "").slice(0, 180))}</span>`).join("") + "</div>";
    }
    mensaje("g", html);
    ocupado(false);
  }

  // ------------------------------------------------------------ llamadas a GuardIA
  async function llamar(extra) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 25000);
    try {
      const r = await fetch("/api/tickets/revisar", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: ctl.signal,
        body: JSON.stringify(Object.assign({ campos: valores(), anexos: nombresAnexos(), historial: st.historial }, extra || {})),
      });
      if (r.status === 401) { error("Tu sesión expiró. Copia tu texto y vuelve a entrar."); return null; }
      const d = await r.json();
      if (!d.ok) { manual(); return null; }
      return d;
    } catch (e) {
      manual();
      return null;
    } finally { clearTimeout(t); }
  }

  async function revisar() {
    ocupado(true);
    const w = panel.hidden ? null : pensando();
    const r = await llamar();
    if (w) w.remove();
    if (!r) return;
    if (r.completo) { llenar(Object.assign({}, r.campos, { titulo: r.titulo })); st.completo = true; enviar("completo"); return; }
    abrir();
    pintarRespuesta(r);
  }

  async function conversar(texto) {
    texto = (texto || "").trim();
    if (!texto || st.ocupado) return;
    mensaje("u", esc(texto));
    chat.value = "";
    ocupado(true, "GuardIA responde…");
    const w = pensando();
    const r = await llamar({ mensaje: texto });
    w.remove();
    st.historial.push({ de: "u", t: texto });
    if (!r) return;
    st.historial.push({ de: "g", t: r.mensaje || "" });
    pintarRespuesta(r);
  }

  // ------------------------------------------------------------ modo manual (GuardIA no respondió)
  function requeridosManual() {
    const tipo = campo("tipo").value || "error";
    const req = datos.requeridos[tipo] || [];
    const mapa = { pantalla: ["pantalla"], pasos: ["pasos"], esperado_obtenido: ["esperado", "obtenido"], referencia: ["referencia"] };
    ["pantalla", "pasos", "esperado", "obtenido", "referencia"].forEach((n) => {
      campo(n).required = false;
      const l = form.querySelector(`label[for="tk-${n}"]`); if (l) l.textContent = l.textContent.replace(/ \*$/, "");
    });
    req.forEach((c) => (mapa[c] || []).forEach((n) => {
      campo(n).required = true;
      const l = form.querySelector(`label[for="tk-${n}"]`); if (l && !/\*$/.test(l.textContent)) l.textContent += " *";
    }));
  }
  function manual() {
    if (st.modo === "manual" && !$("#tk-sinia").hidden) { ocupado(false); return; }
    st.modo = "manual"; st.faltan = []; st.completo = false;
    marcar(); cerrar(); tab.hidden = true;
    $("#tk-sinia").hidden = false;
    $("#tk-titulo").placeholder = "Se arma solo con producto, pantalla y descripción";
    $("#tk-pie").textContent = "Tu borrador se guarda en cada cambio. El equipo revisa tu ticket aunque GuardIA no esté.";
    requeridosManual();
    ocupado(false);
    $("#tk-sinia").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // ------------------------------------------------------------ envío
  async function enviar(validacion) {
    error("");
    const fd = new FormData(form);
    fd.delete("archivos");
    Array.from(archivos.files).forEach((f) => fd.append("archivos", f, f.name));
    fd.append("validacion", validacion);
    fd.append("faltantes", JSON.stringify(validacion === "incompleto" ? st.faltan.map((f) => f.campo) : []));
    fd.append("conversacion", JSON.stringify(st.historial));
    fd.append("prompt_version", datos.prompt_version || "");
    ocupado(true, "Levantando…");
    try {
      const r = await fetch("/api/tickets/nuevo", { method: "POST", body: fd });
      const d = await r.json().catch(() => ({ ok: false, mensaje: "No se pudo levantar el ticket. Intenta de nuevo." }));
      if (d.ok) { try { localStorage.removeItem(BORRADOR); } catch (e) { /* sin almacenamiento */ } location.href = d.url; return; }
      error(d.mensaje || "No se pudo levantar el ticket.");
    } catch (e) {
      error("Sin conexión con el servidor. Tu información sigue aquí; intenta de nuevo.");
    }
    ocupado(false);
  }

  btn.addEventListener("click", () => {
    if (st.ocupado) return;
    error("");
    if (!form.reportValidity()) return;
    if (st.modo === "manual") { enviar("sin_ia"); return; }
    if (st.completo) { enviar("completo"); return; }
    revisar();
  });

  msgs.addEventListener("click", (e) => {
    const b = e.target.closest("[data-q]");
    if (!b || st.ocupado) return;
    const q = b.dataset.q;
    if (q === "adjuntar") input.click();
    else if (q === "no") conversar("No lo tengo");
    else if (q === "asi") enviar("incompleto");
    else if (q === "ya") enviar(st.faltan.length ? "incompleto" : "completo");
  });
  $("#tk-enviar").addEventListener("click", () => conversar(chat.value));
  chat.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); conversar(chat.value); } });
  chat.addEventListener("input", () => { chat.style.height = "auto"; chat.style.height = Math.min(chat.scrollHeight, 140) + "px"; });

  // ------------------------------------------------------------ anexos
  function pintarArchivos() {
    const cont = $("#tk-files");
    cont.innerHTML = Array.from(archivos.files).map((f, i) =>
      `<span class="tkt-file">${esc(f.name)} <small>${(f.size / 1024).toFixed(0)} KB</small><button type="button" data-x="${i}" title="Quitar">×</button></span>`).join("");
    $("#tk-files-vacio").hidden = archivos.files.length > 0;
  }
  input.addEventListener("change", () => {
    const grandes = [];
    Array.from(input.files).forEach((f) => { if (f.size > 10 * 1024 * 1024) grandes.push(f.name); else archivos.items.add(f); });
    input.value = "";
    if (grandes.length) error("Pasan de 10 MB: " + grandes.join(", "));
    pintarArchivos();
    if (archivos.files.length && st.faltan.some((f) => f.campo === "anexo")) {
      st.faltan = st.faltan.filter((f) => f.campo !== "anexo");
      marcar();
      st.completo = !st.faltan.length;
      mensaje("g", st.completo ? 'Recibí el archivo ✓. Ya tiene lo necesario.<div class="tkt-quick"><button type="button" data-q="ya">Levantar ticket</button></div>'
                               : "Recibí el archivo ✓. Solo falta lo que marqué en rojo.");
      ocupado(false);
    }
  });
  $("#tk-files").addEventListener("click", (e) => {
    const b = e.target.closest("[data-x]");
    if (!b) return;
    const nuevo = new DataTransfer();
    Array.from(archivos.files).forEach((f, i) => { if (i !== +b.dataset.x) nuevo.items.add(f); });
    archivos = nuevo;
    pintarArchivos();
  });

  // ------------------------------------------------------------ catálogo en cascada
  function opciones(id, lista) { $("#" + id).innerHTML = lista.map((x) => `<option value="${esc(x)}">`).join(""); }
  function cascada() {
    const prod = datos.catalogo[campo("producto").value] || {};
    opciones("tk-l-menu", Object.keys(prod));
    opciones("tk-l-pantalla", prod[campo("menu").value] || [].concat(...Object.values(prod)));
  }
  ["producto", "menu"].forEach((n) => campo(n).addEventListener("change", cascada));
  campo("tipo").addEventListener("change", () => { if (st.modo === "manual") requeridosManual(); });

  // cualquier cambio después de una revisión obliga a revisar de nuevo (menos el título)
  form.addEventListener("input", (e) => {
    if (e.target.name && e.target.name !== "titulo" && st.completo && st.modo === "ia" && !e.target.classList.contains("tkt-lleno")) {
      st.completo = false; ocupado(false);
    }
    e.target.classList.remove("tkt-lleno");
    guardarBorrador();
  });

  // ------------------------------------------------------------ borrador local
  function guardarBorrador() { try { localStorage.setItem(BORRADOR, JSON.stringify(valores())); } catch (e) { /* sin almacenamiento */ } }
  try {
    const b = JSON.parse(localStorage.getItem(BORRADOR) || "null");
    if (b) CAMPOS.forEach((n) => { if (b[n]) campo(n).value = b[n]; });
  } catch (e) { /* sin almacenamiento */ }
  cascada();
  if (st.modo === "manual") manual();
})();
