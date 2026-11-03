-- Ejecutar como root de MySQL:  sudo mysql < deploy/mysql.sql
-- Cambia las contraseñas antes de correrlo y cópialas a /opt/bitacora-mci/.env

CREATE DATABASE IF NOT EXISTS bitacora_mci CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Usuario de la aplicación: solo su propia base.
CREATE USER IF NOT EXISTS 'bitacora_app'@'localhost' IDENTIFIED BY 'CAMBIA_ESTO_APP';
GRANT ALL PRIVILEGES ON bitacora_mci.* TO 'bitacora_app'@'localhost';

-- Usuario de solo lectura para la base de CFDI. Cambia `cfdi` por el nombre real de tu base.
CREATE USER IF NOT EXISTS 'cfdi_lectura'@'localhost' IDENTIFIED BY 'CAMBIA_ESTO_RO';
GRANT SELECT ON `cfdi`.* TO 'cfdi_lectura'@'localhost';

FLUSH PRIVILEGES;
