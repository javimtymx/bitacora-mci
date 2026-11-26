"""Círculo de Innovación: consolidación IA, votación por puntos y decisiones."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CirculoInnovacion, Hallazgo, Idea, Iniciativa, Objecion, PropuestaValor, Usuario
from ..services import ia, innovacion
from ..services import propuesta_valor as pvs
from ..services.util import auditar, hoy
from . import graficas
from .base import flash, ir, render, requiere_direccion, requiere_lider, usuario_actual

router = APIRouter(prefix="/circulo")
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


@router.get("")
def lista(request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    cs = list(db.scalars(select(CirculoInnovacion).order_by(CirculoInnovacion.creado.desc()).limit(24)))
    f = innovacion.fuentes(db)
    return render(request, "circulo_lista.html", u, "circulo", circulos=cs, mes_def=hoy().strftime("%Y-%m"),
                  n_ideas=len(f["ideas"]), n_hall=len(f["hallazgos"]), n_obj=len(f["objeciones"]),
                  n_quejas=len(f["quejas"]), n_bench=len(f["benchmark"]))


@router.post("")
def crear(request: Request, mes: str = Form(...), puntos: int = Form(5), maximo: int = Form(3),
          db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    try:
        y, m = map(int, mes.split("-"))
    except ValueError:
        raise HTTPException(400, "Mes con formato AAAA-MM.")
    c = CirculoInnovacion(mes=mes, nombre=f"Círculo de Innovación · {MESES[m - 1]} {y}",
                          puntos_por_persona=max(1, min(puntos, 20)), max_por_iniciativa=max(1, min(maximo, 10)),
                          creado_por=u.id)
    db.add(c)
    db.flush()
    auditar(db, "crear", "circulo", c.id, u.id)
    db.commit()
    return ir(f"/circulo/{c.id}")


def _circulo(db: Session, cid: int) -> CirculoInnovacion:
    c = db.get(CirculoInnovacion, cid)
    if not c:
        raise HTTPException(404)
    return c


@router.get("/{cid}")
def ver(cid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    c = _circulo(db, cid)
    mios = innovacion.mis_puntos(db, c, u.id)
    votaron = innovacion.votantes(c)
    nombres = {x.id: x for x in db.scalars(select(Usuario))}
    ideas = {i.id: i for i in db.scalars(select(Idea))}
    resultados_visibles = c.estado in ("decisiones", "cerrado")
    props = {ini.id: pvs.ultima(db, ini) for ini in c.iniciativas}
    return render(request, "circulo.html", u, "circulo", c=c, mios=mios, usados=sum(mios.values()), props=props,
                  consolidando=innovacion.consolidando(c),
                  votaron=votaron, nombres=nombres, ideas=ideas, mapa=graficas.mapa_iniciativas(c.iniciativas),
                  conteo=innovacion.conteo(c), visibles=resultados_visibles,
                  total_puntos=sum(p for _, p in innovacion.conteo(c)),
                  responsables=[x for x in nombres.values() if x.activo])


@router.post("/{cid}/consolidar")
def consolidar(cid: int, request: Request, fondo: BackgroundTasks, db: Session = Depends(get_db),
               u: Usuario = Depends(requiere_lider)):
    """Corre en segundo plano: la página no espera a la IA (así no hay tiempo agotado del navegador ni del proxy)."""
    c = _circulo(db, cid)
    if c.estado not in ("preparacion", "votacion"):
        raise HTTPException(400, "El círculo ya pasó a decisiones.")
    if innovacion.votantes(c):
        raise HTTPException(400, "Ya hay votos; no se puede volver a consolidar.")
    if innovacion.consolidando(c):
        flash(request, "GuardIA ya está consolidando; la página se actualiza sola al terminar.")
        return ir(f"/circulo/{cid}")
    innovacion.marcar_consolidando(c)
    db.commit()
    fondo.add_task(innovacion.consolidar_en_fondo, c.id, u.id)
    flash(request, "GuardIA está consolidando las iniciativas (1 a 3 minutos). Puedes salir; la página se actualiza sola.")
    return ir(f"/circulo/{cid}")


# ---------------------------------------------------------------- propuesta de valor por iniciativa

def _iniciativa(db: Session, cid: int, iid: int) -> Iniciativa:
    ini = db.get(Iniciativa, iid)
    if not ini or ini.circulo_id != cid:
        raise HTTPException(404)
    return ini


@router.get("/{cid}/iniciativas/{iid}/propuesta")
def propuesta(cid: int, iid: int, request: Request, fondo: BackgroundTasks, v: int | None = None,
              db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    ini = _iniciativa(db, cid, iid)
    versiones = list(db.scalars(select(PropuestaValor).where(PropuestaValor.iniciativa_id == ini.id)
                                .order_by(PropuestaValor.version.desc())))
    pv = next((x for x in versiones if x.version == v), None) if v else (versiones[0] if versiones else None)
    if pv and pv.estado in ("pendiente", "corriendo") and pvs.vencida(pv):
        fondo.add_task(pvs.correr, pv.id)  # quedó a medias (reinicio o tiempo agotado): se reanuda sola
    return render(request, "propuesta_valor.html", u, "circulo", c=ini.circulo, ini=ini, pv=pv, versiones=versiones,
                  avance=pvs.avance(pv) if pv else None, etapas=pvs.ETAPAS, pantallas=pvs.pantallas(pv) if pv else [],
                  prioridad_txt=pvs.PRIORIDAD_TXT, ev={it["c"]: it for it in (pv.evidencia or [])} if pv else {})


@router.post("/{cid}/iniciativas/{iid}/propuesta")
def propuesta_generar(cid: int, iid: int, request: Request, fondo: BackgroundTasks, db: Session = Depends(get_db),
                      u: Usuario = Depends(requiere_lider)):
    ini = _iniciativa(db, cid, iid)
    pv = pvs.iniciar(db, ini, u)
    db.commit()
    fondo.add_task(pvs.correr, pv.id)
    flash(request, "GuardIA está construyendo la propuesta de valor por etapas (3 a 8 minutos). Puedes salir: cada etapa "
                   "se guarda y, si algo se interrumpe, continúa donde se quedó.")
    return ir(f"/circulo/{cid}/iniciativas/{iid}/propuesta")


@router.post("/{cid}/iniciativas/{iid}/propuesta/{pid}/reintentar")
def propuesta_reintentar(cid: int, iid: int, pid: int, request: Request, fondo: BackgroundTasks,
                         db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    ini = _iniciativa(db, cid, iid)
    pv = db.get(PropuestaValor, pid)
    if not pv or pv.iniciativa_id != ini.id:
        raise HTTPException(404)
    if pv.estado == "error":
        pvs.reintentar(db, pv)
        db.commit()
    fondo.add_task(pvs.correr, pv.id)
    flash(request, "Se reanuda desde la etapa que falló; lo ya generado se conserva.")
    return ir(f"/circulo/{cid}/iniciativas/{iid}/propuesta")


@router.get("/propuestas/{pid}/avance")
def propuesta_avance(pid: int, fondo: BackgroundTasks, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    pv = db.get(PropuestaValor, pid)
    if not pv:
        raise HTTPException(404)
    if pv.estado in ("pendiente", "corriendo") and pvs.vencida(pv):
        fondo.add_task(pvs.correr, pv.id)
    return JSONResponse(pvs.avance(pv))


@router.get("/{cid}/iniciativas/{iid}/propuesta/{pid}.md")
def propuesta_md(cid: int, iid: int, pid: int, prompt: int = 0, db: Session = Depends(get_db),
                 u: Usuario = Depends(usuario_actual)):
    ini = _iniciativa(db, cid, iid)
    pv = db.get(PropuestaValor, pid)
    if not pv or pv.iniciativa_id != ini.id:
        raise HTTPException(404)
    texto = pv.prompt_prototipo if prompt else pvs.markdown(pv)
    nombre = ("prompt-prototipo" if prompt else "propuesta-valor") + f"-iniciativa-{ini.id}-v{pv.version}.md"
    return Response(texto, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.post("/{cid}/propuestas")
def propuestas_todas(cid: int, request: Request, fondo: BackgroundTasks, db: Session = Depends(get_db),
                     u: Usuario = Depends(requiere_lider)):
    """Profundiza todas las iniciativas que aún no tienen propuesta (una tras otra)."""
    c = _circulo(db, cid)
    ids = []
    for ini in c.iniciativas:
        previa = pvs.ultima(db, ini)
        if previa and previa.estado in ("listo", "pendiente", "corriendo"):
            continue
        ids.append(pvs.iniciar(db, ini, u).id)
    db.commit()
    if ids:
        fondo.add_task(pvs.correr_varias, ids)
    flash(request, f"GuardIA va a profundizar {len(ids)} iniciativa(s), una tras otra." if ids
          else "Todas las iniciativas ya tienen su propuesta de valor (o están en proceso).")
    return ir(f"/circulo/{cid}")


@router.post("/{cid}/votar")
def votar(cid: int, request: Request, iniciativa_id: int = Form(...), puntos: int = Form(...),
          db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    c = _circulo(db, cid)
    ini = db.get(Iniciativa, iniciativa_id)
    if not ini or ini.circulo_id != c.id:
        raise HTTPException(404)
    err = innovacion.votar(db, c, ini, u, puntos)
    if err:
        flash(request, err, "error")
    return ir(f"/circulo/{cid}#ini-{ini.id}")


@router.post("/{cid}/cerrar-votacion")
def cerrar_votacion(cid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    c = _circulo(db, cid)
    if c.estado != "votacion":
        raise HTTPException(400)
    c.estado = "decisiones"
    auditar(db, "cerrar_votacion", "circulo", c.id, u.id, votantes=len(innovacion.votantes(c)))
    db.commit()
    return ir(f"/circulo/{cid}#decisiones")


@router.post("/{cid}/decisiones")
async def decisiones(cid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_direccion)):
    c = _circulo(db, cid)
    if c.estado != "decisiones":
        raise HTTPException(400, "Primero cierra la votación.")
    form = await request.form()
    dec = {}
    for ini in c.iniciativas:
        d = str(form.get(f"decision_{ini.id}") or "")
        if d not in ("estrategia", "proyecto", "banco", "descartar"):
            continue
        rid = str(form.get(f"responsable_{ini.id}") or "")
        fr = str(form.get(f"revision_{ini.id}") or "")
        dec[ini.id] = {"decision": d, "responsable_id": int(rid) if rid.isdigit() else None,
                       "primera_revision": date.fromisoformat(fr) if fr else None}
    n = innovacion.decidir(db, c, u, dec)
    flash(request, f"Decisiones guardadas. Se crearon {n} estrategia(s) ligadas a su MCI y se avisó a quienes aportaron las ideas.")
    return ir(f"/circulo/{cid}")


@router.get("/{cid}/fuentes/{iid}")
def fuentes(cid: int, iid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(usuario_actual)):
    ini = db.get(Iniciativa, iid)
    if not ini or ini.circulo_id != cid:
        raise HTTPException(404)
    ideas = [db.get(Idea, i) for i in ini.fuentes.get("ideas", [])]
    hall = [db.get(Hallazgo, i) for i in ini.fuentes.get("hallazgos", [])]
    objs = [db.get(Objecion, i) for i in ini.fuentes.get("objeciones", [])]
    from ..models import InsightBenchmark, PendienteCliente
    from ..services import clientes as cl
    quejas = [x for x in (db.get(PendienteCliente, i) for i in ini.fuentes.get("quejas", [])) if x]
    bench = [x for x in (db.get(InsightBenchmark, i) for i in ini.fuentes.get("benchmark", [])) if x]

    def ve_sesion(s):  # la transcripción solo la abre quien ya puede verla; los demás ven la cita
        return bool(s) and (u.es_direccion or s.autor_id == u.id or (u.rol == "lider" and u.area_id == s.area_id))
    return render(request, "circulo_fuentes.html", u, "circulo", ini=ini, ideas=[x for x in ideas if x],
                  hall=[x for x in hall if x], objs=[x for x in objs if x], quejas=quejas, bench=bench,
                  ve_sesion=ve_sesion, nombre_cliente=cl.nombre_cliente)
