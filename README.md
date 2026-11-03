# Bitácora MCI · OneCore

Plataforma de ejecución semanal 4DX con un PM virtual (IA). El equipo captura compromisos, remediaciones e ideas. La IA revisa cada registro antes de guardarlo, lo compara con el histórico y programa el seguimiento: recordatorios, avisos y documentos.

**Stack:** FastAPI, Jinja2, SQLAlchemy y MySQL. La IA corre con la API de Anthropic. Tareas programadas con systemd. Va detrás de nginx en AWS Lightsail.

## Qué hace

| Módulo | Para qué |
|---|---|
| **Compromisos** | Captura semanal ligada a una medida predictiva (y, opcional, a una estrategia). La IA revisa que sea verificable, que mueva la medida y que no repita lo que ya falló. Si guardas sin su sugerencia, tienes que explicar el motivo. |
| **Remediaciones** | Cuando un compromiso no se cumple, la IA clasifica la causa y la compara con las remediaciones cerradas del área. Te dice qué ha funcionado y propone hasta 3 actividades: recordatorio, correo al líder o a Dirección, o un informe que el sistema redacta. |
| **Ideas** | Calificación de impacto/esfuerzo, cuadrante, fortaleza, detección de duplicados y preguntas para fortalecerlas. |
| **Estrategias (palancas)** | Catálogo con bitácora de eventos: alta, activación, pausa, descarte o éxito, siempre con motivo y evidencia. La IA evalúa con el histórico si la estrategia funciona (mantener, ajustar, pausar, escalar o declararla exitosa). |
| **PM virtual** | Informe diario, alertas por persona y área, y una bandeja de acciones para aprobar, editar o descartar. También redacta documentos a pedido. |
| **Excepciones** | Excluyen un incumplimiento justificado o ajustan una meta sin borrar el dato. Las solicita un líder y las resuelve Dirección. |
| **Parámetros** | Umbrales del semáforo, días de alerta y automatizaciones. Cada cambio queda en el historial con su motivo. |
| **Catálogo estratégico** | MCI y medidas predictivas **versionadas**. Un cambio de meta crea una versión nueva con vigencia, y cada compromiso guarda la versión con la que se midió. |
| **Indicadores** | Ventas desde CFDI: montos solo para quien tiene permiso confidencial; los demás ven la variación %. También cumplimiento por área y aceptación de la IA. |
| **Semanal** | Presentación de la junta por área: cumplidos y no cumplidos, con causa, remediación y nueva fecha. Se imprime o se guarda en PDF. |
| **Reportes** | Excel de compromisos, participación, ideas y ventas por cliente. Este último es confidencial y cada descarga queda auditada. |
| **Socio** | Corte ejecutivo consolidado por área, sin personas ni compromisos individuales. Las excepciones marcadas como visibles aparecen etiquetadas, y Dirección escribe la narrativa. Al publicarse, el corte queda congelado. |
| **Usuarios** | Alta con invitación y baja lógica: se reasignan sus compromisos abiertos y se conserva el histórico. Permisos: colaborador, líder, Dirección o admin, más el permiso de datos confidenciales. |

## Principios de arquitectura

- **Nada se edita en sitio cuando importa para la historia.** Las metas tienen versiones, las estrategias tienen eventos, las excepciones y los parámetros tienen historial, y todo pasa por la tabla `auditoria`.
- **Cada revisión de la IA queda registrada** en `revisiones_ia`: resultado, modelo, tokens y la decisión humana (aceptada o no, con motivo). De ahí sale el indicador de aceptación de IA y la calibración del PM virtual.
- **Las acciones automáticas tienen ciclo de aprobación** (`propuesta → aprobada → ejecutada`). Solo las de bajo riesgo nacen aprobadas, y únicamente si su parámetro está activo.
- **Los datos de los usuarios van cercados** en `<datos>` dentro del prompt, y la IA responde solo JSON validado.
- **CFDI es solo lectura**, con un usuario MySQL que tiene permiso `SELECT` únicamente.

## Despliegue en Lightsail

Requisitos: Ubuntu 22.04+ con nginx, MySQL 8 en Amazon RDS accesible desde la IP estática de Lightsail (Security Group en 3306), un dominio apuntando a esa IP y los puertos 80/443 abiertos en el firewall de Lightsail. La app escucha en 127.0.0.1:8020 (8010–8012 ya los usan otras apps del servidor).

```bash
# 1. Código
sudo apt-get install -y git
git clone https://github.com/<org>/bitacora-mci.git ~/bitacora-mci
cd ~/bitacora-mci

# 2. Base de datos: edita contraseñas y el nombre real de tu base de CFDI
nano deploy/mysql.sql
mysql -h TU_ENDPOINT_RDS -u admin -p < deploy/mysql.sql

# 3. Instalación (crea /opt/bitacora-mci, venv, servicios y nginx)
sudo bash deploy/setup.sh            # la primera vez crea .env y se detiene
sudo nano /opt/bitacora-mci/.env     # DATABASE_URL, CFDI_*, SECRET_KEY, ANTHROPIC_API_KEY, SMTP_*
sudo bash deploy/setup.sh            # ahora sí migra y arranca

# 4. Datos iniciales y tu usuario
cd /opt/bitacora-mci
sudo -u bitacora bash -c 'set -a; . ./.env; set +a; .venv/bin/python -m app.cli crear-admin --email jmartinez@onecore.mx --nombre "Javier Martínez"'
sudo -u bitacora bash -c 'set -a; . ./.env; set +a; .venv/bin/python -m app.cli seed'

# 5. HTTPS
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d bitacora.onecore.mx
```

Para actualizar después de un `git pull`, vuelve a correr `sudo bash deploy/setup.sh`. Aplica las migraciones y reinicia.

### Consulta de CFDI

`CFDI_VENTAS_SQL` debe devolver estas columnas con estos alias: `fecha, uuid, rfc_receptor, nombre_receptor, subtotal, moneda, tipo_cambio`, filtradas con `:desde` y `:hasta`. Usa solo comprobantes de **ingreso** vigentes emitidos por OneCore. Si guardas los XML crudos, conviene una vista que extraiga esos campos.

Para probarla:

```bash
sudo -u bitacora bash -c 'set -a; . ./.env; set +a; .venv/bin/python -m app.jobs sync-cfdi'
```

### Tareas programadas (systemd timers)

| Timer | Cuándo | Qué hace |
|---|---|---|
| `bitacora-sync-cfdi` | diario 6:00 | Agrega ventas por mes y por cliente |
| `bitacora-informe-diario` | L-V 7:30 | Informe del PM virtual |
| `bitacora-recordatorios` | L-V 8:00 | Remediaciones por vencer, personas sin compromiso, seguimiento de estrategias (lunes) |
| `bitacora-ejecutar-acciones` | cada 15 min | Envía correos y redacta informes aprobados |

Para revisar: `systemctl list-timers 'bitacora-*'` y `journalctl -u 'bitacora-job@*' -n 50`.

### Respaldos

Activa los snapshots automáticos de Lightsail y, además, un `mysqldump bitacora_mci` diario a S3.

## Desarrollo local

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # para pruebas locales: DATABASE_URL=sqlite:///./bitacora.sqlite3 y COOKIE_SECURE=false
python -m app.cli init-db && python -m app.cli crear-admin --email yo@onecore.mx --nombre "Yo" && python -m app.cli seed
uvicorn app.main:app --reload
pytest -q
```

Sin `ANTHROPIC_API_KEY`, la app funciona y permite guardar sin revisión. Con `SMTP_DRY_RUN=true`, los correos se registran pero no se envían.

## Siguientes pasos

- Conectar fuentes automáticas de medidas predictivas: HubSpot, SAFE y uso de plataforma.
- Servidor MCP o API para que otros agentes consulten y registren en la bitácora.
- Inicio de sesión con Microsoft 365.
