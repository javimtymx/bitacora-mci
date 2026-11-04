"""Parámetros del sistema con historial.

Cada cambio queda en parametros_historial con su motivo, para explicar después por
qué un semáforo o una automatización se comportó como lo hizo en cierta fecha."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Parametro, ParametroHistorial, utcnow

DEFAULTS: dict[str, tuple[object, str, str]] = {
    "umbral_verde": (85, "Porcentaje de cumplimiento desde el cual una persona o área está en verde.", "semaforo"),
    "umbral_amarillo": (65, "Porcentaje de cumplimiento desde el cual está en amarillo; debajo es rojo.", "semaforo"),
    "dias_sin_captura_revisar": (3, "Días sin registrar nada para marcar a una persona como «Revisar».", "seguimiento"),
    "dias_sin_captura_atender": (5, "Días sin registrar nada para marcar a una persona como «Atender».", "seguimiento"),
    "dias_max_remediacion": (14, "Plazo máximo, en días, para la nueva fecha de una remediación.", "seguimiento"),
    "dias_estrategia_sin_actividad": (21, "Días sin compromisos ligados para alertar que una estrategia activa está detenida.", "seguimiento"),
    "ia_revision_obligatoria": (True, "Si está activo, no se puede guardar sin pasar por la revisión de la IA (salvo que la IA no esté disponible).", "ia"),
    "auto_recordatorio_remediacion": (True, "Enviar sin aprobación el recordatorio un día antes de la nueva fecha de una remediación.", "automatizacion"),
    "auto_recordatorio_sin_compromiso": (False, "Enviar sin aprobación el recordatorio a quien no ha registrado compromiso de la semana.", "automatizacion"),
    "auto_seguimiento_estrategias": (False, "Enviar sin aprobación el correo semanal de seguimiento a responsables de estrategias activas.", "automatizacion"),
    "evidencia_cierre_obligatoria": (True, "Para cerrar un compromiso como cumplido se debe subir al menos un archivo de evidencia; GuardIA revisa si la evidencia demuestra el cumplimiento.", "evidencia"),
    "evidencia_sesion_obligatoria": (False, "Exigir transcripción verificada para cerrar como cumplido un compromiso cuya medida predictiva pide evidencia (sesiones 1:1, objeciones).", "evidencia"),
    "ticket_sla_horas_p1": (4, "Horas para resolver un ticket P1 (bloquea la operación de un cliente).", "tickets"),
    "ticket_sla_horas_p2": (24, "Horas para resolver un ticket P2.", "tickets"),
    "ticket_sla_horas_p3": (72, "Horas para resolver un ticket P3.", "tickets"),
    "ticket_sla_horas_p4": (168, "Horas para resolver un ticket P4 (dudas y mejoras).", "tickets"),
    "ticket_ia_segundos": (15, "Segundos que se espera a GuardIA al levantar un ticket; si no responde, se pasa al formulario manual.", "tickets"),
    "investigaciones_por_semana": (3, "Máximo de investigaciones profundas (búsqueda web) que la IA puede ejecutar por semana en toda la empresa.", "ia"),
    "auto_diagnostico_semanal": (True, "Generar cada lunes el diagnóstico de la IA para cada área.", "automatizacion"),
    "auto_plan_remediacion": (False, "Ejecutar sin aprobación las acciones del plan de remediación que propone la IA.", "automatizacion"),
}


def get(db: Session, clave: str):
    p = db.get(Parametro, clave)
    if p is None:
        return DEFAULTS[clave][0]
    return p.valor.get("v")


def todos(db: Session) -> list[dict]:
    out = []
    for clave, (default, desc, cat) in DEFAULTS.items():
        p = db.get(Parametro, clave)
        out.append({"clave": clave, "valor": p.valor.get("v") if p else default, "default": default,
                    "descripcion": desc, "categoria": cat, "actualizado": p.actualizado if p else None})
    return out


def set_(db: Session, clave: str, valor, usuario_id: int | None, motivo: str) -> None:
    if clave not in DEFAULTS:
        raise KeyError(clave)
    default = DEFAULTS[clave][0]
    if isinstance(default, bool):
        valor = str(valor).lower() in ("1", "true", "on", "si", "sí")
    elif isinstance(default, int):
        valor = int(valor)
    p = db.get(Parametro, clave)
    anterior = p.valor if p else None
    if p is None:
        p = Parametro(clave=clave, valor={"v": valor}, descripcion=DEFAULTS[clave][1], categoria=DEFAULTS[clave][2])
        db.add(p)
    else:
        p.valor = {"v": valor}
    p.actualizado = utcnow()
    p.actualizado_por = usuario_id
    db.add(ParametroHistorial(clave=clave, valor_anterior=anterior, valor_nuevo={"v": valor},
                              motivo=motivo, usuario_id=usuario_id))
