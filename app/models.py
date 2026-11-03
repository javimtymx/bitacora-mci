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
    # nivel: la meta se compara directo cada semana (ej. retención 95%).
    # acumulado: se avanza de linea_base a meta para fecha_meta; el ritmo esperado es lineal.
    tipo_meta: Mapped[str] = mapped_column(String(12), default="nivel")
    linea_base: Mapped[float | None] = mapped_column(Numeric(18, 4))
    fecha_meta: Mapped[date | None] = mapped_column(Date)
    # captura | cfdi_recurrente | cfdi_total. Las fuentes CFDI se miden solas como % de crecimiento contra la base.
    fuente: Mapped[str] = mapped_column(String(20), default="captura")
    base_desde: Mapped[str | None] = mapped_column(String(7))  # AAAA-MM; vacío = primeros 3 meses con datos
    base_hasta: Mapped[str | None] = mapped_column(String(7))
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
    requiere_evidencia: Mapped[bool] = mapped_column(Boolean, default=False)

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
    recomendacion_id: Mapped[int | None] = mapped_column(ForeignKey("recomendaciones_ia.id"))
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


class ClienteEstatus(Base):
    """Estatus del cliente en Panel AYF (saas_estatus_cliente): recurrente y baja."""
    __tablename__ = "clientes_estatus"
    rfc: Mapped[str] = mapped_column(String(13), primary_key=True)
    recurrente: Mapped[bool] = mapped_column(Boolean, default=False)
    baja: Mapped[bool] = mapped_column(Boolean, default=False)
    fecha_baja: Mapped[date | None] = mapped_column(Date)
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


# ---------------------------------------------------------------- diagnóstico semanal por área

class PromptArea(Base):
    """Especialización de la IA por área. Versionado: cada diagnóstico guarda con qué versión se hizo."""
    __tablename__ = "prompts_area"
    __table_args__ = (UniqueConstraint("area_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"))
    version: Mapped[int] = mapped_column(Integer)
    especialidad: Mapped[str] = mapped_column(String(300))
    instrucciones: Mapped[str] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    motivo: Mapped[str] = mapped_column(Text, default="")
    creado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    area: Mapped[Area] = relationship()


class DiagnosticoSemanal(Base):
    """Foto semanal del diagnóstico de la IA por área: lo que vio, lo que concluyó y lo que recomendó."""
    __tablename__ = "diagnosticos_semanales"
    id: Mapped[int] = mapped_column(primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"), index=True)
    semana: Mapped[str] = mapped_column(String(8), index=True)
    veredicto: Mapped[str] = mapped_column(String(20), default="")
    resultado: Mapped[dict] = mapped_column(JSON, default=dict)
    investigacion: Mapped[dict | None] = mapped_column(JSON)
    contexto: Mapped[str] = mapped_column(Text, default="")
    prompt_area_id: Mapped[int | None] = mapped_column(ForeignKey("prompts_area.id"))
    modelo: Mapped[str] = mapped_column(String(60), default="")
    prompt_version: Mapped[str] = mapped_column(String(20), default="")
    generado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    area: Mapped[Area] = relationship()
    prompt_area: Mapped[PromptArea | None] = relationship()
    recomendaciones: Mapped[list["RecomendacionIA"]] = relationship(
        back_populates="diagnostico", order_by="RecomendacionIA.orden")


class RecomendacionIA(Base):
    __tablename__ = "recomendaciones_ia"
    id: Mapped[int] = mapped_column(primary_key=True)
    diagnostico_id: Mapped[int] = mapped_column(ForeignKey("diagnosticos_semanales.id"))
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"), index=True)
    semana: Mapped[str] = mapped_column(String(8))
    orden: Mapped[int] = mapped_column(Integer, default=1)
    texto: Mapped[str] = mapped_column(Text)
    tecnica: Mapped[str] = mapped_column(String(200), default="")
    impacto: Mapped[str] = mapped_column(String(10), default="medio")
    estado: Mapped[str] = mapped_column(String(20), default="propuesta")  # propuesta | convertida | descartada
    ejecucion: Mapped[str | None] = mapped_column(String(20))  # la IA la evalúa la semana siguiente: si | parcial | no
    efecto: Mapped[str | None] = mapped_column(Text)
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    diagnostico: Mapped[DiagnosticoSemanal] = relationship(back_populates="recomendaciones")


# ---------------------------------------------------------------- voz del cliente

CUADRANTES = {
    "producto": "Producto e innovación",
    "plataforma": "Plataforma",
    "servicio": "Servicio y atención",
    "valor": "Valor comercial",
}
TIPOS_OBJECION = ("precio", "urgencia", "autoridad", "necesidad", "confianza", "competencia", "otra")


class SesionCliente(Base):
    """Sesión con cliente (1:1 de retención u objeciones de ventas). Es evidencia de ejecución y fuente de insights."""
    __tablename__ = "sesiones_cliente"
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(20))  # retencion | objeciones
    area_id: Mapped[int] = mapped_column(ForeignKey("areas.id"))
    compromiso_id: Mapped[int | None] = mapped_column(ForeignKey("compromisos.id"), index=True)
    cliente_rfc: Mapped[str] = mapped_column(String(13), default="")
    cliente_nombre: Mapped[str] = mapped_column(String(255), default="")
    fecha: Mapped[date] = mapped_column(Date)
    participantes: Mapped[str] = mapped_column(String(300), default="")
    consentimiento: Mapped[bool] = mapped_column(Boolean, default=False)
    transcripcion: Mapped[str] = mapped_column(Text().with_variant(Text(length=4294967295), "mysql"))
    cifrada: Mapped[bool] = mapped_column(Boolean, default=False)
    palabras: Mapped[int] = mapped_column(Integer, default=0)
    estado: Mapped[str] = mapped_column(String(20), default="cargada")  # cargada | analizada | error
    resultado: Mapped[dict] = mapped_column(JSON, default=dict)
    riesgo_baja: Mapped[str] = mapped_column(String(10), default="")
    evidencia_verificada: Mapped[bool] = mapped_column(Boolean, default=False)
    citas_descartadas: Mapped[int] = mapped_column(Integer, default=0)
    autor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    area: Mapped[Area] = relationship()
    autor: Mapped[Usuario] = relationship()
    compromiso: Mapped["Compromiso | None"] = relationship()
    hallazgos: Mapped[list["Hallazgo"]] = relationship(back_populates="sesion")
    objeciones: Mapped[list["Objecion"]] = relationship(back_populates="sesion")


class Hallazgo(Base):
    __tablename__ = "hallazgos"
    id: Mapped[int] = mapped_column(primary_key=True)
    sesion_id: Mapped[int] = mapped_column(ForeignKey("sesiones_cliente.id"), index=True)
    cuadrante: Mapped[str] = mapped_column(String(20), index=True)
    tema: Mapped[str] = mapped_column(String(200))
    cita: Mapped[str] = mapped_column(Text)
    sentimiento: Mapped[str] = mapped_column(String(12), default="neutral")  # positivo | neutral | negativo
    severidad: Mapped[str] = mapped_column(String(10), default="media")
    tipo: Mapped[str] = mapped_column(String(20), default="queja")  # queja | necesidad | elogio | riesgo
    normativo: Mapped[bool] = mapped_column(Boolean, default=False)
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    sesion: Mapped[SesionCliente] = relationship(back_populates="hallazgos")


class Objecion(Base):
    __tablename__ = "objeciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    sesion_id: Mapped[int] = mapped_column(ForeignKey("sesiones_cliente.id"), index=True)
    tipo: Mapped[str] = mapped_column(String(20), index=True)
    cita: Mapped[str] = mapped_column(Text)
    manejo: Mapped[str] = mapped_column(String(20), default="")  # explorada | sin_explorar | no_manejada
    respuesta: Mapped[str] = mapped_column(Text, default="")
    avanzo: Mapped[bool] = mapped_column(Boolean, default=False)
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    sesion: Mapped[SesionCliente] = relationship(back_populates="objeciones")


# ---------------------------------------------------------------- círculo de innovación

class CirculoInnovacion(Base):
    __tablename__ = "circulos_innovacion"
    id: Mapped[int] = mapped_column(primary_key=True)
    mes: Mapped[str] = mapped_column(String(7))
    nombre: Mapped[str] = mapped_column(String(200))
    estado: Mapped[str] = mapped_column(String(20), default="preparacion")  # preparacion | votacion | decisiones | cerrado
    puntos_por_persona: Mapped[int] = mapped_column(Integer, default=5)
    max_por_iniciativa: Mapped[int] = mapped_column(Integer, default=3)
    analisis: Mapped[dict] = mapped_column(JSON, default=dict)
    creado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    iniciativas: Mapped[list["Iniciativa"]] = relationship(back_populates="circulo", order_by="Iniciativa.orden")


class Iniciativa(Base):
    __tablename__ = "iniciativas"
    id: Mapped[int] = mapped_column(primary_key=True)
    circulo_id: Mapped[int] = mapped_column(ForeignKey("circulos_innovacion.id"), index=True)
    orden: Mapped[int] = mapped_column(Integer, default=1)
    titulo: Mapped[str] = mapped_column(String(200))
    descripcion: Mapped[str] = mapped_column(Text, default="")
    impacto: Mapped[int] = mapped_column(Integer, default=3)
    esfuerzo: Mapped[int] = mapped_column(Integer, default=3)
    cuadrante: Mapped[str] = mapped_column(String(30), default="")
    mci_id: Mapped[int | None] = mapped_column(ForeignKey("mci.id"))
    fuentes: Mapped[dict] = mapped_column(JSON, default=dict)  # {"ideas":[ids], "hallazgos":[ids], "objeciones":[ids]}
    cita: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(String(20), default="")  # estrategia | proyecto | banco | descartar
    responsable_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    primera_revision: Mapped[date | None] = mapped_column(Date)
    estrategia_id: Mapped[int | None] = mapped_column(ForeignKey("estrategias.id"))

    circulo: Mapped[CirculoInnovacion] = relationship(back_populates="iniciativas")
    mci: Mapped[Mci | None] = relationship()
    responsable: Mapped[Usuario | None] = relationship()
    votos: Mapped[list["Voto"]] = relationship(back_populates="iniciativa")


class Voto(Base):
    __tablename__ = "votos"
    __table_args__ = (UniqueConstraint("iniciativa_id", "usuario_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    iniciativa_id: Mapped[int] = mapped_column(ForeignKey("iniciativas.id"))
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    puntos: Mapped[int] = mapped_column(Integer, default=0)
    creado: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    iniciativa: Mapped[Iniciativa] = relationship(back_populates="votos")
    usuario: Mapped[Usuario] = relationship()
