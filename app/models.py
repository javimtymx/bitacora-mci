"""Modelo de datos de la Bitácora MCI.

Principios:
- La estrategia es versionada: una MCI o medida predictiva nunca se edita en sitio;
  un cambio de meta crea una versión nueva con vigencia, así el histórico conserva
  contra qué meta se midió cada semana.
- Las estrategias (palancas) llevan bitácora de eventos: por qué se activaron,
  pausaron o descartaron, con evidencia.
- Las excepciones al cumplimiento se solicitan, se aprueban y quedan registradas;
  el cálculo las aplica, pero nunca borra el dato original.
- Los parámetros del sistema (umbrales, automatizaciones) guardan historial.
- Toda revisión y acción de la IA queda registrada con su resultado y la decisión humana.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- personas y áreas

class Area(Base):
    __tablename__ = "areas"
    id: Mapped[int] = mapped_column(primary_key=True)
    clave: Mapped[str] = mapped_column(String(40), unique=True)
    nombre: Mapped[str] = mapped_column(String(120))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


ROLES = ("colaborador", "lider", "direccion", "admin")


class Usuario(Base):
    __tablename__ = "usuarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(190), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    rol: Mapped[str] = mapped_column(String(20), default="colaborador")
    area_id: Mapped[int | None] = mapped_column(ForeignKey("areas.id"))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    puede_confidencial: Mapped[bool] = mapped_column(Boolean, default=False)
    recibe_correos: Mapped[bool] = mapped_column(Boolean, default=True)
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ultimo_acceso: Mapped[datetime | None] = mapped_column(DateTime)
    baja_en: Mapped[datetime | None] = mapped_column(DateTime)
    baja_motivo: Mapped[str | None] = mapped_column(Text)

    area: Mapped[Area | None] = relationship()

    @property
    def es_lider(self) -> bool:
        return self.rol in ("lider", "direccion", "admin")

    @property
    def es_direccion(self) -> bool:
        return self.rol in ("direccion", "admin")


# ---------------------------------------------------------------- catálogo estratégico versionado

class Mci(Base):
    __tablename__ = "mci"
    id: Mapped[int] = mapped_column(primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"))
    clave: Mapped[str] = mapped_column(String(60), unique=True)
    nombre: Mapped[str] = mapped_column(String(200))
    dueno_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    area: Mapped[Area] = relationship()
    dueno: Mapped[Usuario | None] = relationship()
    versiones: Mapped[list["MciVersion"]] = relationship(back_populates="mci", order_by="MciVersion.version")
    predictivas: Mapped[list["Predictiva"]] = relationship(back_populates="mci")


class MciVersion(Base):
    __tablename__ = "mci_versiones"
    __table_args__ = (UniqueConstraint("mci_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    mci_id: Mapped[int] = mapped_column(ForeignKey("mci.id"))
    version: Mapped[int] = mapped_column(Integer)
    descripcion: Mapped[str] = mapped_column(Text)
    meta: Mapped[float | None] = mapped_column(Numeric(18, 4))
    unidad: Mapped[str] = mapped_column(String(30), default="")
    sentido: Mapped[str] = mapped_column(String(10), default="mayor")
    periodicidad: Mapped[str] = mapped_column(String(20), default="mensual")
    vigente_desde: Mapped[date] = mapped_column(Date)
    vigente_hasta: Mapped[date | None] = mapped_column(Date)
    motivo_cambio: Mapped[str | None] = mapped_column(Text)
    creado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    mci: Mapped[Mci] = relationship(back_populates="versiones")


class Predictiva(Base):
    __tablename__ = "predictivas"
    id: Mapped[int] = mapped_column(primary_key=True)
    mci_id: Mapped[int] = mapped_column(ForeignKey("mci.id"))
    clave: Mapped[str] = mapped_column(String(60), unique=True)
    nombre: Mapped[str] = mapped_column(String(200))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    mci: Mapped[Mci] = relationship(back_populates="predictivas")
    versiones: Mapped[list["PredictivaVersion"]] = relationship(
        back_populates="predictiva", order_by="PredictivaVersion.version"
    )


class PredictivaVersion(Base):
    __tablename__ = "predictiva_versiones"
    __table_args__ = (UniqueConstraint("predictiva_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    predictiva_id: Mapped[int] = mapped_column(ForeignKey("predictivas.id"))
    version: Mapped[int] = mapped_column(Integer)
    descripcion: Mapped[str] = mapped_column(Text)
    meta_semanal: Mapped[float | None] = mapped_column(Numeric(18, 4))
    unidad: Mapped[str] = mapped_column(String(30), default="")
    sentido: Mapped[str] = mapped_column(String(10), default="mayor")
    fuente: Mapped[str] = mapped_column(String(20), default="captura")
    vigente_desde: Mapped[date] = mapped_column(Date)
    vigente_hasta: Mapped[date | None] = mapped_column(Date)
    motivo_cambio: Mapped[str | None] = mapped_column(Text)
    creado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    predictiva: Mapped[Predictiva] = relationship(back_populates="versiones")


class Medicion(Base):
    """Valor de una MCI o predictiva en un periodo, ligado a la versión vigente."""
    __tablename__ = "mediciones"
    __table_args__ = (UniqueConstraint("tipo", "version_id", "periodo"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(12))  # mci | predictiva
    version_id: Mapped[int] = mapped_column(Integer)
    periodo: Mapped[str] = mapped_column(String(10))  # 2026-10 o 2026-W45
    valor: Mapped[float] = mapped_column(Numeric(18, 4))
    fuente: Mapped[str] = mapped_column(String(20), default="captura")
    nota: Mapped[str | None] = mapped_column(Text)
    capturado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


ESTADOS_ESTRATEGIA = ("propuesta", "activa", "pausada", "descartada", "exitosa")


class Estrategia(Base):
    """Palanca o iniciativa acordada. Su ciclo de vida queda en EstrategiaEvento."""
    __tablename__ = "estrategias"
    id: Mapped[int] = mapped_column(primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"))
    mci_id: Mapped[int | None] = mapped_column(ForeignKey("mci.id"))
    nombre: Mapped[str] = mapped_column(String(200))
    descripcion: Mapped[str] = mapped_column(Text, default="")
    hipotesis: Mapped[str] = mapped_column(Text, default="")
    responsable_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    estado: Mapped[str] = mapped_column(String(20), default="propuesta")
    inicio: Mapped[date | None] = mapped_column(Date)
    fin: Mapped[date | None] = mapped_column(Date)
    seguimiento: Mapped[str] = mapped_column(String(20), default="semanal")
    creado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actualizado: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    area: Mapped[Area] = relationship()
    mci: Mapped[Mci | None] = relationship()
    responsable: Mapped[Usuario | None] = relationship(foreign_keys=[responsable_id])
    eventos: Mapped[list["EstrategiaEvento"]] = relationship(
        back_populates="estrategia", order_by="EstrategiaEvento.creado"
    )


class EstrategiaEvento(Base):
    __tablename__ = "estrategia_eventos"
    id: Mapped[int] = mapped_column(primary_key=True)
    estrategia_id: Mapped[int] = mapped_column(ForeignKey("estrategias.id"))
    tipo: Mapped[str] = mapped_column(String(30))  # creada | cambio_estado | ajuste | evaluacion_ia | nota
    estado_anterior: Mapped[str | None] = mapped_column(String(20))
    estado_nuevo: Mapped[str | None] = mapped_column(String(20))
    motivo: Mapped[str] = mapped_column(Text, default="")
    evidencia: Mapped[str | None] = mapped_column(Text)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    estrategia: Mapped[Estrategia] = relationship(back_populates="eventos")
    usuario: Mapped[Usuario | None] = relationship()


# ---------------------------------------------------------------- captura

ESTADOS_COMPROMISO = ("abierto", "cumplido", "no_cumplido", "cancelado")


class Compromiso(Base):
    __tablename__ = "compromisos"
    id: Mapped[int] = mapped_column(primary_key=True)
    semana: Mapped[str] = mapped_column(String(8), index=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"))
    responsable_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    autor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    predictiva_id: Mapped[int | None] = mapped_column(ForeignKey("predictivas.id"))
    predictiva_version_id: Mapped[int | None] = mapped_column(ForeignKey("predictiva_versiones.id"))
    estrategia_id: Mapped[int | None] = mapped_column(ForeignKey("estrategias.id"))
    texto: Mapped[str] = mapped_column(Text)
    fecha_limite: Mapped[date] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(String(20), default="abierto", index=True)
    avance: Mapped[int] = mapped_column(Integer, default=0)
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actualizado: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    cerrado: Mapped[datetime | None] = mapped_column(DateTime)

    area: Mapped[Area] = relationship()
    responsable: Mapped[Usuario] = relationship(foreign_keys=[responsable_id])
    autor: Mapped[Usuario] = relationship(foreign_keys=[autor_id])
    predictiva: Mapped[Predictiva | None] = relationship()
    predictiva_version: Mapped[PredictivaVersion | None] = relationship()
    estrategia: Mapped[Estrategia | None] = relationship()
    notas: Mapped[list["CompromisoNota"]] = relationship(order_by="CompromisoNota.creado")
    remediaciones: Mapped[list["Remediacion"]] = relationship(
        back_populates="compromiso", order_by="Remediacion.creado"
    )

    @property
    def remediacion(self) -> "Remediacion | None":
        return self.remediaciones[-1] if self.remediaciones else None


class CompromisoNota(Base):
    __tablename__ = "compromiso_notas"
    id: Mapped[int] = mapped_column(primary_key=True)
    compromiso_id: Mapped[int] = mapped_column(ForeignKey("compromisos.id"))
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    avance: Mapped[int] = mapped_column(Integer)
    texto: Mapped[str] = mapped_column(Text, default="")
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    usuario: Mapped[Usuario] = relationship()


CATEGORIAS_CAUSA = (
    "dependencia_interna",
    "dependencia_cliente",
    "capacidad",
    "prioridad_cambiada",
    "compromiso_mal_definido",
    "ejecucion",
    "externa",
    "otra",
)


class Remediacion(Base):
    __tablename__ = "remediaciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    compromiso_id: Mapped[int] = mapped_column(ForeignKey("compromisos.id"))
    causa: Mapped[str] = mapped_column(Text)
    causa_categoria: Mapped[str] = mapped_column(String(40), default="otra")
    accion: Mapped[str] = mapped_column(Text)
    nueva_fecha: Mapped[date] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(String(20), default="abierta")  # abierta | cumplida | no_cumplida | cancelada
    resultado: Mapped[str | None] = mapped_column(Text)
    creado_por: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    cerrada: Mapped[datetime | None] = mapped_column(DateTime)

    compromiso: Mapped[Compromiso] = relationship(back_populates="remediaciones")


ESTADOS_IDEA = ("propuesta", "validada", "en_curso", "implementada", "descartada")


class Idea(Base):
    __tablename__ = "ideas"
    id: Mapped[int] = mapped_column(primary_key=True)
    autor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"))
    titulo: Mapped[str] = mapped_column(String(200))
    problema: Mapped[str] = mapped_column(Text)
    beneficio: Mapped[str] = mapped_column(Text, default="")
    origen: Mapped[str] = mapped_column(String(40))
    estado: Mapped[str] = mapped_column(String(20), default="propuesta")
    avance: Mapped[int] = mapped_column(Integer, default=0)
    impacto: Mapped[int | None] = mapped_column(Integer)
    esfuerzo: Mapped[int | None] = mapped_column(Integer)
    cuadrante: Mapped[str | None] = mapped_column(String(30))
    fortaleza: Mapped[str | None] = mapped_column(String(10))
    duplicado_de: Mapped[int | None] = mapped_column(ForeignKey("ideas.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actualizado: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    autor: Mapped[Usuario] = relationship()
    area: Mapped[Area] = relationship()


# ---------------------------------------------------------------- IA

class RevisionIA(Base):
    __tablename__ = "revisiones_ia"
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(30), index=True)
    entidad_id: Mapped[int | None] = mapped_column(Integer, index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    veredicto: Mapped[str] = mapped_column(String(30), default="")
    resultado: Mapped[dict] = mapped_column(JSON, default=dict)
    sugerencia: Mapped[str | None] = mapped_column(Text)
    aceptada: Mapped[bool | None] = mapped_column(Boolean)
    motivo_rechazo: Mapped[str | None] = mapped_column(Text)
    modelo: Mapped[str] = mapped_column(String(60), default="")
    prompt_version: Mapped[str] = mapped_column(String(20), default="")
    tokens_entrada: Mapped[int] = mapped_column(Integer, default=0)
    tokens_salida: Mapped[int] = mapped_column(Integer, default=0)
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- excepciones y parámetros

class Excepcion(Base):
    """Excepción al cumplimiento. Una vez aprobada, el cálculo la respeta;
    el registro original no se modifica."""
    __tablename__ = "excepciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo_entidad: Mapped[str] = mapped_column(String(20))  # compromiso | predictiva | mci | persona
    entidad_id: Mapped[int] = mapped_column(Integer, index=True)
    periodo_desde: Mapped[date] = mapped_column(Date)
    periodo_hasta: Mapped[date] = mapped_column(Date)
    categoria: Mapped[str] = mapped_column(String(40))
    efecto: Mapped[str] = mapped_column(String(20), default="excluir")  # excluir | meta_ajustada
    meta_ajustada: Mapped[float | None] = mapped_column(Numeric(18, 4))
    justificacion: Mapped[str] = mapped_column(Text)
    solicitada_por: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    estado: Mapped[str] = mapped_column(String(20), default="solicitada")  # solicitada | aprobada | rechazada
    resuelta_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    resuelta_en: Mapped[datetime | None] = mapped_column(DateTime)
    comentario_resolucion: Mapped[str | None] = mapped_column(Text)
    visible_socio: Mapped[bool] = mapped_column(Boolean, default=True)
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    solicitante: Mapped[Usuario] = relationship(foreign_keys=[solicitada_por])
    resolvio: Mapped[Usuario | None] = relationship(foreign_keys=[resuelta_por])


class Parametro(Base):
    __tablename__ = "parametros"
    clave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[dict] = mapped_column(JSON)
    descripcion: Mapped[str] = mapped_column(Text, default="")
    categoria: Mapped[str] = mapped_column(String(40), default="general")
    actualizado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actualizado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))


class ParametroHistorial(Base):
    __tablename__ = "parametros_historial"
    id: Mapped[int] = mapped_column(primary_key=True)
    clave: Mapped[str] = mapped_column(String(60), index=True)
    valor_anterior: Mapped[dict | None] = mapped_column(JSON)
    valor_nuevo: Mapped[dict] = mapped_column(JSON)
    motivo: Mapped[str] = mapped_column(Text, default="")
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- agente: acciones e informes

class AccionAgente(Base):
    __tablename__ = "acciones_agente"
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(20))  # correo | recordatorio | informe
    origen: Mapped[str] = mapped_column(String(30))  # agente | plan_remediacion | estrategia | usuario
    titulo: Mapped[str] = mapped_column(String(200))
    destinatarios: Mapped[list] = mapped_column(JSON, default=list)  # ids de usuario
    asunto: Mapped[str] = mapped_column(String(200), default="")
    cuerpo: Mapped[str] = mapped_column(Text, default="")
    instrucciones: Mapped[str | None] = mapped_column(Text)
    entidad_tipo: Mapped[str | None] = mapped_column(String(30))
    entidad_id: Mapped[int | None] = mapped_column(Integer)
    programada_para: Mapped[datetime | None] = mapped_column(DateTime)
    estado: Mapped[str] = mapped_column(String(20), default="propuesta", index=True)
    requiere_aprobacion: Mapped[bool] = mapped_column(Boolean, default=True)
    aprobada_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    aprobada_en: Mapped[datetime | None] = mapped_column(DateTime)
    ejecutada_en: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)
    informe_id: Mapped[int | None] = mapped_column(ForeignKey("informes.id"))
    creado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Informe(Base):
    __tablename__ = "informes"
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(30), index=True)  # pm_diario | remediacion | estrategia | semanal | socio
    titulo: Mapped[str] = mapped_column(String(200))
    periodo: Mapped[str] = mapped_column(String(20), default="")
    contenido: Mapped[str] = mapped_column(Text, default="")
    datos: Mapped[dict] = mapped_column(JSON, default=dict)
    origen: Mapped[str] = mapped_column(String(20), default="agente")
    generado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------- ventas desde CFDI

class VentaMensual(Base):
    __tablename__ = "ventas_mensuales"
    periodo: Mapped[str] = mapped_column(String(7), primary_key=True)
    total_mxn: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    facturas: Mapped[int] = mapped_column(Integer, default=0)
    clientes: Mapped[int] = mapped_column(Integer, default=0)
    clientes_nuevos: Mapped[int] = mapped_column(Integer, default=0)
    total_nuevos_mxn: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    sincronizado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VentaClienteMes(Base):
    """Confidencial: solo usuarios con puede_confidencial."""
    __tablename__ = "ventas_cliente_mes"
    __table_args__ = (UniqueConstraint("periodo", "rfc"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    periodo: Mapped[str] = mapped_column(String(7), index=True)
    rfc: Mapped[str] = mapped_column(String(13))
    nombre: Mapped[str] = mapped_column(String(255), default="")
    total_mxn: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    facturas: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------- vista para el socio y auditoría

class PublicacionSocio(Base):
    """Corte consolidado que Dirección publica para el socio. Se arma a partir de los
    mismos datos; las metas ajustadas y excepciones aprobadas se muestran como tales."""
    __tablename__ = "publicaciones_socio"
    id: Mapped[int] = mapped_column(primary_key=True)
    periodo: Mapped[str] = mapped_column(String(7))
    titulo: Mapped[str] = mapped_column(String(200))
    contenido: Mapped[dict] = mapped_column(JSON, default=dict)
    narrativa: Mapped[str] = mapped_column(Text, default="")
    estado: Mapped[str] = mapped_column(String(20), default="borrador")
    creado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    publicada_en: Mapped[datetime | None] = mapped_column(DateTime)
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Auditoria(Base):
    __tablename__ = "auditoria"
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    actor: Mapped[str] = mapped_column(String(20), default="usuario")  # usuario | agente | sistema
    accion: Mapped[str] = mapped_column(String(60))
    entidad: Mapped[str] = mapped_column(String(40))
    entidad_id: Mapped[int | None] = mapped_column(Integer)
    detalle: Mapped[dict] = mapped_column(JSON, default=dict)
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
