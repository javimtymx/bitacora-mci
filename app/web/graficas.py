"""Gráficas SVG generadas en el servidor (sin librerías de JavaScript)."""
from __future__ import annotations

from html import escape

from markupsafe import Markup

from ..services.indicadores import SerieMci, fmt

VERDE, ROJO, GRIS, ROSA, AMBAR = "#2E8B3D", "#D7261E", "#AEB7BF", "#F3A39E", "#F0A020"
TEAL, TEXTO2 = "#053B3F", "#5B6570"


def barras_brecha(serie: SerieMci, n: int = 8, w: int = 300, h: int = 64) -> Markup:
    pts = serie.puntos[-n:]
    vals = [p.brecha for p in pts]
    mx = max([abs(v) for v in vals if v is not None] or [1]) or 1
    bw, mid = w / n, h / 2
    s = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="none" role="img" '
         f'aria-label="Brecha semanal contra el objetivo: arriba gana, abajo pierde">'
         f'<line x1="0" y1="{mid}" x2="{w}" y2="{mid}" stroke="#CBD3DA"/>']
    for i, v in enumerate(vals):
        if v is None:
            s.append(f'<rect x="{i*bw+bw*.3:.1f}" y="{mid-1}" width="{bw*.4:.1f}" height="2" fill="#E6EBEF"/>')
            continue
        bh = max(abs(v) / mx * (mid - 4), 2)
        y = mid - bh if v >= 0 else mid
        c = VERDE if v > 1e-9 else (ROJO if v < -1e-9 else GRIS)
        s.append(f'<rect x="{i*bw+bw*.18:.1f}" y="{y:.1f}" width="{bw*.64:.1f}" height="{bh:.1f}" rx="2" fill="{c}">'
                 f'<title>{pts[i].semana}: {v:+.3g}</title></rect>')
    s.append("</svg>")
    return Markup("".join(s))


def kardex(serie: SerieMci, eventos: list[tuple[str, str]], unidad: str = "", dec: int = 1, periodo: str = "semana",
           etiqueta=None) -> Markup:
    pts = serie.puntos
    nombre_p = "mes" if periodo == "mes" else "semana"
    n = len(pts)
    W, H = 900, 520
    x0, x1 = 120, 860
    X = (lambda i: x0 + i * (x1 - x0) / (n - 1)) if n > 1 else (lambda i: (x0 + x1) / 2)
    vals = [p.valor for p in pts if p.valor is not None] + [p.objetivo for p in pts if p.objetivo is not None]
    lo, hi = (min(vals), max(vals)) if vals else (0, 1)
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad, hi + pad
    rango = hi - lo
    dec = 0 if rango >= 20 else (1 if rango >= 2 else min(dec, 3))
    yt, yb = 48, 190
    Y = lambda v: yb - (v - lo) / (hi - lo) * (yb - yt)
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Histórico {'mensual' if periodo == 'mes' else 'semanal'} del indicador, '
         f'su brecha y los compromisos de cada {nombre_p}">',
         f'<text x="8" y="22" font-size="12.5" font-weight="800" fill="#15191E">1 · Indicador</text>']
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        s.append(f'<line x1="{x0}" x2="{x1}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#EEF1F4"/>'
                 f'<text x="{x0-24}" y="{Y(v)+4:.1f}" text-anchor="end" font-size="11" fill="{TEXTO2}">{fmt(v, dec)}{escape(unidad)}</text>')
    sems = [p.semana for p in pts]
    for j, (sem, _t) in enumerate(eventos[:9]):
        if sem in sems:
            i = sems.index(sem)
            s.append(f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{yt-6}" y2="{H-34}" stroke="#2BA5A3" stroke-dasharray="3 4"/>'
                     f'<circle cx="{X(i):.1f}" cy="{yt-15}" r="9" fill="{TEAL}"/>'
                     f'<text x="{X(i):.1f}" y="{yt-11}" text-anchor="middle" font-size="11" font-weight="800" fill="#fff">{j+1}</text>')
    def linea(attr, color, extra):
        segs, cur = [], []
        for i, p in enumerate(pts):
            v = getattr(p, attr)
            if v is None:
                if cur:
                    segs.append(cur)
                cur = []
            else:
                cur.append(f"{X(i):.1f},{Y(v):.1f}")
        if cur:
            segs.append(cur)
        return "".join(f'<polyline points="{" ".join(sg)}" fill="none" stroke="{color}" {extra}/>' for sg in segs)
    s.append(linea("objetivo", "#8A949E", 'stroke-width="2" stroke-dasharray="6 5"'))
    s.append(linea("valor", TEAL, 'stroke-width="3"'))
    for i, p in enumerate(pts):
        if p.valor is not None:
            s.append(f'<circle cx="{X(i):.1f}" cy="{Y(p.valor):.1f}" r="3.5" fill="{TEAL}"><title>{p.semana}: '
                     f'{fmt(p.valor, dec)}{escape(unidad)}</title></circle>')
    # brecha
    gc = 250
    gaps = [p.brecha for p in pts]
    mx = max([abs(g) for g in gaps if g is not None] or [1]) or 1
    s.append(f'<text x="8" y="{gc-38}" font-size="12.5" font-weight="800" fill="#15191E">2 · Brecha vs objetivo</text>'
             f'<text x="{x0-24}" y="{gc-8}" text-anchor="end" font-size="11" fill="{TEXTO2}">gana ↑</text>'
             f'<text x="{x0-24}" y="{gc+18}" text-anchor="end" font-size="11" fill="{TEXTO2}">pierde ↓</text>'
             f'<line x1="{x0}" x2="{x1}" y1="{gc}" y2="{gc}" stroke="#CBD3DA"/>')
    bw = min(22, (x1 - x0) / max(n, 1) * 0.6)
    for i, g in enumerate(gaps):
        if g is None:
            continue
        bh = max(abs(g) / mx * 26, 2)
        y = gc - bh if g >= 0 else gc
        c = VERDE if g > 1e-9 else (ROJO if g < -1e-9 else GRIS)
        s.append(f'<rect x="{X(i)-bw/2:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" fill="{c}"/>')
    # compromisos
    cb = 400
    mxc = max([max(p.cumplidos, p.remediados + p.en_remediacion + p.solo_texto) for p in pts] or [1]) or 1
    unit = min(16, 70 / mxc)
    s.append(f'<text x="8" y="{cb-96}" font-size="12.5" font-weight="800" fill="#15191E">3 · Compromisos del {nombre_p}</text>'
             f'<text x="{x0-24}" y="{cb-8}" text-anchor="end" font-size="11" fill="{TEXTO2}">cumplidos ↑</text>'
             f'<text x="{x0-24}" y="{cb+18}" text-anchor="end" font-size="11" fill="{TEXTO2}">no cumplidos ↓</text>'
             f'<line x1="{x0}" x2="{x1}" y1="{cb}" y2="{cb}" stroke="#CBD3DA"/>')
    for i, p in enumerate(pts):
        if p.cumplidos:
            s.append(f'<rect x="{X(i)-bw/2:.1f}" y="{cb-p.cumplidos*unit:.1f}" width="{bw:.1f}" height="{p.cumplidos*unit:.1f}" rx="3" fill="{VERDE}"/>')
        y = cb + 1
        for cnt, col in ((p.remediados, ROSA), (p.en_remediacion, AMBAR), (p.solo_texto, ROJO)):
            if cnt:
                s.append(f'<rect x="{X(i)-bw/2:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{cnt*unit:.1f}" rx="3" fill="{col}"/>')
                y += cnt * unit
        pc = p.pct_cumplimiento
        if pc is not None:
            col = "#B3261E" if pc < 70 else ("#7A4600" if pc < 85 else "#1D6B2A")
            s.append(f'<text x="{X(i):.1f}" y="{cb-p.cumplidos*unit-6:.1f}" text-anchor="middle" font-size="10.5" '
                     f'font-weight="700" fill="{col}">{pc:.0f}%</text>')
        actual = i == n - 1
        s.append(f'<text x="{X(i):.1f}" y="{H-12}" text-anchor="middle" font-size="11.5" fill="{"#15191E" if actual else TEXTO2}" '
                 f'font-weight="{800 if actual else 400}">{escape(etiqueta(p.semana)) if etiqueta else "S" + p.semana[-2:]}</text>')
    s.append("</svg>")
    return Markup("".join(s))


def mapa_iniciativas(inis) -> Markup:
    """Impacto contra esfuerzo; el tamaño del círculo es la evidencia."""
    W, H = 560, 420
    x0, x1, y0, y1 = 70, 530, 30, 370
    X = lambda e: x0 + (e - 0.5) / 5 * (x1 - x0)
    Y = lambda i: y1 - (i - 0.5) / 5 * (y1 - y0)
    col = {"Ganancia rápida": VERDE, "Proyecto": "#6A4BC4", "Relleno": "#8A949E", "Reconsiderar": "#C26A00"}
    mx, my = X(2.5), Y(3.5)
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Mapa de impacto contra esfuerzo de las iniciativas">',
         f'<rect x="{x0}" y="{y0}" width="{mx-x0}" height="{my-y0}" fill="#DDF7DC" opacity=".5"/>',
         f'<line x1="{mx}" x2="{mx}" y1="{y0}" y2="{y1}" stroke="#CBD3DA" stroke-dasharray="4 4"/>',
         f'<line x1="{x0}" x2="{x1}" y1="{my}" y2="{my}" stroke="#CBD3DA" stroke-dasharray="4 4"/>',
         f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" fill="none" stroke="#CBD3DA"/>']
    for t, x, y, a in (("Ganancia rápida", x0 + 10, y0 + 20, "start"), ("Proyecto", x1 - 10, y0 + 20, "end"),
                       ("Relleno", x0 + 10, y1 - 10, "start"), ("Reconsiderar", x1 - 10, y1 - 10, "end")):
        s.append(f'<text x="{x}" y="{y}" text-anchor="{a}" font-size="12" font-weight="800" fill="{col[t]}">{t}</text>')
    for v in range(1, 6):
        s.append(f'<text x="{X(v)}" y="{y1+20}" text-anchor="middle" font-size="11.5" fill="{TEXTO2}">{v}</text>'
                 f'<text x="{x0-12}" y="{Y(v)+4}" text-anchor="end" font-size="11.5" fill="{TEXTO2}">{v}</text>')
    s.append(f'<text x="{(x0+x1)/2}" y="{H-6}" text-anchor="middle" font-size="12" font-weight="700" fill="#3F4A54">Esfuerzo →</text>'
             f'<text x="18" y="{(y0+y1)/2}" text-anchor="middle" font-size="12" font-weight="700" fill="#3F4A54" '
             f'transform="rotate(-90 18 {(y0+y1)/2})">Impacto →</text>')
    vistos: dict[tuple[int, int], int] = {}
    for ini in inis:
        k = (ini.esfuerzo, ini.impacto)
        j = vistos.get(k, 0)
        vistos[k] = j + 1
        dx, dy = ((0, 0), (16, -12), (-16, 12), (16, 12), (-16, -12))[j % 5]
        ev = sum(len(ini.fuentes.get(x, [])) for x in ("ideas", "hallazgos", "objeciones"))
        r = min(10 + ev * 0.9, 34)
        c = col.get(ini.cuadrante, "#8A949E")
        cx, cy = X(ini.esfuerzo) + dx, Y(ini.impacto) + dy
        s.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{c}" fill-opacity=".18" stroke="{c}" stroke-width="2">'
                 f'<title>{escape(ini.titulo)}</title></circle>'
                 f'<text x="{cx:.1f}" y="{cy+5:.1f}" text-anchor="middle" font-size="14" font-weight="800" fill="#15191E">{ini.orden}</text>')
    s.append("</svg>")
    return Markup("".join(s))
