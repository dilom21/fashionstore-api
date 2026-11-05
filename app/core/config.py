from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.launch_guard import validar_arranque
from app.core.surface_guard import validar_coexistencia


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
    # 5C1: la superficie heredada deja de depender de SAAS_AUTH_ENABLED.
    #
    # Antes, activar el SaaS devolvia 503 a toda ruta del primer parcial. Ahora
    # las dos superficies se deciden por separado:
    #   * LEGACY_SURFACE_ENABLED=true (default) -> el contrato heredado responde.
    #   * SAAS_AUTH_ENABLED=true                -> la superficie tenant responde.
    # Si ambas estan activas en el mismo proceso, `surface_guard` exige
    # LEGACY_DATABASE_URL apuntando a un destino de datos distinto del tenant;
    # de lo contrario el arranque aborta (fail closed).
    legacy_surface_enabled: bool = True
    # DSN de la base legacy (single-tenant). Solo se usa cuando conviven las dos
    # superficies. Ningun servicio tenant lo consume.
    legacy_database_url: str | None = None
    # CU01/CU02: administración global independiente del JWT tenant.
    # Ambas opciones deben configurarse explícitamente para abrir el módulo.
    # La clave en texto plano nunca va en el repositorio ni en Settings.
    saas_superadmin_enabled: bool = False
    # Huella(s) SHA-256 de las claves de superadministrador. Admite DOS
    # formatos, ambos sin secretos en claro:
    #   * "<hex64>"                       -> identificador por defecto "v1";
    #   * "<kid>:<hex64>,<kid>:<hex64>"   -> rotación con dos claves vigentes.
    # Una clave se revoca quitándola de la lista y reiniciando el proceso; no
    # existe backdoor ni login público de recuperación.
    saas_superadmin_key_sha256: str | None = None
    # 5BO: fuerza bruta compartida entre procesos (contadores en PostgreSQL).
    saas_superadmin_rate_limit_ip: int = 10
    saas_superadmin_rate_limit_clave: int = 30
    saas_superadmin_rate_limit_ventana_segundos: int = 300
    # 5BO: en despliegue real exigir HTTPS. Solo se confía en
    # X-Forwarded-Proto cuando el cliente es un proxy listado; nunca en un
    # encabezado enviado por un cliente arbitrario.
    saas_superadmin_require_https: bool = False
    saas_superadmin_trusted_proxies: str | None = None
    # 5BP/B9: limite compartido del endpoint publico de aceptacion de
    # invitaciones. Los contadores viven en PostgreSQL (saas_admin), por lo que
    # el estado es comun a todos los procesos del despliegue. Se cuentan solo
    # los fallos reales de token (invalido/expirado/usado), no los errores de
    # politica de contrasena, para no bloquear al invitado legitimo.
    # Exige SAAS_ADMIN_DATABASE_URL: sin ese principal la ruta responde 503
    # (fail closed) en lugar de quedar sin proteccion.
    saas_invitation_rate_limit_enabled: bool = True
    saas_invitation_rate_limit_ip: int = 30
    saas_invitation_rate_limit_token: int = 10
    saas_invitation_rate_limit_ventana_segundos: int = 300
    # Solo se confia en X-Forwarded-For cuando el socket pertenece a un proxy
    # listado aqui. Un encabezado enviado por un cliente cualquiera se ignora.
    saas_invitation_trusted_proxies: str | None = None
    # CU16: habilitar solo tras validar SP definitivo, principal SQL y RLS.
    saas_reservas_enabled: bool = False
    # E1/5BM: venta presencial de contado tenantizada (OFF por defecto).
    saas_ventas_enabled: bool = False
    # E2/5BN: compras con recepcion de mercaderia (OFF por defecto).
    saas_compras_enabled: bool = False
    # E2/5BN: devoluciones de venta tenantizadas (OFF por defecto).
    saas_devoluciones_enabled: bool = False
    # E1/5BM: DSN del principal GLOBAL de aprovisionamiento (saas_admin_runtime).
    # Solo lo usa /saas/superadmin/*; nunca el JWT tenant ni los servicios
    # comerciales. Sin valor, CU01/CU02 responden 503 (fail closed).
    saas_admin_database_url: str | None = None
    # Solo tras auditar/instalar un verificador SECURITY DEFINER con dueño
    # NOLOGIN dedicado; sin valor, la escritura SaaS falla cerrada.
    saas_actor_sql_owner: str | None = None
    # Un SP SECURITY DEFINER comercial sólo es elegible si su propietario
    # NOLOGIN se identifica exactamente y sus ACL/search_path son seguros.
    # None bloquea por defecto el modo DEFINER; no instalar desde FastAPI.
    saas_sp_sql_owner: str | None = None
    # Etapa C (5BK): clave HMAC con la que FastAPI firma la asercion de tenant
    # que verifican las politicas RLS. Sin clave, el principal restringido no
    # resuelve empresa y toda consulta tenant queda denegada (fail closed).
    # Se aprovisiona por entorno; nunca se versiona ni se expone por HTTP.
    saas_tenant_assertion_key: str | None = None

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
    # 5BZ: la guardia de arranque vive aqui (y no en main.py) porque
    # `settings = get_settings()` se evalua al importar este modulo: la
    # validacion ocurre antes de que exista cualquier engine o conexion.
    # pydantic-settings prioriza el entorno sobre `.env`, de modo que un
    # DATABASE_URL heredado del proceso padre llegaria hasta aqui sin aviso.
    ajustes = Settings()
    validar_arranque(
        app_env=ajustes.app_env,
        database_url=ajustes.database_url,
        saas_admin_database_url=ajustes.saas_admin_database_url,
    )
    # 5C1: la combinacion legacy/SaaS se valida antes de construir engines.
    validar_coexistencia(
        saas_auth_enabled=ajustes.saas_auth_enabled,
        legacy_surface_enabled=ajustes.legacy_surface_enabled,
        database_url=ajustes.database_url,
        legacy_database_url=ajustes.legacy_database_url,
        app_env=ajustes.app_env,
    )
    return ajustes


settings = get_settings()