from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./bitacora.sqlite3"
    cfdi_database_url: str = ""
    cfdi_ventas_sql: str = ""
    cfdi_clientes_sql: str = ""

    secret_key: str = "dev-inseguro-cambia-esto"
    base_url: str = "http://localhost:8000"
    cookie_secure: bool = False

    anthropic_api_key: str = ""
    ia_modelo: str = "claude-sonnet-5-5"
    ia_modelo_rapido: str = "claude-haiku-5-5"
    ia_busqueda_web: bool = True
    ia_herramienta_busqueda: str = "web_search_20250305"

    # Clave Fernet para cifrar transcripciones (python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
    evidencias_dir: str = "/var/lib/bitacora-mci/evidencias"
    transcripciones_clave: str = ""

    # Almacén de archivos (evidencias, transcripciones y anexos de tickets). Sin S3_BUCKET se usa EVIDENCIAS_DIR.
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_prefijo: str = "bitacora-mci"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "GuardIA OneCore <pm@onecore.mx>"
    smtp_dry_run: bool = True

    zona_horaria: str = "America/Mexico_City"


@lru_cache
def get_settings() -> Settings:
    return Settings()
