/* Vista de tablero de tareas: filtros, grupos plegables, subtareas y alta rápida de compromisos con revisión de GuardIA. */
(function () {
  const raiz = document.querySelector(".tt");
  if (!raiz) return;
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const norm = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const recargar = () => { try { localStorage.setItem("tablero-vista", "tablero"); } catch (e) {} location.reload(); };

  async function post(url, datos, json) {
    const r = await fetch(url, { method: "POST", body: datos, headers: json ? { Accept: "application/json" } : {} });
    let d = {};
    try { d = await r.json(); } catch (e) { d = {}; }
    if (!r.ok && d.ok === undefined) d = { ok: false, mensaje: d.detail || "No se pudo guardar." };
    return d;
  }

  // ---------------------------------------------------------- filtros
  function filtrar() {
    const q = norm($("[data-tt-buscar]", raiz).value.trim());
    const per = $("[data-tt-persona]", raiz).value;
    const est = $("[data-tt-estado]", raiz).value;
    $$(".tt-fila", raiz).forEach((f) => {
      const ok = (!q || norm(f.dataset.texto).includes(q)) && (!per || f.dataset.persona === per) &&
        (!est || (est === "abierto" ? f.dataset.abierto === "1" : f.dataset.estado === est));
      f.hidden = !ok;
      $$(`[data-sub-de="${f.dataset.comp}"]`, raiz).forEach((s) => { if (!ok) s.hidden = true; else s.hidden = !f.classList.contains("abierta"); });
    });
  }
  ["input", "change"].forEach((ev) => raiz.addEventListener(ev, (e) => {
    if (e.target.matches("[data-tt-buscar],[data-tt-persona],[data-tt-estado]")) filtrar();
  }));

  // ---------------------------------------------------------- grupos y subtareas
  function verSubs(fila, abrir) {
    fila.classList.toggle("abierta", abrir);
    const b = $("[data-tt-sub]", fila);
    if (b) { b.setAttribute("aria-expanded", abrir ? "true" : "false"); b.firstChild.textContent = abrir ? "▾" : "▸"; }
    $$(`[data-sub-de="${fila.dataset.comp}"]`, raiz).forEach((s) => { s.hidden = !abrir || fila.hidden; });
  }
  raiz.addEventListener("click", async (e) => {
    const col = e.target.closest("[data-tt-colapsar]");
    if (col) {
      const g = col.closest("[data-grupo]");
      const plegado = g.classList.toggle("plegado");
      col.setAttribute("aria-expanded", plegado ? "false" : "true");
      col.textContent = plegado ? "▸" : "▾";
      return;
    }
    const sb = e.target.closest("[data-tt-sub]");
    if (sb) { const f = sb.closest(".tt-fila"); verSubs(f, !f.classList.contains("abierta")); return; }
    const todos = e.target.closest("[data-tt-subs]");
    if (todos) { $$(".tt-fila", raiz).forEach((f) => verSubs(f, todos.dataset.ttSubs === "1")); return; }
    const q = e.target.closest("[data-tt-quitar]");
    if (q) {
      const d = await post(`/subtareas/${q.dataset.ttQuitar}/quitar`, new FormData());
      if (d.ok) recargar(); else alert(d.mensaje || "No se pudo quitar.");
    }
  });
  raiz.addEventListener("change", async (e) => {
    const ch = e.target.closest("[data-tt-marcar]");
    if (!ch) return;
    const fd = new FormData(); fd.append("hecha", ch.checked ? "true" : "false");
    const d = await post(`/subtareas/${ch.dataset.ttMarcar}/marcar`, fd);
    if (!d.ok) { ch.checked = !ch.checked; alert(d.mensaje || "No se pudo guardar."); return; }
    ch.nextElementSibling.classList.toggle("hecha", ch.checked);
    const fila = $(`.tt-fila[data-comp="${ch.closest("[data-sub-de]").dataset.subDe}"]`, raiz);
    const subs = $$(`[data-sub-de="${fila.dataset.comp}"] [data-tt-marcar]`, raiz);
    $("[data-tt-sub] span", fila).textContent = `${subs.filter((x) => x.checked).length}/${subs.length}`;
  });

  // ---------------------------------------------------------- alta de subtarea
  raiz.addEventListener("submit", async (e) => {
    const sf = e.target.closest("[data-tt-subform]");
    if (sf) {
      e.preventDefault();
      if (!sf.texto.value.trim()) return;
      const d = await post(`/compromisos/${sf.dataset.ttSubform}/subtareas`, new FormData(sf));
      if (d.ok) recargar(); else alert(d.mensaje || "No se pudo agregar.");
      return;
    }
    const alta = e.target.closest("[data-tt-alta]");
    if (alta) { e.preventDefault(); agregar(alta); }
  });

  // ---------------------------------------------------------- alta rápida de compromiso (mismas reglas que el formulario)
  const VER = { ok: ["g", "Listo para guardar"], fuerte: ["g", "Fuerte"], media: ["b", "Aceptable"], debil: ["o", "Débil"], mejorar: ["o", "Se puede mejorar"], repetido: ["r", "Repite lo que no funcionó"],
    muy_debil: ["r", "Muy débil"], no_aplica: ["r", "No mueve esta medida"] };
  const FAVORABLES = ["ok", "fuerte", "media"];

  function datos(alta) {
    const fd = new FormData();
    fd.append("area_id", raiz.dataset.area);
    fd.append("predictiva_id", alta.dataset.pred);
    fd.append("responsable_id", alta.responsable_id.value);
    fd.append("texto", alta.texto.value.trim());
    fd.append("fecha_limite", alta.fecha_limite.value);
    return fd;
  }
  async function crear(alta, extra) {
    const fd = datos(alta);
    Object.entries(extra || {}).forEach(([k, v]) => fd.append(k, v));
    const d = await post("/compromisos", fd, true);
    if (d.ok) recargar();
    else mostrar(alta, `<div class="flash error small" style="margin:0">${esc(d.mensaje || "No se pudo guardar.")}</div>`);
  }
  function mostrar(alta, html) { const box = $(".tt-ia", alta); box.innerHTML = html; box.hidden = !html; }

  async function agregar(alta) {
    if (alta.texto.value.trim().length < 5) { alta.texto.focus(); return; }
    if (!alta.fecha_limite.value) { alta.fecha_limite.focus(); return; }
    const btn = $("button[type=submit]", alta);
    btn.disabled = true;
    mostrar(alta, `<span class="muted small">Guard<b>IA</b> está revisando el compromiso…</span>`);
    try {
      const r = await post("/api/ia/compromiso", datos(alta));
      if (!r.ok) {
        if (r.sin_ia) return await crear(alta, { decision: "ok" });  // sin IA configurada se guarda sin revisión
        return mostrar(alta, `<div class="flash error small" style="margin:0">${esc(r.mensaje || "La IA no respondió.")}</div>`);
      }
      if (FAVORABLES.includes(r.veredicto)) return await crear(alta, { revision_id: r.revision_id, decision: "ok" });
      const [cls, txt] = VER[r.veredicto] || ["o", r.veredicto];
      let h = `<div class="row" style="gap:8px"><span class="tag ${cls}">${esc(txt)}</span><span class="small muted">Revisión de GuardIA</span></div>`;
      if ((r.observaciones || []).length) h += `<ul class="obs">${r.observaciones.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>`;
      if (r.sugerencia) h += `<div class="tt-sug"><b>Sugerencia:</b> ${esc(r.sugerencia)}</div>`;
      h += `<div class="row" style="gap:8px;flex-wrap:wrap">`;
      if (r.sugerencia) h += `<button type="button" class="btn sm yellow" data-tt-usar>Usar la sugerencia</button>`;
      if (r.veredicto !== "muy_debil") h += `<input class="tt-in" data-tt-motivo placeholder="Por qué lo guardas así" style="min-width:240px"><button type="button" class="btn sm soft" data-tt-propio>Guardar el mío</button>`;
      else h += `<span class="small" style="color:var(--red)">Muy débil: no se acepta así. Mejóralo o usa la sugerencia.</span>`;
      h += `<button type="button" class="btn sm ghost" data-tt-corregir>Corregir</button></div>`;
      mostrar(alta, h);
      $("[data-tt-usar]", alta)?.addEventListener("click", () => crear(alta, { revision_id: r.revision_id, decision: "sugerencia" }));
      $("[data-tt-propio]", alta)?.addEventListener("click", () => {
        const m = $("[data-tt-motivo]", alta);
        if (!m.value.trim()) { m.focus(); m.classList.add("falta"); return; }
        crear(alta, { revision_id: r.revision_id, decision: "propio", motivo: m.value.trim() });
      });
      $("[data-tt-corregir]", alta).addEventListener("click", () => { mostrar(alta, ""); alta.texto.focus(); });
      alta.texto.addEventListener("input", () => mostrar(alta, ""), { once: true });  // si cambia el texto, hay que revisar de nuevo
    } finally { btn.disabled = false; }
  }
  $$("[data-tt-alta]", raiz).forEach((alta) => alta.texto.addEventListener("focus", () => alta.classList.add("activa")));
})();
