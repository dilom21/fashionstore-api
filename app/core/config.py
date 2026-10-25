from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "FashionStore API"
    app_env: str = "development"
    app_debug: bool = True

    database_url: str

    secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Segundo parcial: habilitar solo tras migracion SaaS y auditoria de seguridad.
    saas_auth_enabled: bool = False
    # CU16: habilitar solo tras validar SP definitivo, principal SQL y RLS.
    saas_reservas_enabled: bool = False
    # Solo tras auditar/instalar un verificador SECURITY DEFINER con dueño
    # NOLOGIN dedicado; sin valor, la escritura SaaS falla cerrada.
    saas_actor_sql_owner: str | None = None

    # CU22 - Pago electronico con Stripe (Test Mode).
    # Los secretos se leen del entorno (.env local, no versionado). Si no estan
    # configurados, el provider responde con un error de configuracion limpio.
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_currency: str = "bob"

    # Asistencia Inteligente (recomendaciones con IA).
    # Las claves viven UNICAMENTE en el backend (.env local, no versionado).
    # La API arranca aunque la IA no este configurada: solo el endpoint de
    # asistencia responde 503 cuando falta provider/model/api key.
    ai_provider: str = "openai"
    ai_model: str | None = None
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    ai_timeout_seconds: float = 20.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()