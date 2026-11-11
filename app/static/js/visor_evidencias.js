/* Visor de evidencias de compromisos: archivos (imagen y PDF en línea) + texto completo del compromiso + notas. */
(function () {
  const dlg = document.getElementById("rp-visor");
  if (!dlg || dlg.dataset.listo) return;
  dlg.dataset.listo = "1";
  const $ = (id) => document.getElementById(id);
  const lista = $("rp-visor-lista"), ver = $("rp-visor-ver"), notas = $("rp-visor-notas");
  const el = (tag, cls, txt) => { const x = document.createElement(tag); if (cls) x.className = cls; if (txt != null) x.textContent = txt; return x; };

  function mostrar(a) {
    mostrar.turno = mostrar.turno || 0;
    lista.querySelectorAll("li").forEach((li) => li.classList.toggle("on", li.dataset.id == a.id));
    const url = "/evidencias/" + a.id + "?ver=1";
    ver.innerHTML = "";
    if (/^image\//.test(a.tipo)) {
      const i = el("img"); i.src = url; i.alt = a.nombre; i.title = "Abrir en tamaño real";
      i.addEventListener("click", () => window.open(url, "_blank"));
      ver.appendChild(i);
    } else if (a.tipo === "application/pdf") {
      // Las páginas del PDF se dibujan aquí mismo (PDF.js); si no se puede, se usa el visor del navegador con una copia
      // local (blob:), porque el servidor no permite incrustar sus propias páginas en marcos.
      const caja = el("div", "rp-visor-pdf"); ver.appendChild(caja);
      const espera = el("p", "rp-visor-nada", "Cargando el PDF…"); caja.appendChild(espera);
      const turno = ++mostrar.turno;
      fetch(url, { credentials: "same-origin" })
        .then((r) => { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); })
        .then((buf) => (turno !== mostrar.turno ? null : pintarPdf(buf, caja, turno).catch(() => {
          caja.innerHTML = "";
          if (mostrar.blob) URL.revokeObjectURL(mostrar.blob);
          mostrar.blob = URL.createObjectURL(new Blob([buf], { type: "application/pdf" }));
          const f = el("iframe"); f.title = a.nombre; f.src = mostrar.blob; caja.appendChild(f);
        })))
        .catch(() => { espera.textContent = "No se pudo mostrar el PDF aquí; usa «Abrir en otra pestaña» o descárgalo."; });
    } else ver.appendChild(el("p", "rp-visor-nada", "Este tipo de archivo no se puede ver aquí; descárgalo."));
    const bar = el("div", "row"); bar.style.gap = "8px";
    const d = el("a", "btn soft sm", "Descargar " + a.nombre); d.href = "/evidencias/" + a.id; bar.appendChild(d);
    if (/^image\//.test(a.tipo) || a.tipo === "application/pdf") {
      const n = el("a", "btn ghost sm", "Abrir en otra pestaña"); n.href = url; n.target = "_blank"; n.rel = "noopener"; bar.appendChild(n);
    }
    ver.appendChild(bar);
  }

  const PDFJS = "/static/vendor/pdfjs/";  // PDF.js 3.11.174 (Apache-2.0), servido por la propia app
  function cargarPdfJs() {
    if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
    if (!cargarPdfJs.p) cargarPdfJs.p = new Promise((ok, mal) => {
      const sc = document.createElement("script"); sc.src = PDFJS + "pdf.min.js";
      sc.onload = () => { window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS + "pdf.worker.min.js"; ok(window.pdfjsLib); };
      sc.onerror = () => { cargarPdfJs.p = null; mal(new Error("pdf.js")); };
      document.head.appendChild(sc);
    });
    return cargarPdfJs.p;
  }
  async function pintarPdf(buf, caja, turno) {
    const lib = await cargarPdfJs();
    const doc = await lib.getDocument({ data: new Uint8Array(buf) }).promise;
    if (turno !== mostrar.turno) return;
    caja.innerHTML = "";
    const ancho = Math.max(320, caja.clientWidth - 24);
    const total = Math.min(doc.numPages, 60);
    for (let n = 1; n <= total; n++) {
      if (turno !== mostrar.turno) return;
      const pag = await doc.getPage(n);
      const base = pag.getViewport({ scale: 1 });
      const escala = ancho / base.width, dpr = window.devicePixelRatio || 1;
      const vp = pag.getViewport({ scale: escala * dpr });
      const cv = document.createElement("canvas");
      cv.width = vp.width; cv.height = vp.height; cv.style.width = ancho + "px"; cv.style.height = (vp.height / dpr) + "px";
      caja.appendChild(cv);
      await pag.render({ canvasContext: cv.getContext("2d"), viewport: vp }).promise;
    }
    if (doc.numPages > total) caja.appendChild(el("p", "rp-visor-nada", `Se muestran ${total} de ${doc.numPages} páginas; abre el PDF para ver el resto.`));
  }

  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-visor]");
    if (!b) return;
    e.preventDefault();
    const info = JSON.parse(b.dataset.evid), arch = JSON.parse(b.dataset.arch || "[]");
    $("rp-visor-t").textContent = info.folio + " · " + arch.length + " evidencia" + (arch.length === 1 ? "" : "s");
    $("rp-visor-meta").textContent = [info.responsable, info.limite ? "límite " + info.limite : ""].filter(Boolean).join(" · ");
    $("rp-visor-sub").textContent = info.texto;  // texto completo del compromiso
    $("rp-visor-abrir").href = info.url || "#";
    lista.innerHTML = "";
    arch.forEach((a) => {
      const li = el("li"); li.dataset.id = a.id;
      li.appendChild(el("b", null, a.nombre));
      li.appendChild(el("span", null, a.fecha + " · " + a.autor + " · " + a.tam));
      li.addEventListener("click", () => mostrar(a));
      lista.appendChild(li);
    });
    notas.innerHTML = "";
    (info.notas || []).slice().reverse().forEach((n) => {
      const li = el("li");
      li.appendChild(el("span", "rp-visor-nmeta", n.fecha + " · " + n.autor + (n.avance != null ? " · avance " + n.avance + "%" : "")));
      li.appendChild(el("div", "rp-visor-ntxt", n.texto));
      notas.appendChild(li);
    });
    $("rp-visor-lbl-notas").hidden = !(info.notas || []).length;
    if (arch.length) mostrar(arch[0]);
    else { ver.innerHTML = ""; ver.appendChild(el("p", "rp-visor-nada", "Este compromiso no tiene evidencias cargadas.")); }
    dlg.showModal();
  });
  // ---------------------------------------------------------- diagnóstico de un cierre con sospecha
  const sosp = $("rp-sosp");
  let sospBtn = null;
  const lista_ = (ul, items) => { ul.innerHTML = ""; (items || []).forEach((t) => ul.appendChild(el("li", null, t))); return (items || []).length; };
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-sospecha]");
    if (!b || !sosp) return;
    e.preventDefault();
    e.stopPropagation();
    sospBtn = b;
    const d = JSON.parse(b.dataset.info);
    $("rp-sosp-t").textContent = d.folio;
    $("rp-sosp-meta").textContent = [d.responsable, d.cerrado ? "cerrado " + d.cerrado : "",
      d.confianza ? "confianza " + d.confianza : ""].filter(Boolean).join(" · ");
    $("rp-sosp-texto").textContent = d.texto;
    $("rp-sosp-titulo").textContent = d.titulo;
    $("rp-sosp-motivo").textContent = d.motivo || "GuardIA no dejó explicación.";
    $("rp-sosp-falta-box").hidden = !lista_($("rp-sosp-falta"), d.falta);
    $("rp-sosp-cumple-box").hidden = !lista_($("rp-sosp-cumple"), d.cumple);
    $("rp-sosp-com").textContent = d.comentario || "";
    $("rp-sosp-com-box").hidden = !d.comentario;
    $("rp-sosp-abrir").href = d.url;
    const n = JSON.parse(b.dataset.arch || "[]").length;
    sosp.querySelector("[data-sosp-evid] span").textContent = n ? `Ver evidencias (${n})` : "Sin evidencias: ver notas";
    sosp.showModal();
  }, true);
  if (sosp) {
    sosp.querySelector("[data-sosp-x]").addEventListener("click", () => sosp.close());
    sosp.addEventListener("click", (e) => { if (e.target === sosp) sosp.close(); });
    sosp.querySelector("[data-sosp-evid]").addEventListener("click", () => {
      sosp.close();
      const t = document.createElement("button");  // abre el visor con los mismos datos del compromiso
      t.hidden = true; t.dataset.visor = ""; t.dataset.evid = sospBtn.dataset.evid; t.dataset.arch = sospBtn.dataset.arch;
      document.body.appendChild(t); t.click(); t.remove();
    });
  }

  $("rp-visor-x").addEventListener("click", () => dlg.close());
  dlg.addEventListener("close", () => { if (mostrar.blob) { URL.revokeObjectURL(mostrar.blob); mostrar.blob = null; } ver.innerHTML = ""; });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
})();
