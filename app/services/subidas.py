"""Archivos grandes: el navegador sube directo a S3 con una URL firmada y luego entrega un «token» al formulario.

Así ningún archivo pasa por el servidor ni por nginx: no hay límite práctico de tamaño (hasta 5 GB por archivo,
el máximo de S3 en una sola pieza). A partir de 50 MB se avisa al usuario que puede tardar.
El token (firmado y con caducidad) dice qué archivo subió quién y para qué; el servidor revisa en S3 que exista.
Si S3 no está configurado, los formularios mandan los archivos de la forma tradicional.
"""
from __future__ import annotations

from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import get_settings
from . import almacen

GRANDE = 50 * 1024 * 1024
CADUCIDAD = 24 * 3600

_IMG = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}


def _tipos(destino: str) -> dict[str, str]:
    if destino == "ticket":
        from .tickets import PERMITIDOS
        return PERMITIDOS
    if destino == "evidencia":
        from .evidencias import PERMITIDOS
        return PERMITIDOS
    if destino == "pago":
        return {**_IMG, "pdf": "application/pdf", "xml": "application/xml", "zip": "application/zip"}
    if destino == "transcripcion":
        return {"txt": "text/plain", "vtt": "text/vtt", "srt": "text/plain", "md": "text/markdown"}
    raise ValueError("Destino inválido.")


CARPETAS = {"ticket": "tickets", "evidencia": "evidencias", "pago": "pagos", "transcripcion": "transcripciones-origen"}


class SubidaInvalida(ValueError):
    pass


def _serial() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="subida-s3")


def disponible() -> bool:
    return almacen.s3_activo()


def firmar(uid: int, destino: str, nombre: str, tamano: int) -> dict:
    tipos = _tipos(destino)
    ext = Path(nombre or "").suffix.lower().lstrip(".")
    if ext not in tipos:
        raise SubidaInvalida(f"«{nombre}»: tipo de archivo no permitido aquí.")
    if tamano <= 0:
        raise SubidaInvalida(f"«{nombre}» está vacío.")
    if tamano > almacen.MAX_PUT:
        raise SubidaInvalida(f"«{nombre}» pasa de 5 GB, el máximo por archivo. Divídelo o comprímelo.")
    tipo = tipos[ext]
    llave = almacen.llave_nueva(CARPETAS[destino], nombre)
    token = _serial().dumps({"k": llave, "n": Path(nombre).name[:200], "t": tipo, "d": destino, "u": uid})
    return {"url": almacen.url_subida(llave, tipo), "token": token, "grande": tamano > GRANDE,
            "headers": {"Content-Type": tipo, "x-amz-server-side-encryption": "AES256"}}


def recibir(token: str, uid: int, destino: str) -> dict:
    """Valida el token y confirma en S3 que el archivo existe. Regresa {ruta, nombre, tipo, bytes}."""
    try:
        d = _serial().loads(token, max_age=CADUCIDAD)
    except SignatureExpired as e:
        raise SubidaInvalida("La subida caducó; vuelve a adjuntar el archivo.") from e
    except BadSignature as e:
        raise SubidaInvalida("Subida inválida.") from e
    if d.get("d") != destino or d.get("u") != uid:
        raise SubidaInvalida("Ese archivo no corresponde a este formulario.")
    ruta = almacen.PREFIJO_S3 + d["k"]
    try:
        n = almacen.tamano(ruta)
    except almacen.AlmacenError as e:
        raise SubidaInvalida(str(e)) from e
    return {"ruta": ruta, "nombre": d["n"], "tipo": d["t"], "bytes": n}
