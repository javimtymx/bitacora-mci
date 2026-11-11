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


def _tabla_html(tabla: dict) -> str:
    """tabla = {"columnas": [...], "filas": [[celda, ...]], "enlace": índice de la columna con URL (se pinta como liga)}."""
    th = "".join(f"<th style='text-align:left;padding:6px 8px;border-bottom:2px solid #053B3F;font-size:13px'>{escape(c)}</th>"
                 for c in tabla["columnas"])
    filas = []
    for f in tabla["filas"]:
        celdas = []
        for i, c in enumerate(f):
            v = (f"<a href='{escape(str(c))}' style='color:#0F766E;font-weight:700'>Abrir</a>"
                 if i == tabla.get("enlace") else escape(str(c)))
            celdas.append(f"<td style='padding:6px 8px;border-bottom:1px solid #E6ECF0;font-size:13px;vertical-align:top'>{v}</td>")
        filas.append("<tr>" + "".join(celdas) + "</tr>")
    return (f"<table style='border-collapse:collapse;width:100%;margin:0 0 16px'><thead><tr>{th}</tr></thead>"
            f"<tbody>{''.join(filas)}</tbody></table>")


def _html(cuerpo: str, enlace: str | None, boton: str = "Abrir la bitácora", pie: str = PIE,
          titulo: str = "Bitácora MCI", tabla: dict | None = None) -> str:
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
    if tabla:
        partes.append(_tabla_html(tabla))
    btn = (f"<p style='margin:20px 0 0'><a href='{escape(enlace)}' style='background:#FFE14D;color:#15191E;"
           f"padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:700'>{escape(boton)}</a></p>"
           if enlace else "")
    return (f"<div style='font-family:Arial,sans-serif;font-size:15px;line-height:1.5;color:#15191E;max-width:600px'>"
            f"<div style='background:#053B3F;color:#fff;padding:12px 16px;border-radius:8px;font-weight:700'>"
            f"Guard<b style='color:#FFE14D'>IA</b> OneCore · {escape(titulo)}</div><div style='padding:16px 4px'>{''.join(partes)}{btn}</div>"
            f"<p style='color:#6B7580;font-size:12px'>{escape(pie)}</p></div>")


def enviar(destinatarios: list[str], asunto: str, cuerpo: str, enlace: str | None = None, *,
           boton: str = "Abrir la bitácora", pie: str = PIE, titulo: str = "Bitácora MCI", cc: list[str] | None = None,
           calendario: tuple[str, str] | None = None, tabla: dict | None = None) -> str:
    """Envía un correo. Devuelve 'enviado' o 'simulado'. Lanza excepción si falla.
    calendario=(método, ics): invitación iCalendar (REQUEST/CANCEL) que Google Calendar y Outlook ofrecen agendar."""
    s = get_settings()
    destinatarios = sorted({d.strip().lower() for d in destinatarios if d and d.strip()})
    cc = sorted({d.strip().lower() for d in cc or [] if d and d.strip()} - set(destinatarios))
    if not destinatarios:
        raise ValueError("Sin destinatarios con correo")
    ses = (s.correo_proveedor or "").lower() == "ses"
    if s.smtp_dry_run or (not ses and not s.smtp_host):
        log.info("Correo simulado a %s (cc %s): %s", destinatarios, cc, asunto)
        return "simulado"
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = ", ".join(destinatarios)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = asunto
    texto_tabla = ""
    if tabla:
        texto_tabla = "\n\n" + "\n".join(" | ".join(str(c) for c in f) for f in [tabla["columnas"]] + tabla["filas"])
    msg.set_content(cuerpo + texto_tabla + (f"\n\n{enlace}" if enlace else ""))
    msg.add_alternative(_html(cuerpo, enlace, boton, pie, titulo, tabla), subtype="html")
    if calendario:
        metodo, ics = calendario
        msg.add_alternative(ics, subtype="calendar", params={"method": metodo})
        msg.add_attachment(ics.encode(), maintype="text", subtype="calendar", filename="invitacion.ics", params={"method": metodo})
    if ses:
        _cliente_ses().send_raw_email(Source=s.smtp_from, Destinations=destinatarios + cc,
                                      RawMessage={"Data": msg.as_bytes()})
        log.info("Correo enviado por SES a %s: %s", destinatarios, asunto)
        return "enviado"
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)
    return "enviado"
