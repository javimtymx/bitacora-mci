"""Tablero (corte mensual), kárdex por indicador, captura de indicadores, diagnóstico IA por área y prompts por área."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Area,
    Compromiso,
    DiagnosticoSemanal,
    Mci,
    Medicion,
    PromptArea,
    RecomendacionIA,
    Usuario,
    utcnow,
)
from ..services import catalogo, diagnostico, ia, indicadores, parametros, seguimiento, ventas_auto
from ..services.util import auditar, hoy, semana_iso
from . import graficas
from .base import flash, ir, render, requiere_direccion, requiere_lider, usuario_actual

router = APIRouter()


def _areas(db: Session) -> list[Area]:
    return [a for a in db.scalars(select(Area).where(Area.activo.is_(True)).order_by(Area.id))
            if indicadores.mcis_de(db, a.id)]


HISTORICO_VISTA = 4  # semanas anteriores que se muestran junto a la actual


def _semanas_vista(db: Session, area: Area, preds, sem: str) -> list[str]:
    """Columnas del seguimiento: la semana evaluada y hasta 4 anteriores CON movimiento (compromisos o capturas),
    aunque sean de otro mes o previas al arranque. Primero las que aún tienen compromisos abiertos."""
    from ..models import Medicion
    con_datos = {x for x in db.scalars(select(Compromiso.semana).where(Compromiso.area_id == area.id,
                                                                       Compromiso.estado != "cancelado",
                                                                       Compromiso.semana < sem).distinct())}
    ver_ids = [v.id for p in preds for v in p.versiones]
    if ver_ids:
        con_datos |= {x for x in db.scalars(select(Medicion.periodo).where(Medicion.tipo == "predictiva",
                                                                           Medicion.version_id.in_(ver_ids),
                                                                           Medicion.periodo < sem).distinct())
                      if "-W" in x}
    pendientes = {x for x in db.scalars(select(Compromiso.semana).where(Compromiso.area_id == area.id,
                                                                        Compromiso.estado == "abierto",
                                                                        Compromiso.semana < sem).distinct())}
    elegidas = sorted(pendientes, reverse=True)[:HISTORICO_VISTA]
    for x in sorted(con_datos - set(elegidas), reverse=True):
        if len(elegidas) >= HISTORICO_VISTA:
            break
        elegidas.append(x)
    return sorted(elegidas) + [sem]


def _consolidado_area(db: Session, area: Area, sem: str, sems8: list[str]) -> dict:
    """Todo lo del área en una página: medidas predictivas (las palancas), compromisos, voz del cliente, ideas y diagnóstico."""
    from datetime import timedelta as _td
    from ..models import Hallazgo, Idea, Objecion, Predictiva, SesionCliente
    preds = list(db.scalars(select(Predictiva).join(Mci).where(Mci.area_id == area.id, Mci.activo.is_(True),
                                                             Predictiva.activo.is_(True)).order_by(Mci.id, Predictiva.id)))
    sems8 = _semanas_vista(db, area, preds, sem)
    comps = list(db.scalars(select(Compromiso).where(Compromiso.area_id == area.id, Compromiso.semana.in_(sems8))))
    matriz = indicadores.matriz_predictivas(db, preds, sems8, comps)
    abiertos = sorted(db.scalars(select(Compromiso).where(Compromiso.area_id == area.id, Compromiso.estado == "abierto")),
                      key=lambda c: (c.fecha_limite, c.id))
    # jerarquía: cada MCI (meta) con su resultado semanal y, debajo, las predictivas que la mueven
    grupos = []
    for m in indicadores.mcis_de(db, area.id):
        filas = [f for f in matriz if f[0].mci_id == m.id]
        sr = indicadores.serie_mci(db, m, len(sems8), sem)
        grupos.append({"mci": m, "v": sr.version, "mes": sr.actual, "filas": filas, "dec": indicadores.decimales(m),
                       "racha": sr.racha})
    comps_sem = sorted((c for c in comps if c.semana == sem), key=lambda c: c.fecha_limite)
    desde = hoy() - _td(weeks=6)
    hall = list(db.scalars(select(Hallazgo).join(SesionCliente).where(SesionCliente.area_id == area.id,
                                                                       SesionCliente.fecha >= desde)))
    objs = list(db.scalars(select(Objecion).join(SesionCliente).where(SesionCliente.area_id == area.id,
                                                                       SesionCliente.fecha >= desde)))
    temas: dict[tuple[str, str], int] = {}
    for h in hall:
        temas[(h.cuadrante, h.tema)] = temas.get((h.cuadrante, h.tema), 0) + 1
    tipos: dict[str, list[int]] = {}
    for o in objs:
        t = tipos.setdefault(o.tipo, [0, 0])
        t[0] += 1
        t[1] += 1 if o.avanzo else 0
    n_ses = len({h.sesion_id for h in hall} | {o.sesion_id for o in objs})
    ideas = list(db.scalars(select(Idea).where(Idea.area_id == area.id, Idea.creado >= utcnow() - _td(days=90))
                            .order_by(Idea.actualizado.desc())))
    d = diagnostico.ultimo(db, area.id)
    return {"tablero": _tablero_tareas(db, area, preds, comps, abiertos), "ayf": _panel_ayf(db, area), "matriz": matriz, "grupos": grupos, "comps_sem": comps_sem, "sems8": sems8,
            "abiertos": abiertos, "pendientes_sem": {c.semana for c in abiertos},
            "temas": sorted(temas.items(), key=lambda x: -x[1])[:5], "tipos": sorted(tipos.items(), key=lambda x: -x[1][0]),
            "n_ses": n_ses, "ideas": ideas[:5], "ideas_estado": {k: sum(1 for i in ideas if i.estado == k)
                                                                   for k in ("propuesta", "validada", "en_curso", "implementada")},
            "diag": d}


COLORES_GRUPO = ("#579BFC", "#A25DDC", "#00C875", "#FDAB3D", "#E2445C", "#0086C0", "#9D50DD", "#037F4C", "#FF642E",
                 "#66CCFF")


def _resumen_grupo(cs, estados) -> dict:
    from ..services import reporte_semanal as rs
    n = len(cs)
    colores = {}
    for c in cs:
        k = estados[c.id].color
        colores[k] = colores.get(k, 0) + 1
    fechas = [c.fecha_limite for c in cs]
    subt = [x for c in cs for x in c.subtareas]
    return {"n": n, "colores": colores, "avance": round(sum(100 if c.estado == "cumplido" else c.avance for c in cs) / n) if n else None,
            "desde": min(fechas) if fechas else None, "hasta": max(fechas) if fechas else None,
            "vencidos": sum(1 for c in cs if rs.dias_vencido(c)), "evidencias": sum(len(c.evidencias) for c in cs),
            "subt": (sum(1 for x in subt if x.hecha), len(subt))}


def _tablero_tareas(db: Session, area: Area, preds, comps, abiertos) -> list[dict]:
    """Vista de tablero (tipo lista de tareas): MCI › grupo por medida predictiva › compromisos › subtareas.
    Entran los abiertos de cualquier semana y lo cerrado de las semanas visibles."""
    from ..services import reporte_semanal as rs
    excs = seguimiento.excepciones_aprobadas(db)
    todos = {c.id: c for c in list(abiertos) + [c for c in comps if c.estado != "cancelado"]}
    estados = {cid: rs.estado(c, excs) for cid, c in todos.items()}
    orden = lambda c: (0 if c.estado == "abierto" else 1, c.fecha_limite, c.id)
    out, i = [], 0
    for m in indicadores.mcis_de(db, area.id):
        grupos = []
        for p in [p for p in preds if p.mci_id == m.id]:
            cs = sorted((c for c in todos.values() if c.predictiva_id == p.id), key=orden)
            grupos.append({"pred": p, "color": COLORES_GRUPO[i % len(COLORES_GRUPO)], "comps": cs,
                           "res": _resumen_grupo(cs, estados)})
            i += 1
        out.append({"mci": m, "grupos": grupos})
    ids_pred = {p.id for p in preds}
    sueltos = sorted((c for c in todos.values() if c.predictiva_id not in ids_pred), key=orden)
    if sueltos:
        out.append({"mci": None, "grupos": [{"pred": None, "color": "#C4C4C4", "comps": sueltos,
                                             "res": _resumen_grupo(sueltos, estados)}]})
    return {"mcis": out, "estados": estados, "rs": rs}


def _panel_ayf(db: Session, area: Area) -> list:
    """Recuadro informativo de Ventas con lo que sale de Panel AYF (no es MCI: no lleva compromisos ni racha)."""
    if area.clave != "ventas":
        return []
    out = []
    datos = None
    for clave in ("ventas-base", "ventas-facturacion"):
        m = db.scalar(select(Mci).where(Mci.clave == clave))
        if m and ventas_auto.es_auto(m):
            datos = datos or ventas_auto.Datos(db)
            r = ventas_auto.resumen(db, m, datos)
            if r:
                out.append(r)
    return out


def _resumen_areas(db: Session, areas: list[Area], sem: str, rachas_mci: dict[int, str]) -> list[dict]:
    sems4 = indicadores.semanas(4, sem)
    excs = seguimiento.excepciones_aprobadas(db)
    out = []
    for a in areas:
        rachas = [rachas_mci[m.id] for m in indicadores.mcis_de(db, a.id) if m.id in rachas_mci]
        comps = list(db.scalars(select(Compromiso).where(Compromiso.area_id == a.id, Compromiso.semana.in_(sems4))))
        cu = seguimiento.cumplimiento(comps, excs)
        out.append({"area": a, "ganando": rachas.count("ganando"), "perdiendo": rachas.count("perdiendo"),
                    "total": len(rachas), "pct": cu.pct,
                    "diag": diagnostico.ultimo(db, a.id, sem)})
    return out


@router.get("/")
def tablero(request: Request, area: str = "todas", mes: str | None = None, db: Session = Depends(get_db),
            u: Usuario = Depends(usuario_actual)):
    mes = indicadores.mes_valido(mes)
    sem = indicadores.semana_de_mes(mes)
    areas = _areas(db)
    sel = next((a for a in areas if a.clave == area), None)
    mcis = indicadores.mcis_de(db, sel.id if sel else None)
    todas = indicadores.mcis_de(db)
    rachas_mci = {m.id: indicadores.serie_mci(db, m, 1, sem).racha[0] for m in todas}
    ganando = sum(1 for m in mcis if rachas_mci.get(m.id) == "ganando")
    perdiendo = sum(1 for m in mcis if rachas_mci.get(m.id) == "perdiendo")
    sem_ant = indicadores.semanas(2, sem)[0]
    comps_ant = list(db.scalars(select(Compromiso).where(Compromiso.semana == sem_ant)))
    if sel:
        comps_ant = [c for c in comps_ant if c.area_id == sel.id]
    cerrados = [c for c in comps_ant if c.estado in ("cumplido", "no_cumplido")]
    personas = [p for p in seguimiento.estado_personas(db, sel.id if sel else None)
                if p.usuario.en_indicador_personas is not False]  # Configurar › Usuarios decide quién aparece
    sems8 = indicadores.semanas_mes(mes) or [sem]  # semanas del mes evaluado (seguimiento semanal)
    tiras = indicadores.semanas_persona(db, [p.usuario.id for p in personas], sems8)
    diags = [d for d in (diagnostico.ultimo(db, a.id, sem) for a in areas) if d]
    sin_rem = sum(1 for c in db.scalars(select(Compromiso).where(Compromiso.estado == "no_cumplido"))
                  if seguimiento.sin_remediacion(c) and (not sel or c.area_id == sel.id))
    cons = _consolidado_area(db, sel, sem, sems8) if sel else None
    resumen_areas = None if sel else _resumen_areas(db, areas, sem, rachas_mci)
    from .captura import _opciones_captura
    opciones = _opciones_captura(db, u) if sel else {}
    if opciones:
        opciones["sem_nc"] = opciones.pop("sem")  # el formulario siempre registra en la semana en curso
        opciones["areas_nc"] = opciones.pop("areas")  # el tablero ya usa "areas" para el filtro
    n_arch = 0
    if sel:
        from .archivo import archivo
        n_arch = len(archivo(db, u, sel.id))
    return render(request, "tablero.html", u, "inicio", areas=areas, sel=sel, sem=sem, mes=mes, n_mci=len(mcis),
                  mes_de=indicadores.mes_de, inicio=indicadores.inicio_seguimiento(),
                  meses=indicadores.meses_disponibles(), nombre_mes=indicadores.nombre_mes,
                  adaptacion=indicadores.semanas_adaptacion(),
                  n_arch=n_arch, seg=seguimiento, **opciones,
                  cons=cons, resumen_areas=resumen_areas,
                  ganando=ganando, perdiendo=perdiendo, cumplidos_ant=sum(1 for c in cerrados if c.estado == "cumplido"),
                  cerrados_ant=len(cerrados), sem_ant=sem_ant, sin_rem=sin_rem, personas=personas, tiras=tiras,
                  sems8=sems8, diags=diags)


@router.get("/kardex")
def kardex_area(area: str, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    a = db.scalar(select(Area).where(Area.clave == area))
    ms = indicadores.mcis_de(db, a.id) if a else []
    if not ms:
        raise HTTPException(404, "Esa área no tiene indicadores.")
    return ir(f"/kardex/{ms[0].id}")


@router.get("/kardex/{mid}")
def kardex(mid: int, request: Request, semana: str | None = None, db: Session = Depends(get_db),
           u: Usuario = Depends(usuario_actual)):
    """Kárdex: el resultado de la MCI por mes y los compromisos de sus semanas."""
    from types import SimpleNamespace
    m = db.get(Mci, mid)
    if not m:
        raise HTTPException(404)
    sem = semana or semana_iso(hoy())
    sr = indicadores.serie_mci(db, m, 26, sem, n_meses=12)
    sems = [p.semana for p in sr.puntos]
    eventos = indicadores.eventos_mci(db, m, sems)
    eventos_mes = [(indicadores.mes_de(s_), t) for s_, t in eventos]
    mensual = sr.por_mes()
    dec = indicadores.decimales(m)
    unidad = sr.version.unidad if sr.version else ""
    ultimos4 = sr.puntos[-4:]
    cum4 = sum(p.cumplidos for p in ultimos4)
    tot4 = sum(p.compromisos for p in ultimos4)
    pred_ids = [p.id for p in m.predictivas]
    comps_sem = list(db.scalars(select(Compromiso).where(Compromiso.predictiva_id.in_(pred_ids), Compromiso.semana == sem)
                                .order_by(Compromiso.fecha_limite))) if pred_ids else []
    preds = []
    for p in m.predictivas:
        if not p.activo:
            continue
        cs = [c for c in comps_sem if c.predictiva_id == p.id]
        hechos = sum(1 for c in cs if c.estado == "cumplido")
        preds.append((p, hechos, len(cs)))
    diags: dict[str, DiagnosticoSemanal] = {}
    for d in db.scalars(select(DiagnosticoSemanal).where(DiagnosticoSemanal.area_id == m.area_id,
                                                         DiagnosticoSemanal.semana.in_(sems))
                        .order_by(DiagnosticoSemanal.semana)):
        diags[indicadores.mes_de(d.semana)] = d  # el último diagnóstico de cada mes
    ev_por_mes: dict[str, list[str]] = {}
    for ms, t in eventos_mes:
        ev_por_mes.setdefault(ms, []).append(t)
    filas = []
    for i in range(len(mensual) - 1, -1, -1):
        p = mensual[i]
        prev = mensual[i - 1] if i else None
        mov = p.valor - prev.valor if (p.valor is not None and prev and prev.valor is not None) else None
        filas.append({"p": p, "mov": mov, "eventos": ev_por_mes.get(p.semana, []), "diag": diags.get(p.semana)})
    ultimo_diag = diagnostico.ultimo(db, m.area_id)
    dec_obj = 1 if (sr.version and sr.version.tipo_meta == "acumulado") else dec
    grafica = graficas.kardex(SimpleNamespace(puntos=mensual), eventos_mes, unidad, dec, periodo="mes",
                              etiqueta=lambda x: indicadores.nombre_mes(x, False)[:3])
    return render(request, "kardex.html", u, "inicio", m=m, sr=sr, sem=sem, dec=dec, dec_obj=dec_obj, unidad=unidad,
                  grafica=grafica, eventos=eventos_mes, filas=filas, preds=preds, nombre_mes=indicadores.nombre_mes,
                  comps_sem=comps_sem, cum4=cum4, tot4=tot4, areas=_areas(db), hermanas=indicadores.mcis_de(db, m.area_id),
                  rem_ok=sum(p.remediados for p in sr.puntos), rem_texto=sum(p.solo_texto for p in sr.puntos),
                  ultimo_diag=ultimo_diag, seg=seguimiento, puede_capturar=_puede_capturar(u, m))


# ---------------------------------------------------------------- captura semanal

def _puede_capturar(u: Usuario, m: Mci) -> bool:
    return u.es_direccion or m.dueno_id == u.id or (u.rol == "lider" and u.area_id == m.area_id)


@router.get("/indicadores/captura")
def captura(request: Request, mes: str | None = None, semana: str | None = None, db: Session = Depends(get_db),
            u: Usuario = Depends(usuario_actual)):
    """MCI: un valor por mes. Medidas predictivas: un valor por semana."""
    mes = indicadores.mes_valido(mes)
    sems_disp = indicadores.semanas(8)
    sem = semana if semana in sems_disp else sems_disp[-1]
    todas = [m for m in indicadores.mcis_de(db) if _puede_capturar(u, m)]
    ms = [m for m in todas if not ventas_auto.es_auto(m)]
    if not todas:
        raise HTTPException(403, "No tienes indicadores de captura manual.")
    filas = []
    for m in ms:
        v = catalogo.version_vigente(m.versiones, indicadores.ultimo_dia(mes)) or catalogo.version_vigente(m.versiones)
        med = db.scalar(select(Medicion).where(Medicion.tipo == "mci", Medicion.version_id.in_([x.id for x in m.versiones]),
                                               Medicion.periodo == mes)) if m.versiones else None
        sr = indicadores.serie_mci(db, m, 1, None, n_meses=4)
        previos = [p for p in sr.meses if p.mes < mes][-3:]
        actual = next((p for p in sr.meses if p.mes == mes), None)
        filas.append({"m": m, "v": v, "med": med, "prev": previos,
                      "obj": actual.objetivo if actual else indicadores.objetivo(v, indicadores.ultimo_dia(mes)),
                      "dec": indicadores.decimales(m)})
    preds = [p for m in todas for p in m.predictivas if p.activo]
    sems4 = indicadores.semanas(4, sem)
    pfilas = [{"p": p, "v": v, "fila": fila} for p, v, fila in indicadores.matriz_predictivas(db, preds, sems4, [])]
    return render(request, "captura_mediciones.html", u, "indicadores", filas=filas, pfilas=pfilas, sem=sem, mes=mes,
                  meses=indicadores.meses_disponibles(), semanas=sems_disp, nombre_mes=indicadores.nombre_mes)


@router.post("/indicadores/captura")
async def guardar_captura(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    form = await request.form()
    mes = indicadores.mes_valido(str(form.get("mes") or ""))
    sem = str(form.get("semana") or semana_iso(hoy()))
    if sem not in indicadores.semanas(8):
        raise HTTPException(400, "Semana inválida.")
    n = _guardar_mci(db, u, form, mes)
    n_mci = n
    n += _guardar_predictivas(db, u, form, sem)
    if n:
        auditar(db, "captura", "mediciones", None, u.id, mes=mes if n_mci else None, semana=sem if n > n_mci else None, n=n)
    db.commit()
    donde = indicadores.nombre_mes(mes).lower() if n_mci else f"la semana {sem[-2:]}"
    flash(request, f"{n} valor(es) guardados para {donde}." if n else "No se capturó ningún valor.")
    return ir(f"/indicadores/captura?mes={mes}&semana={sem}" + ("#predictivas" if n > n_mci else ""))


def _guardar_mci(db: Session, u: Usuario, form, mes: str) -> int:
    """Resultado mensual de las MCI (solo los campos que vienen en el formulario)."""
    n = 0
    dia = indicadores.ultimo_dia(mes)
    for m in indicadores.mcis_de(db):
        raw = str(form.get(f"valor_{m.id}") or "").strip().replace(",", "")
        if not raw or not _puede_capturar(u, m) or ventas_auto.es_auto(m):
            continue
        try:
            valor = float(raw.rstrip("%"))
        except ValueError:
            continue
        v = catalogo.version_vigente(m.versiones, dia) or catalogo.version_vigente(m.versiones)
        if not v:
            continue
        nota = str(form.get(f"nota_{m.id}") or "").strip()[:500] or None
        med = db.scalar(select(Medicion).where(Medicion.tipo == "mci", Medicion.version_id.in_([x.id for x in m.versiones]),
                                               Medicion.periodo == mes))
        if med:
            med.valor, med.nota, med.version_id, med.capturado_por = valor, nota, v.id, u.id
        else:
            db.add(Medicion(tipo="mci", version_id=v.id, periodo=mes, valor=valor, nota=nota, capturado_por=u.id))
        n += 1
    return n


def _guardar_predictivas(db: Session, u: Usuario, form, sem: str) -> int:
    """Valores semanales de las medidas predictivas."""
    n = 0
    dia = indicadores.viernes_de(sem)
    for m in indicadores.mcis_de(db):
        if not _puede_capturar(u, m):
            continue
        for p in m.predictivas:
            raw = str(form.get(f"pred_{p.id}") or "").strip().replace(",", "")
            if not raw or not p.versiones:
                continue
            try:
                valor = float(raw.rstrip("%"))
            except ValueError:
                continue
            v = catalogo.version_vigente(p.versiones, dia) or catalogo.version_vigente(p.versiones)
            med = db.scalar(select(Medicion).where(Medicion.tipo == "predictiva",
                                                   Medicion.version_id.in_([x.id for x in p.versiones]),
                                                   Medicion.periodo == sem))
            if med:
                med.valor, med.version_id, med.capturado_por = valor, v.id, u.id
            else:
                db.add(Medicion(tipo="predictiva", version_id=v.id, periodo=sem, valor=valor, capturado_por=u.id))
            n += 1
    return n


# ---------------------------------------------------------------- diagnóstico IA por área

@router.get("/diagnostico")
def ver_diagnostico(request: Request, area: str | None = None, semana: str | None = None,
                    db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    areas = _areas(db)
    if not areas:
        raise HTTPException(404, "No hay indicadores configurados.")
    sel = next((a for a in areas if a.clave == area), None) or \
        next((a for a in areas if a.id == u.area_id), None) or areas[0]
    sem = semana or semana_iso(hoy())
    d = diagnostico.ultimo(db, sel.id, sem) or diagnostico.ultimo(db, sel.id)
    estados = {a.id: diagnostico.ultimo(db, a.id, sem) for a in areas}
    previas = list(db.scalars(select(RecomendacionIA).where(RecomendacionIA.area_id == sel.id,
                                                            RecomendacionIA.ejecucion.is_not(None))
                              .order_by(RecomendacionIA.semana.desc()).limit(6)))
    hist = list(db.scalars(select(DiagnosticoSemanal).where(DiagnosticoSemanal.area_id == sel.id)
                           .order_by(DiagnosticoSemanal.creado.desc()).limit(8)))
    return render(request, "diagnostico.html", u, "diagnostico", areas=areas, sel=sel, d=d, sem=sem, estados=estados,
                  previas=previas, hist=hist, efect=diagnostico.efectividad(db, sel.id),
                  inv_usadas=diagnostico.investigaciones_usadas(db, sem),
                  inv_max=parametros.get(db, "investigaciones_por_semana"),
                  prompt=diagnostico.prompt_vigente(db, sel), puede=u.es_direccion or u.area_id == sel.id)


@router.post("/diagnostico/generar")
def generar_diagnostico(request: Request, area: str = Form(...), pregunta: str = Form(""),
                        db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    a = db.scalar(select(Area).where(Area.clave == area))
    if not a or not (u.es_direccion or u.area_id == a.id):
        raise HTTPException(403)
    try:
        diagnostico.generar(db, a, usuario_id=u.id, pregunta_manual=pregunta.strip() or None)
        flash(request, "Diagnóstico generado." + (" Incluye investigación profunda." if pregunta.strip() else ""))
    except (ia.IANoDisponible, ValueError) as e:
        flash(request, f"No se pudo generar el diagnóstico: {str(e)[:160]}", "error")
    return ir(f"/diagnostico?area={a.clave}")


@router.get("/recomendaciones/{rid}/compromiso")
def recomendacion_a_compromiso(rid: int, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    r = db.get(RecomendacionIA, rid)
    if not r:
        raise HTTPException(404)
    return ir(f"/compromisos?scope=mios&rec={r.id}#nuevo-compromiso")


@router.post("/recomendaciones/{rid}/descartar")
def descartar_recomendacion(rid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    r = db.get(RecomendacionIA, rid)
    if not r:
        raise HTTPException(404)
    r.estado = "descartada"
    auditar(db, "descartar", "recomendacion", r.id, u.id)
    db.commit()
    a = db.get(Area, r.area_id)
    return ir(f"/diagnostico?area={a.clave}")


# ---------------------------------------------------------------- prompts por área

@router.get("/admin/prompts")
def prompts(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    areas = list(db.scalars(select(Area).order_by(Area.id)))
    vig = {a.id: diagnostico.prompt_vigente(db, a) for a in areas}
    hist = {a.id: list(db.scalars(select(PromptArea).where(PromptArea.area_id == a.id).order_by(PromptArea.version.desc())))
            for a in areas}
    return render(request, "admin_prompts.html", u, "admin", areas=areas, vig=vig, hist=hist, tab="prompts")


@router.post("/admin/prompts/{aid}")
def guardar_prompt(aid: int, request: Request, especialidad: str = Form(...), instrucciones: str = Form(...),
                   motivo: str = Form(...), db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    a = db.get(Area, aid)
    if not a or not motivo.strip():
        raise HTTPException(400, "El cambio requiere motivo.")
    actual = diagnostico.prompt_vigente(db, a)
    for p in db.scalars(select(PromptArea).where(PromptArea.area_id == a.id, PromptArea.activo.is_(True))):
        p.activo = False
    db.add(PromptArea(area_id=a.id, version=(actual.version + 1) if actual else 1, especialidad=especialidad.strip()[:300],
                      instrucciones=instrucciones.strip(), motivo=motivo.strip(), creado_por=u.id))
    auditar(db, "prompt", "area", a.id, u.id, motivo=motivo)
    db.commit()
    flash(request, f"Nueva versión del especialista de {a.nombre}. Los diagnósticos anteriores conservan la versión con la que se hicieron.")
    return ir("/admin/prompts")
