/* Visor de evidencias de compromisos: archivos (imagen y PDF en línea) + texto completo del compromiso + notas. */
(function () {
  const dlg = document.getElementById("rp-visor");
  if (!dlg || dlg.dataset.listo) return;
  dlg.dataset.listo = "1";
  const $ = (id) => document.getElementById(id);
  const lista = $("rp-visor-lista"), ver = $("rp-visor-ver"), notas = $("rp-visor-notas");
  const el = (tag, cls, txt) => { const x = document.createElement(tag); if (cls) x.className = cls; if (txt != null) x.textContent = txt; return x; };

  function mostrar(a) {
    lista.querySelectorAll("li").forEach((li) => li.classList.toggle("on", li.dataset.id == a.id));
    const url = "/evidencias/" + a.id + "?ver=1";
    ver.innerHTML = "";
    if (/^image\//.test(a.tipo)) {
      const i = el("img"); i.src = url; i.alt = a.nombre; i.title = "Abrir en tamaño real";
      i.addEventListener("click", () => window.open(url, "_blank"));
      ver.appendChild(i);
    } else if (a.tipo === "application/pdf") {
      const f = el("iframe"); f.src = url; f.title = a.nombre; ver.appendChild(f);
    } else ver.appendChild(el("p", "rp-visor-nada", "Este tipo de archivo no se puede ver aquí; descárgalo."));
    const bar = el("div", "row"); bar.style.gap = "8px";
    const d = el("a", "btn soft sm", "Descargar " + a.nombre); d.href = "/evidencias/" + a.id; bar.appendChild(d);
    if (/^image\//.test(a.tipo) || a.tipo === "application/pdf") {
      const n = el("a", "btn ghost sm", "Abrir en otra pestaña"); n.href = url; n.target = "_blank"; n.rel = "noopener"; bar.appendChild(n);
    }
    ver.appendChild(bar);
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
  $("rp-visor-x").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
})();
