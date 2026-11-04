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
  const CAMPOS = ["descripcion", "producto", "menu", "pantalla", "tipo", "impacto", "titulo", "pasos", "referencia", "cliente"];
  const st = { modo: datos.ia ? "ia" : "manual", historial: [], faltan: [], completo: false, ocupado: false,
               tituloManual: false, sugerido: "", leyendo: false, omitidos: new Set(), respondiendo: null };
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
      if (k === "titulo" && st.tituloManual) return;
      if (k === "titulo" || !el.value.trim() || el.dataset.auto === "1") {
        el.dataset.auto = "1";
        el.value = v;
        el.classList.add("tkt-lleno");
        el.dispatchEvent(new Event("change", { bubbles: true }));
        if (el.classList.contains("tkt-auto")) crecer(el);
      }
    });
    guardarBorrador();
  }

  // cada pregunta trae sus propias decisiones: responder, adjuntar o «no lo tengo»
  function tarjeta(f) {
    const adj = f.campo === "anexo" || f.campo === "referencia";
    return `<div class="tkt-q" data-campo="${esc(f.campo)}"><b>${esc(f.pregunta)}</b>` +
      (f.por_que ? `<small>${esc(f.por_que)}</small>` : "") +
      `<div class="tkt-quick">${f.campo !== "anexo" ? `<button type="button" data-q="resp">Responder</button>` : ""}` +
      `${adj ? `<button type="button" data-q="adjuntar">📎 Adjuntar</button>` : ""}<button type="button" data-q="no">No lo tengo</button></div></div>`;
  }
  function cierre() {
    return st.faltan.length
      ? '<div class="tkt-quick tkt-fin"><button type="button" data-q="asi">Levantar así</button></div>'
      : '<div class="tkt-quick tkt-fin"><button type="button" data-q="ya" class="si">Levantar ticket</button></div>';
  }
  function pintarRespuesta(r) {
    llenar(Object.assign({}, r.campos, r.titulo ? { titulo: r.titulo } : {}));
    if (datos.prompt_version !== r.prompt_version && r.prompt_version) datos.prompt_version = r.prompt_version;
    st.faltan = (r.faltan || []).filter((f) => !st.omitidos.has(f.campo));
    st.completo = !st.faltan.length;
    marcar();
    // las preguntas anteriores ya resueltas se marcan y solo queda un botón final vigente
    $$(".tkt-fin", msgs).forEach((x) => x.remove());
    $$(".tkt-q:not(.omitido)", msgs).forEach((c) => {
      if (!st.faltan.some((f) => f.campo === c.dataset.campo)) { c.classList.add("hecho"); const q = $(".tkt-quick", c); if (q) q.innerHTML = "<small>✓ Respondido</small>"; }
      else c.remove();
    });
    let html = esc(r.mensaje || (st.completo ? "Listo, ya tiene lo necesario." : "Me falta:"));
    if (st.faltan.length) html += st.faltan.map(tarjeta).join("");
    html += cierre();
    if ((r.similares || []).length) {
      html += '<div class="tkt-sim"><b>¿Es alguno de estos? Ya se resolvieron:</b>' + r.similares.map((s) =>
        `<a href="/tickets/${s.id}" target="_blank" rel="noopener">${esc(s.folio)} · ${esc(s.titulo)}</a><span>${esc((s.solucion || "").slice(0, 180))}</span>`).join("") + "</div>";
    }
    mensaje("g", html);
    ocupado(false);
  }
  function omitir(campo, tarjetaEl) {
    st.omitidos.add(campo);
    st.faltan = st.faltan.filter((f) => f.campo !== campo);
    if (tarjetaEl) { tarjetaEl.classList.add("omitido"); $(".tkt-quick", tarjetaEl).innerHTML = "<small>No lo tienes · el equipo lo sabrá</small>"; }
    marcar();
    st.completo = !st.faltan.length;
    $$(".tkt-fin").forEach((x) => x.remove());
    const ultimo = msgs.lastElementChild;
    if (st.completo) mensaje("g", "Listo, ya puedes levantarlo." + cierre());
    else if (ultimo) ultimo.insertAdjacentHTML("beforeend", cierre());
    ocupado(false);
  }

  // ------------------------------------------------------------ llamadas a GuardIA
  async function llamar(extra) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 40000);
    try {
      const r = await fetch("/api/tickets/revisar", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: ctl.signal,
        body: JSON.stringify(Object.assign({ campos: valores(), anexos: nombresAnexos(), historial: st.historial,
                                             omitidos: Array.from(st.omitidos) }, extra || {})),
      });
      if (r.status === 401) { error("Tu sesión expiró. Copia tu texto y vuelve a entrar."); return null; }
      const d = await r.json();
      if (!d.ok) { manual(d.mensaje); return null; }
      return d;
    } catch (e) {
      manual(e && e.name === "AbortError" ? "GuardIA tardó demasiado en responder." : "Sin conexión con el servidor.");
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
    const sobre = st.respondiendo;
    st.respondiendo = null;
    chat.placeholder = "Escribe aquí…";
    const paraIA = sobre ? `Respuesta a «${sobre.pregunta}»: ${texto}` : texto;
    ocupado(true, "GuardIA responde…");
    const w = pensando();
    const r = await llamar({ mensaje: paraIA });
    w.remove();
    st.historial.push({ de: "u", t: paraIA });
    if (!r) return;
    st.historial.push({ de: "g", t: r.mensaje || "" });
    pintarRespuesta(r);
  }

  // ------------------------------------------------------------ modo manual (GuardIA no respondió)
  // marca en las etiquetas qué datos pide el tipo: «recomendado» con GuardIA, «*» obligatorio sin GuardIA
  const MAPA = { pantalla: ["pantalla"], pasos: ["pasos"], referencia: ["referencia"] };
  function etiquetas(n) {
    const el = campo(n);
    return el ? [form.querySelector(`label[for="tk-${n}"]`)].filter(Boolean) : [];
  }
  function marcarTipo() {
    $$("[data-dyn]", form).forEach((x) => x.remove());
    ["pantalla", "pasos", "referencia"].forEach((n) => { campo(n).required = false; });
    const tipo = campo("tipo").value;
    if (!tipo) return;
    (datos.requeridos[tipo] || []).forEach((c) => {
      const labels = c === "anexo" ? [$('[data-req="anexo"] > label', form)] : [].concat(...(MAPA[c] || []).map(etiquetas));
      labels.filter(Boolean).forEach((l) => {
        const s = document.createElement("span");
        s.dataset.dyn = "1";
        if (st.modo === "manual" && c !== "anexo") { s.className = "ast"; s.textContent = " *"; }
        else { s.className = "rec"; s.textContent = "recomendado"; }
        l.appendChild(s);
      });
      if (st.modo === "manual") (MAPA[c] || []).forEach((n) => { campo(n).required = true; });
    });
  }
  const requeridosManual = marcarTipo;
  function manual(motivo) {
    if (motivo) $("#tk-sinia-motivo").textContent = "Motivo: " + motivo;
    if (st.modo === "manual" && !$("#tk-sinia").hidden) { ocupado(false); return; }
    st.modo = "manual"; st.faltan = []; st.completo = false;
    marcar(); cerrar(); tab.hidden = true;
    $("#tk-sinia").hidden = false;
    campo("titulo").placeholder = "Se arma solo al levantarlo (o escríbelo tú)";
    $("#tk-sep-nota").innerHTML = 'Los marcados con <span class="ast">*</span> son obligatorios para este tipo de ticket.';
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
    if (st.omitidos.size && validacion === "completo") validacion = "incompleto";
    fd.append("validacion", validacion);
    fd.append("faltantes", JSON.stringify(Array.from(new Set([...st.omitidos, ...(validacion === "incompleto" ? st.faltan.map((f) => f.campo) : [])]))));
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
    const q = b.dataset.q, card = b.closest(".tkt-q"), campo_ = card && card.dataset.campo;
    if (q === "adjuntar") input.click();
    else if (q === "no") omitir(campo_, card);
    else if (q === "resp") {
      st.respondiendo = { campo: campo_, pregunta: $("b", card).textContent };
      chat.placeholder = "Tu respuesta: " + st.respondiendo.pregunta;
      chat.focus();
    }
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

  // ------------------------------------------------------------ mientras escribe: título y campos sugeridos
  const lee = $("#tk-lee"), leeTxt = lee.innerHTML;
  async function sugerir() {
    const desc = campo("descripcion").value.trim();
    if (st.modo !== "ia" || st.ocupado || st.leyendo || desc.length < 40 || desc === st.sugerido) return;
    st.leyendo = true; st.sugerido = desc;
    lee.innerHTML = '<span class="spin" style="width:12px;height:12px;border-width:2px"></span> GuardIA está leyendo tu descripción…';
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 30000);
    try {
      const r = await fetch("/api/tickets/revisar", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: ctl.signal,
        body: JSON.stringify({ campos: valores(), anexos: nombresAnexos(), solo_llenar: true }),
      });
      const d = await r.json();
      if (d.ok) { llenar(Object.assign({}, d.campos, d.titulo ? { titulo: d.titulo } : {})); cascada(); marcarTipo(); }
    } catch (e) { /* la sugerencia es opcional: si falla, se revisa al levantar */ }
    finally { clearTimeout(t); st.leyendo = false; lee.innerHTML = leeTxt; }
  }
  // RFC en la descripción → cliente al instante (GuardIA lo afina después con la razón social)
  const RFC = /\b([A-ZÑ&]{3,4})\s?-?(\d{6})\s?-?([A-Z0-9]{3})\b/;
  function clienteDesdeRFC() {
    const c = campo("cliente");
    if (c.value.trim() && c.dataset.auto !== "1") return;
    for (const linea of campo("descripcion").value.split(/\n/)) {
      const m = linea.toUpperCase().match(RFC);
      if (!m) continue;
      const rfc = m[1] + m[2] + m[3];
      const resto = linea.slice(m.index + m[0].length).replace(/^[\s,:;\-–]+/, "").trim();
      const nombre = /^[A-ZÁÉÍÓÚÑ0-9&.,\s]{4,120}$/.test(resto.toUpperCase()) && resto ? resto.replace(/\s{2,}/g, " ") : "";
      const v = nombre ? `${nombre} (${rfc})` : rfc;
      if (c.value !== v) { c.value = v; c.dataset.auto = "1"; c.classList.add("tkt-lleno"); }
      return;
    }
  }
  // GuardIA solo lee la descripción cuando el usuario sale del campo (o al levantar), no mientras escribe:
  // así no se gastan tokens en cada pausa. El RFC se detecta localmente, sin IA.
  campo("descripcion").addEventListener("input", clienteDesdeRFC);
  campo("descripcion").addEventListener("blur", sugerir);

  $("#tk-titulo-ed").addEventListener("click", () => {
    const t = campo("titulo"); t.readOnly = false; st.tituloManual = true; t.focus(); t.select();
  });

  function crecer(el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight + 2, 320) + "px"; el.style.overflowY = el.scrollHeight > 320 ? "auto" : "hidden"; }
  $$(".tkt-auto", form).forEach((el) => { el.addEventListener("input", () => crecer(el)); });

  // ------------------------------------------------------------ catálogo en cascada
  function opciones(id, lista) { $("#" + id).innerHTML = lista.map((x) => `<option value="${esc(x)}">`).join(""); }
  function cascada() {
    const prod = datos.catalogo[campo("producto").value] || {};
    opciones("tk-l-menu", Object.keys(prod));
    opciones("tk-l-pantalla", prod[campo("menu").value] || [].concat(...Object.values(prod)));
  }
  ["producto", "menu"].forEach((n) => campo(n).addEventListener("change", cascada));
  campo("tipo").addEventListener("change", marcarTipo);

  // cualquier cambio después de una revisión obliga a revisar de nuevo (menos el título)
  form.addEventListener("input", (e) => {
    if (e.target.name && e.target.name !== "titulo" && st.completo && st.modo === "ia" && !e.target.classList.contains("tkt-lleno")) {
      st.completo = false; ocupado(false);
    }
    e.target.classList.remove("tkt-lleno");
    if (e.target.dataset) delete e.target.dataset.auto;
    guardarBorrador();
  });

  // ------------------------------------------------------------ borrador local
  function guardarBorrador() { try { localStorage.setItem(BORRADOR, JSON.stringify(valores())); } catch (e) { /* sin almacenamiento */ } }
  try {
    const b = JSON.parse(localStorage.getItem(BORRADOR) || "null");
    if (b) CAMPOS.forEach((n) => { if (b[n]) campo(n).value = b[n]; });
  } catch (e) { /* sin almacenamiento */ }
  cascada();
  clienteDesdeRFC();
  marcarTipo();
  $$(".tkt-auto", form).forEach(crecer);
  if (st.modo === "manual") manual(datos.ia ? "" : "La IA no está configurada en el servidor.");
})();
