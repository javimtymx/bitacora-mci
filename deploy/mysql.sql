-- Base de la bitácora y usuarios en Amazon RDS (MySQL 8).
-- Ejecutar desde el servidor Lightsail con el usuario maestro de RDS:
--   mysql -h TU_ENDPOINT_RDS -u admin -p < deploy/mysql.sql
-- Antes: cambia las contraseñas y el nombre de la base de CFDI. Copia las contraseñas a /opt/bitacora-mci/.env
--
-- El host '%' permite conectarse desde cualquier IP; quien realmente limita el acceso es el
-- Security Group de RDS (puerto 3306 abierto solo a la IP estática de Lightsail).
-- Si prefieres amarrarlo también aquí, cambia '%' por la IP estática pública de Lightsail.

CREATE DATABASE IF NOT EXISTS bitacora_mci CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Usuario de la aplicación: solo su propia base.
CREATE USER IF NOT EXISTS 'bitacora_app'@'%' IDENTIFIED BY 'CAMBIA_ESTO_APP';
GRANT ALL PRIVILEGES ON bitacora_mci.* TO 'bitacora_app'@'%';

-- Usuario de solo lectura para la base de CFDI. Cambia `cfdi` por el nombre real de tu base.
CREATE USER IF NOT EXISTS 'cfdi_lectura'@'%' IDENTIFIED BY 'CAMBIA_ESTO_RO';
GRANT SELECT ON `cfdi`.* TO 'cfdi_lectura'@'%';
