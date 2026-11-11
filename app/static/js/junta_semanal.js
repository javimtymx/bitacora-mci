/* Junta semanal: búsqueda, filtro por persona y estatus, y grupos plegables (lo filtrado es lo que se imprime). */
(function () {
  const barra = document.querySelector(".jt-barra");
  if (!barra) return;
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const norm = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const q = barra.querySelector("[data-jt-buscar]"), per = barra.querySelector("[data-jt-persona]"), col = barra.querySelector("[data-jt-color]");
  function filtrar() {
    const t = norm(q.value.trim()), p = per.value, c = col.value;
    $$(".jt-area .tt-fila").forEach((f) => {
      const color = f.dataset.color;
      const okc = !c || (c === "r" ? (color === "r" || color === "o") : color === c);
      f.hidden = !((!t || norm(f.dataset.texto).includes(t)) && (!p || f.dataset.persona === p) && okc);
    });
    $$(".jt-grupo").forEach((g) => { const filas = $$(".tt-fila", g); g.hidden = filas.length > 0 && filas.every((f) => f.hidden) && !!(t || p || c); });
  }
  q.addEventListener("input", filtrar); per.addEventListener("change", filtrar); col.addEventListener("change", filtrar);
  function plegar(g, sí) {
    g.classList.toggle("plegado", sí);
    const b = g.querySelector("[data-jt-colapsar]");
    if (b) { b.textContent = sí ? "▸" : "▾"; b.setAttribute("aria-expanded", sí ? "false" : "true"); }
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-jt-colapsar]");
    if (b) { const g = b.closest("[data-grupo]"); plegar(g, !g.classList.contains("plegado")); return; }
    const todos = e.target.closest("[data-jt-plegar]");
    if (todos) {
      const sí = todos.dataset.jtPlegar === "1";
      $$(".jt-grupo").forEach((g) => plegar(g, sí));
      todos.dataset.jtPlegar = sí ? "0" : "1";
      todos.textContent = sí ? "Expandir grupos" : "Contraer grupos";
    }
  });
})();
