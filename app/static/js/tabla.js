/* Tablas con filtro por columna y orden al hacer clic en el encabezado.
   Uso: <table data-tabla> y en cada <th>:
     data-f="texto"  → caja de texto (contiene)
     data-f="lista"  → combo con los valores de la columna
     data-o="num" | "fecha" | "texto" (por defecto texto) → tipo de orden; sin data-o="no" la columna se ordena.
   En <td>, data-v="..." da el valor para ordenar/filtrar (p. ej. fecha ISO) si el texto visible no sirve. */
(function () {
  const norm = (s) => (s || "").toString().normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
  const valor = (td) => (td ? (td.dataset.v !== undefined ? td.dataset.v : td.innerText) : "");
  const visible = (td) => (td ? (td.dataset.l !== undefined ? td.dataset.l : td.innerText.split("\n")[0]) : "").trim();

  function preparar(tabla) {
    const ths = Array.from(tabla.tHead.rows[0].cells);
    const cuerpo = tabla.tBodies[0];
    const filas = Array.from(cuerpo.rows).filter((r) => r.cells.length === ths.length);
    if (!filas.length) return;
    const vacias = Array.from(cuerpo.rows).filter((r) => !filas.includes(r));
    const llave = "tabla:" + location.pathname + ":" + (tabla.id || "");
    let estado = {};
    try { estado = JSON.parse(localStorage.getItem(llave) || "{}"); } catch (e) { estado = {}; }
    estado.f = estado.f || {};

    // fila de filtros
    const fr = document.createElement("tr");
    fr.className = "tabla-filtros";
    const ctrls = ths.map((th, i) => {
      const c = document.createElement("th");
      fr.appendChild(c);
      const tipo = th.dataset.f;
      if (!tipo) return null;
      let el;
      if (tipo === "lista") {
        el = document.createElement("select");
        el.innerHTML = '<option value="">Todos</option>';
        const vals = Array.from(new Set(filas.map((r) => visible(r.cells[i])).filter(Boolean))).sort((a, b) => a.localeCompare(b, "es"));
        vals.forEach((v) => { const o = document.createElement("option"); o.value = v; o.textContent = v; el.appendChild(o); });
      } else {
        el = document.createElement("input");
        el.type = "search"; el.placeholder = "Filtrar…";
      }
      el.className = "input tabla-f";
      el.setAttribute("aria-label", "Filtrar " + th.innerText.trim());
      el.dataset.i = i;
      if (estado.f[i]) el.value = estado.f[i];
      el.addEventListener(tipo === "lista" ? "change" : "input", aplicar);
      c.appendChild(el);
      return el;
    });
    tabla.tHead.appendChild(fr);

    // contador y limpiar
    const pie = document.createElement("div");
    pie.className = "tabla-pie muted small";
    (tabla.closest(".tbl") || tabla).after(pie);

    function aplicar() {
      estado.f = {};
      ctrls.forEach((el) => { if (el && el.value) estado.f[el.dataset.i] = el.value; });
      let n = 0;
      filas.forEach((r) => {
        const ok = ctrls.every((el, i) => {
          if (!el || !el.value) return true;
          return el.tagName === "SELECT" ? visible(r.cells[i]) === el.value : norm(valor(r.cells[i]) + " " + r.cells[i].innerText).includes(norm(el.value));
        });
        r.hidden = !ok;
        if (ok) n++;
      });
      vacias.forEach((r) => { r.hidden = filas.length > 0; });
      const activos = Object.keys(estado.f).length;
      pie.innerHTML = `${n} de ${filas.length}` + (activos ? ' · <button type="button" class="btn sm ghost tabla-limpiar">Quitar filtros</button>' : "");
      guardar();
    }

    function ordenar(i, dir) {
      const tipo = ths[i].dataset.o || "texto";
      const k = (r) => {
        const v = valor(r.cells[i]).trim();
        if (tipo === "num") { const x = parseFloat(v.replace(/[^0-9.-]/g, "")); return isNaN(x) ? -Infinity : x; }
        return tipo === "fecha" ? v : norm(v);
      };
      filas.sort((a, b) => {
        const x = k(a), y = k(b);
        if (x === y) return 0;
        if (x === "" || x === -Infinity) return 1;
        if (y === "" || y === -Infinity) return -1;
        return (x < y ? -1 : 1) * dir;
      });
      filas.forEach((r) => cuerpo.appendChild(r));
      ths.forEach((th, j) => th.setAttribute("aria-sort", j === i ? (dir > 0 ? "ascending" : "descending") : "none"));
      estado.o = [i, dir];
      guardar();
    }

    function guardar() { try { localStorage.setItem(llave, JSON.stringify(estado)); } catch (e) { /* sin almacenamiento */ } }

    ths.forEach((th, i) => {
      if (th.dataset.o === "no") return;
      th.classList.add("ordenable");
      th.tabIndex = 0;
      th.setAttribute("aria-sort", "none");
      const clic = () => ordenar(i, estado.o && estado.o[0] === i ? -estado.o[1] : 1);
      th.addEventListener("click", clic);
      th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); clic(); } });
    });
    pie.addEventListener("click", (e) => {
      if (!e.target.closest(".tabla-limpiar")) return;
      ctrls.forEach((el) => { if (el) el.value = ""; });
      aplicar();
    });
    if (estado.o && ths[estado.o[0]]) ordenar(estado.o[0], estado.o[1]);
    aplicar();
  }

  document.querySelectorAll("table[data-tabla]").forEach(preparar);
})();
