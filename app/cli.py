"""Comandos de administración.

    python -m app.cli init-db          # crea tablas (en producción usa: alembic upgrade head)
    python -m app.cli seed             # áreas, MCI, medidas predictivas y líderes de los tableros
    python -m app.cli crear-admin --email jmartinez@onecore.mx --nombre "Javier Martínez"
"""
from __future__ import annotations

import argparse
import getpass
import secrets
from datetime import date

from sqlalchemy import select

from .db import Base, SessionLocal, engine
from .models import Area, Mci, Predictiva, Usuario
from .services import catalogo
from .web.base import hash_password

# Catálogo inicial tomado de los tableros MCI de cada área.
CATALOGO = [
    ("ventas", "Ventas", ("Manuel Coronado", "mcoronado@onecore.mx"), [
        ("ventas-base", "Crecimiento de la base corporativa",
         "Incrementar 35% la base corporativa: de $[LÍNEA BASE] a $[META] para el [FECHA]", 35, "%", [
             "Análisis 1:1 de riesgo con IA", "Webinar mensual para clientes", "Publicación de nuevos módulos",
             "Webinar para prospectos", "Objeciones documentadas con respuesta", "Citas por LinkedIn o networking"]),
    ]),
    ("innovacion", "Innovación", ("Gisela Treviño", "gtrevino@onecore.mx"), [
        ("innov-mejoras", "Una mejora por empleado al mes",
         "Implementar 1 mejora por empleado al mes: de [X] a [N EMPLEADOS] mejoras mensuales para el [FECHA]", None, "mejoras", [
             "Avance semanal de 25% en idea verificada", "Habilidad nueva del bimestre", "Círculo de innovación mensual"]),
    ]),
    ("retencion", "Retención", ("Mario Pineda", "mpineda@onecore.mx"), [
        ("ret-corp", "Retención de clientes corporativos",
         "Tasa de retención de clientes corporativos: de [X]% a 95% para el [FECHA]", 95, "%", [
             "Uso de 90% en módulos contratados", "Sesiones 1:1 con minuta resuelta",
             "Dos o más usuarios estratégicos por cuenta", "Calificaciones bajas atendidas"]),
    ]),
    ("operaciones", "Operaciones", ("Erick Garza", "egarza@onecore.mx"), [
        ("ops-cero-errores", "Transacciones con cero errores",
         "Mantener 99.98% de transacciones con cero errores: de [X]% a 99.98% para el [FECHA]", 99.98, "%", [
             "Criterios con justificación recurrente ajustados", "SAFE ejecutado a más tardar el día siguiente",
             "Analítico por patente aduanal"]),
        ("ops-alertas", "Mitigación preventiva de alertas",
         "Mitigar preventivamente al menos el 95% de las alertas antes del pago del pedimento", 95, "%", [
             "Alertas resueltas antes del pago", "Fuentes de información analizadas"]),
        ("ops-criterios", "15 criterios de cumplimiento al mes",
         "Implementar 15 criterios de cumplimiento generales pre / post pago por mes", 15, "criterios", [
             "Criterios nuevos con IA"]),
    ]),
    ("compliance", "Compliance", ("Héctor Basave", "hbasave@onecore.mx"), [
        ("comp-360", "Análisis 360 a todos los clientes",
         "Que el 100% de los clientes reciban su análisis 360 cada mes: de [X]% a 100% para el [FECHA]", 100, "%", [
             "Analítico nuevo por módulo", "Informe 360 presentado en reunión mensual"]),
    ]),
    ("direccion", "Dirección", None, [
        ("dir-automatizacion", "Automatización de procesos",
         "Automatizar el 80% de los procesos digitales manuales para fines de 2027", 80, "%", [
             "Procesos evaluados con caso de inversión", "Automatizaciones con avance semanal",
             "Horas liberadas reasignadas"]),
        ("dir-aceptacion-ia", "Aceptación de IA",
         "Tasa de aceptación de sugerencias de IA del 85% para el Q4 2027", 85, "%", [
             "Rechazos de IA analizados", "Ajustes de IA desplegados"]),
    ]),
]


def _slug(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return "-".join("".join(c if c.isalnum() else " " for c in t).split())[:55]


def init_db() -> None:
    Base.metadata.create_all(engine)
    print("Tablas creadas.")


def seed() -> None:
    desde = date(date.today().year, 1, 1)
    with SessionLocal() as db:
        admin = db.scalar(select(Usuario).where(Usuario.rol.in_(("admin", "direccion"))).limit(1))
        for clave, nombre, lider, mcis in CATALOGO:
            area = db.scalar(select(Area).where(Area.clave == clave))
            if not area:
                area = Area(clave=clave, nombre=nombre)
                db.add(area)
                db.flush()
            dueno = admin
            if lider:
                dueno = db.scalar(select(Usuario).where(Usuario.email == lider[1]))
                if not dueno:
                    dueno = Usuario(nombre=lider[0], email=lider[1], rol="lider", area_id=area.id,
                                    puede_confidencial=(clave == "ventas"),
                                    password_hash=hash_password(secrets.token_urlsafe(16)))
                    db.add(dueno)
                    db.flush()
            elif admin and not admin.area_id:
                admin.area_id = area.id
            for mclave, mnombre, desc, meta, unidad, preds in mcis:
                if db.scalar(select(Mci).where(Mci.clave == mclave)):
                    continue
                m = Mci(area_id=area.id, clave=mclave, nombre=mnombre, dueno_id=dueno.id if dueno else None)
                db.add(m)
                db.flush()
                catalogo.nueva_version_mci(db, m, descripcion=desc, meta=meta, unidad=unidad, sentido="mayor",
                                           periodicidad="mensual", vigente_desde=desde, motivo="Carga inicial",
                                           usuario_id=admin.id if admin else None)
                for pn in preds:
                    p = Predictiva(mci_id=m.id, clave=f"{mclave}-{_slug(pn)}"[:60], nombre=pn)
                    db.add(p)
                    db.flush()
                    catalogo.nueva_version_predictiva(db, p, descripcion=pn, meta_semanal=None, unidad="",
                                                      sentido="mayor", fuente="captura", vigente_desde=desde,
                                                      motivo="Carga inicial", usuario_id=admin.id if admin else None)
        db.commit()
    print("Catálogo inicial cargado. Revisa correos de líderes y metas en Configurar → Catálogo.")


def crear_admin(email: str, nombre: str, password: str | None) -> None:
    password = password or getpass.getpass("Contraseña (mínimo 10 caracteres): ")
    if len(password) < 10:
        raise SystemExit("La contraseña debe tener al menos 10 caracteres.")
    with SessionLocal() as db:
        u = db.scalar(select(Usuario).where(Usuario.email == email.lower()))
        if u:
            u.rol, u.activo, u.puede_confidencial = "admin", True, True
            u.password_hash = hash_password(password)
        else:
            db.add(Usuario(nombre=nombre, email=email.lower(), rol="admin", puede_confidencial=True,
                           password_hash=hash_password(password)))
        db.commit()
    print(f"Administrador listo: {email}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("seed")
    a = sub.add_parser("crear-admin")
    a.add_argument("--email", required=True)
    a.add_argument("--nombre", required=True)
    a.add_argument("--password")
    args = ap.parse_args()
    if args.cmd == "init-db":
        init_db()
    elif args.cmd == "seed":
        seed()
    else:
        crear_admin(args.email, args.nombre, args.password)


if __name__ == "__main__":
    main()
