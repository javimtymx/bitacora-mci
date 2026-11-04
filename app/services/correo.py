"""Envío de correo por SMTP o por la API de Amazon SES (CORREO_PROVEEDOR=ses, con las llaves AWS del .env)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from html import escape

from ..config import get_settings

log = logging.getLogger("bitacora.correo")
PIE = "Mensaje generado por la Bitácora MCI. Responde a tu líder si algo no aplica."

_ses = None


def set_ses(cliente) -> None:
    """Para pruebas: reemplaza el cliente de SES."""
    global _ses
    _ses = cliente


def _cliente_ses():
    global _ses
    if _ses is None:
        import boto3
        s = get_settings()
        kw = {"region_name": s.ses_region or s.s3_region or "us-east-1"}
        if s.aws_access_key_id and s.aws_secret_access_key:
            kw.update(aws_access_key_id=s.aws_access_key_id, aws_secret_access_key=s.aws_secret_access_key)
        _ses = boto3.client("ses", **kw)
    return _ses


def _html(cuerpo: str, enlace: str | None, boton: str = "Abrir la bitácora", pie: str = PIE,
          titulo: str = "Bitácora MCI") -> str:
    partes, lista = [], False
    for linea in cuerpo.split("\n"):
        t = linea.strip()
        if t.startswith(("- ", "• ")):
            if not lista:
                partes.append("<ul style='margin:0 0 12px;padding-left:20px'>")
                lista = True
            partes.append(f"<li style='margin:0 0 6px'>{escape(t[2:])}</li>")
            continue
        if lista:
            partes.append("</ul>")
            lista = False
        if t:
            partes.append(f"<p style='margin:0 0 12px'>{escape(t)}</p>")
    if lista:
        partes.append("</ul>")
    btn = (f"<p style='margin:20px 0 0'><a href='{escape(enlace)}' style='background:#FFE14D;color:#15191E;"
           f"padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:700'>{escape(boton)}</a></p>"
           if enlace else "")
    return (f"<div style='font-family:Arial,sans-serif;font-size:15px;line-height:1.5;color:#15191E;max-width:600px'>"
            f"<div style='background:#053B3F;color:#fff;padding:12px 16px;border-radius:8px;font-weight:700'>"
            f"Guard<b style='color:#FFE14D'>IA</b> OneCore · {escape(titulo)}</div><div style='padding:16px 4px'>{''.join(partes)}{btn}</div>"
            f"<p style='color:#6B7580;font-size:12px'>{escape(pie)}</p></div>")


def enviar(destinatarios: list[str], asunto: str, cuerpo: str, enlace: str | None = None, *,
           boton: str = "Abrir la bitácora", pie: str = PIE, titulo: str = "Bitácora MCI") -> str:
    """Envía un correo. Devuelve 'enviado' o 'simulado'. Lanza excepción si falla."""
    s = get_settings()
    destinatarios = sorted({d.strip().lower() for d in destinatarios if d and d.strip()})
    if not destinatarios:
        raise ValueError("Sin destinatarios con correo")
    ses = (s.correo_proveedor or "").lower() == "ses"
    if s.smtp_dry_run or (not ses and not s.smtp_host):
        log.info("Correo simulado a %s: %s", destinatarios, asunto)
        return "simulado"
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = asunto
    msg.set_content(cuerpo + (f"\n\n{enlace}" if enlace else ""))
    msg.add_alternative(_html(cuerpo, enlace, boton, pie, titulo), subtype="html")
    if ses:
        _cliente_ses().send_raw_email(Source=s.smtp_from, Destinations=destinatarios,
                                      RawMessage={"Data": msg.as_bytes()})
        log.info("Correo enviado por SES a %s: %s", destinatarios, asunto)
        return "enviado"
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)
    return "enviado"
