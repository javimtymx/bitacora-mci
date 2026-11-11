"""Almacén de archivos: Amazon S3 en producción, disco solo como respaldo cuando S3 no está configurado.

Todo lo que sube la gente (evidencias de compromisos, transcripciones de sesiones y anexos de tickets) pasa por aquí,
para que el servidor no guarde archivos propios. En la base solo queda la «ruta»:
- "s3:<llave>"  → objeto en el bucket S3_BUCKET (cifrado en reposo con AES256 de S3).
- "<ruta>"      → archivo en ALMACEN_DIR/EVIDENCIAS_DIR (registros viejos o instalaciones sin S3).

Con S3 configurado, todo lo nuevo va a S3 aunque haya registros viejos en disco; `python -m app.cli almacen-migrar`
sube lo que quedó en disco y en la base.
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from ..config import get_settings

log = logging.getLogger("bitacora.almacen")
PREFIJO_S3 = "s3:"


class AlmacenError(RuntimeError):
    pass


_s3 = None


def set_s3(cliente) -> None:
    """Para pruebas: reemplaza el cliente de S3."""
    global _s3
    _s3 = cliente


def s3_activo() -> bool:
    return bool(get_settings().s3_bucket) or _s3 is not None


def _cliente_s3():
    global _s3
    if _s3 is None:
        s = get_settings()
        import boto3
        from botocore.config import Config
        kw = {"region_name": s.s3_region or None,
              "config": Config(retries={"max_attempts": 5, "mode": "standard"}, connect_timeout=10, read_timeout=60)}
        if s.aws_access_key_id and s.aws_secret_access_key:  # si no, boto3 usa el rol de la instancia o ~/.aws
            kw.update(aws_access_key_id=s.aws_access_key_id, aws_secret_access_key=s.aws_secret_access_key)
        _s3 = boto3.client("s3", **kw)
    return _s3


def _bucket() -> str:
    return get_settings().s3_bucket or "pruebas"


def _llave(carpeta: str, nombre: str) -> str:
    pref = (get_settings().s3_prefijo or "").strip("/")
    limpio = re.sub(r"[^A-Za-z0-9._-]", "_", Path(nombre or "archivo").name)[:120] or "archivo"
    partes = [p for p in (pref, carpeta.strip("/"), f"{uuid.uuid4().hex}_{limpio}") if p]
    return "/".join(partes)


def _dir() -> Path:
    return Path(get_settings().evidencias_dir)


def _local(ruta: str) -> Path:
    base = _dir().resolve()
    p = (base / ruta).resolve()
    if base not in p.parents:
        raise AlmacenError("Ruta inválida.")
    return p


def es_s3(ruta: str | None) -> bool:
    return bool(ruta) and ruta.startswith(PREFIJO_S3)


def guardar(carpeta: str, nombre: str, contenido: bytes, tipo: str = "application/octet-stream") -> str:
    """Guarda el archivo y regresa la ruta que se anota en la base."""
    if s3_activo():
        llave = _llave(carpeta, nombre)
        try:
            _cliente_s3().put_object(Bucket=_bucket(), Key=llave, Body=contenido, ContentType=tipo or "application/octet-stream",
                                     ServerSideEncryption="AES256")
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo subir a S3 %s: %s", llave, e)
            if "AccessDenied" in str(e) or "Forbidden" in str(e):
                raise AlmacenError("S3 negó el permiso para guardar el archivo. Avisa a sistemas: falta permiso en IAM "
                                   "para la carpeta de la Bitácora en el bucket.") from e
            raise AlmacenError("No se pudo guardar el archivo en el almacén (S3). Intenta de nuevo en un momento.") from e
        return PREFIJO_S3 + llave
    rel = f"{carpeta.strip('/')}/{uuid.uuid4().hex}_{re.sub(r'[^A-Za-z0-9._-]', '_', Path(nombre).name)[:120]}"
    destino = _local(rel)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(contenido)
    return rel


def leer(ruta: str) -> bytes:
    if es_s3(ruta):
        try:
            r = _cliente_s3().get_object(Bucket=_bucket(), Key=ruta[len(PREFIJO_S3):])
            return r["Body"].read()
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo leer de S3 %s: %s", ruta, e)
            raise AlmacenError("El archivo no está disponible en el almacén.") from e
    try:
        return _local(ruta).read_bytes()
    except OSError as e:
        raise AlmacenError("El archivo no está disponible en el servidor.") from e


def borrar(ruta: str) -> None:
    try:
        if es_s3(ruta):
            _cliente_s3().delete_object(Bucket=_bucket(), Key=ruta[len(PREFIJO_S3):])
        elif ruta:
            _local(ruta).unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001 - borrar nunca debe tumbar la operación del usuario
        log.warning("No se pudo borrar %s: %s", ruta, e)


def probar() -> str:
    """Sube, lee y borra un archivo de prueba. Para el comando almacen-probar."""
    if not s3_activo():
        return "S3 no está configurado (falta S3_BUCKET): los archivos se guardan en disco."
    ruta = guardar("_prueba", "prueba.txt", b"ok", "text/plain")
    assert leer(ruta) == b"ok"
    borrar(ruta)
    return f"S3 funciona: bucket {_bucket()} ({get_settings().s3_region or 'región por defecto'})."


# ---------------------------------------------------------------- subida directa del navegador a S3

MAX_PUT = 5 * 1024 ** 3  # límite de S3 para una subida en una sola pieza (5 GB)


def llave_nueva(carpeta: str, nombre: str) -> str:
    return _llave(carpeta, nombre)


def url_subida(llave: str, tipo: str, segundos: int = 6 * 3600) -> str:
    """URL firmada para que el navegador suba el archivo directo a S3 (sin pasar por el servidor)."""
    return _cliente_s3().generate_presigned_url(
        "put_object", Params={"Bucket": _bucket(), "Key": llave, "ContentType": tipo, "ServerSideEncryption": "AES256"},
        ExpiresIn=segundos)


def tamano(ruta: str) -> int:
    """Tamaño del objeto en S3; lanza AlmacenError si no existe (la subida no terminó)."""
    try:
        return int(_cliente_s3().head_object(Bucket=_bucket(), Key=ruta[len(PREFIJO_S3):])["ContentLength"])
    except Exception as e:  # noqa: BLE001
        raise AlmacenError("El archivo no terminó de subir. Intenta de nuevo.") from e


def url_descarga(ruta: str, nombre: str, tipo: str = "", segundos: int = 600, en_linea: bool = False) -> str | None:
    """URL firmada de descarga directa desde S3 (los archivos grandes no pasan por el servidor). None si no es S3."""
    if not es_s3(ruta):
        return None
    from urllib.parse import quote
    try:
        return _cliente_s3().generate_presigned_url(
            "get_object", Params={"Bucket": _bucket(), "Key": ruta[len(PREFIJO_S3):],
                                  "ResponseContentDisposition": f"{'inline' if en_linea else 'attachment'}; filename*=UTF-8''{quote(nombre)}",
                                  "ResponseContentType": tipo or "application/octet-stream"}, ExpiresIn=segundos)
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo firmar la descarga de %s: %s", ruta, e)
        return None
