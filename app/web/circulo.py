"""Círculo de Innovación: consolidación IA, votación por puntos y decisiones."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CirculoInnovacion, Hallazgo, Idea, Iniciativa, Objecion, Usuario
from ..services import ia, innovacion
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
    return render(request, "circulo.html", u, "circulo", c=c, mios=mios, usados=sum(mios.values()),
                  votaron=votaron, nombres=nombres, ideas=ideas, mapa=graficas.mapa_iniciativas(c.iniciativas),
                  conteo=innovacion.conteo(c), visibles=resultados_visibles,
                  total_puntos=sum(p for _, p in innovacion.conteo(c)),
                  responsables=[x for x in nombres.values() if x.activo])


@router.post("/{cid}/consolidar")
def consolidar(cid: int, request: Request, db: Session = Depends(get_db), u: Usuario = Depends(requiere_lider)):
    c = _circulo(db, cid)
    if c.estado not in ("preparacion", "votacion"):
        raise HTTPException(400, "El círculo ya pasó a decisiones.")
    if innovacion.votantes(c):
        raise HTTPException(400, "Ya hay votos; no se puede volver a consolidar.")
    try:
        innovacion.consolidar(db, c, u)
        flash(request, f"La IA consolidó {len(c.iniciativas)} iniciativas. La votación está abierta.")
    except (ia.IANoDisponible, ValueError) as e:
        flash(request, f"La IA no pudo consolidar: {str(e)[:160]}", "error")
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
