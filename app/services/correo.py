from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from html import escape

from ..config import get_settings

log = logging.getLogger("bitacora.correo")


def _html(cuerpo: str, enlace: str | None) -> str:
    parrafos = "".join(f"<p style='margin:0 0 12px'>{escape(p)}</p>" for p in cuerpo.split("\n") if p.strip())
    boton = (f"<p style='margin:20px 0 0'><a href='{escape(enlace)}' style='background:#FFE14D;color:#15191E;"
             f"padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:700'>Abrir la bitácora</a></p>"
             if enlace else "")
    return (f"<div style='font-family:Arial,sans-serif;font-size:15px;color:#15191E;max-width:600px'>"
            f"<div style='background:#053B3F;color:#fff;padding:12px 16px;border-radius:8px;font-weight:700'>"
            f"Guard<b style='color:#FFE14D'>IA</b> OneCore · Bitácora MCI</div><div style='padding:16px 4px'>{parrafos}{boton}</div>"
            f"<p style='color:#6B7580;font-size:12px'>Mensaje generado por la Bitácora MCI. Responde a tu líder si algo no aplica.</p></div>")


def enviar(destinatarios: list[str], asunto: str, cuerpo: str, enlace: str | None = None) -> str:
    """Envía un correo. Devuelve 'enviado' o 'simulado'. Lanza excepción si falla."""
    s = get_settings()
    destinatarios = [d for d in destinatarios if d]
    if not destinatarios:
        raise ValueError("Sin destinatarios con correo")
    if s.smtp_dry_run or not s.smtp_host:
        log.info("Correo simulado a %s: %s", destinatarios, asunto)
        return "simulado"
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = asunto
    msg.set_content(cuerpo + (f"\n\n{enlace}" if enlace else ""))
    msg.add_alternative(_html(cuerpo, enlace), subtype="html")
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)
    return "enviado"
