/* Archivos grandes: suben directo del navegador a S3 con progreso; el formulario solo manda un «token» por archivo.
   - Formularios con data-subidas="<destino>" (ticket, evidencia, pago, transcripcion): se intercepta el envío.
   - window.Subidas.subir(file, destino, onProgreso) → Promise<token> para pantallas con su propio JS.
   Si el servidor no tiene S3 (409), el formulario se envía de la forma tradicional. */
(function () {
  "use strict";
  const GRANDE = 50 * 1024 * 1024;
  let activas = 0;

  function mb(n) { return n >= 1024 ** 3 ? (n / 1024 ** 3).toFixed(1) + " GB" : (n / 1024 ** 2).toFixed(n > 10 * 1024 ** 2 ? 0 : 1) + " MB"; }

  window.addEventListener("beforeunload", (e) => {
    if (activas > 0) { e.preventDefault(); e.returnValue = "Hay archivos subiendo."; }
  });

  function panel() {
    let p = document.getElementById("subidas-panel");
    if (!p) {
      p = document.createElement("div");
      p.id = "subidas-panel";
      p.className = "subidas-panel";
      p.setAttribute("role", "status");
      p.setAttribute("aria-live", "polite");
      document.body.appendChild(p);
    }
    return p;
  }

  function fila(file) {
    const d = document.createElement("div");
    d.className = "subida";
    d.innerHTML = `<div class="row between" style="gap:8px"><b></b><span class="small muted pct">0%</span></div>
      <div class="barra"><span style="width:0%"></span></div><span class="small aviso"></span>`;
    d.querySelector("b").textContent = file.name + " · " + mb(file.size);
    if (file.size > GRANDE) d.querySelector(".aviso").textContent = "Archivo grande: puede tardar varios minutos. No cierres esta página.";
    panel().appendChild(d);
    return d;
  }

  async function firmar(file, destino) {
    const r = await fetch("/api/subidas/firmar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destino, nombre: file.name, tamano: file.size }),
    });
    if (r.status === 409) return null; // sin S3: envío tradicional
    const d = await r.json().catch(() => ({ ok: false, mensaje: "No se pudo preparar la subida." }));
    if (!d.ok) throw new Error(d.mensaje || "No se pudo preparar la subida.");
    return d;
  }

  function put(file, f, onProgreso) {
    return new Promise((ok, mal) => {
      const x = new XMLHttpRequest();
      x.open("PUT", f.url);
      Object.entries(f.headers || {}).forEach(([k, v]) => x.setRequestHeader(k, v));
      x.upload.onprogress = (e) => { if (e.lengthComputable && onProgreso) onProgreso(e.loaded / e.total); };
      x.onload = () => (x.status >= 200 && x.status < 300 ? ok() : mal(new Error(`S3 respondió ${x.status} al subir «${file.name}».`)));
      x.onerror = () => { const er = new Error(`Se cortó la conexión al subir «${file.name}». Intenta de nuevo.`); er.red = true; mal(er); };
      x.send(file);
    });
  }

  let sinDirecto = false; // S3 rechazó la subida directa (p. ej. falta CORS): los chicos van por el servidor
  const RESPALDO = 50 * 1024 * 1024;

  async function subir(file, destino, onProgreso) {
    if (sinDirecto && file.size <= RESPALDO) return null;
    const f = await firmar(file, destino);
    if (!f) return null;
    const el = fila(file);
    activas++;
    try {
      await put(file, f, (p) => {
        const v = Math.round(p * 100);
        el.querySelector(".pct").textContent = v + "%";
        el.querySelector(".barra span").style.width = v + "%";
        if (onProgreso) onProgreso(p);
      });
      el.querySelector(".pct").textContent = "✓";
      return f.token;
    } catch (e) {
      if (e.red && file.size <= RESPALDO) { // se manda de la forma tradicional
        sinDirecto = true;
        el.remove();
        return null;
      }
      el.classList.add("error");
      el.querySelector(".aviso").textContent = e.message;
      throw e;
    } finally { activas--; }
  }

  window.Subidas = { subir, GRANDE, mb };

  // ---------------------------------------------------------------- formularios con data-subidas
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    const destino = form.dataset && form.dataset.subidas;
    if (!destino || form.dataset.subidasListo) return;
    const inputs = Array.from(form.querySelectorAll('input[type="file"]')).filter((i) => i.files && i.files.length);
    if (!inputs.length) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const botones = form.querySelectorAll("button, input[type=submit]");
    botones.forEach((b) => { b.disabled = true; });
    const tokens = [];
    try {
      for (const inp of inputs) {
        for (const file of Array.from(inp.files)) {
          const t = await subir(file, destino);
          if (t === null) { // sin S3: se manda como siempre
            form.dataset.subidasListo = "1";
            botones.forEach((b) => { b.disabled = false; });
            form.requestSubmit(e.submitter || undefined);
            return;
          }
          tokens.push(t);
        }
      }
    } catch (err) {
      botones.forEach((b) => { b.disabled = false; });
      alertar(form, err.message);
      return;
    }
    tokens.forEach((t) => {
      const h = document.createElement("input");
      h.type = "hidden"; h.name = "subidas"; h.value = t; form.appendChild(h);
    });
    inputs.forEach((i) => { i.disabled = true; i.required = false; });
    if (e.submitter && e.submitter.name) {
      const h = document.createElement("input");
      h.type = "hidden"; h.name = e.submitter.name; h.value = e.submitter.value; form.appendChild(h);
    }
    form.dataset.subidasListo = "1";
    HTMLFormElement.prototype.submit.call(form);
  }, true);

  function alertar(form, texto) {
    let d = form.querySelector(".subida-error");
    if (!d) { d = document.createElement("div"); d.className = "flash error subida-error"; form.prepend(d); }
    d.textContent = texto;
  }

  // aviso inmediato al elegir un archivo grande
  document.addEventListener("change", (e) => {
    const i = e.target;
    if (!(i instanceof HTMLInputElement) || i.type !== "file" || !i.files) return;
    const grandes = Array.from(i.files).filter((f) => f.size > GRANDE);
    let n = i.parentElement && i.parentElement.querySelector(".aviso-grande");
    if (!grandes.length) { if (n) n.remove(); return; }
    if (!n) { n = document.createElement("span"); n.className = "small aviso-grande"; i.insertAdjacentElement("afterend", n); }
    n.textContent = `Archivo grande (${grandes.map((f) => mb(f.size)).join(", ")}): puede tardar en subir. No cierres la página mientras sube.`;
  });
})();
