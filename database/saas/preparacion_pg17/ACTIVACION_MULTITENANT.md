# Activacion multitenant controlada

Estado: **EJECUTADA** en Supabase el 2026-11-06 (21:22-21:30 hora local).
Checkpoint final: `S4_055_COMMIT`. Ver "Resultado de la ejecucion" al final y el
acta archivada junto al respaldo. La API debe permanecer detenida durante toda la
ventana. No continuar despues de ningun error.

## Prerrequisitos

1. Nuevo `pg_dump -Fc --schema=public`, hash, inventario y lectura integral.
2. Vault debe contener exactamente una clave activa llamada
   `fashionstore_tenant_jwt_hs256_active` y, opcionalmente, una anterior.
3. Preparar tres contrasenas aleatorias independientes en un gestor seguro y
   los DSN de API, bootstrap y platform; no pegarlos en SQL versionado.
4. Preparar una clave superadmin aleatoria y guardar en el despliegue solamente
   su SHA-256. La clave en claro permanece en el gestor seguro.
5. Regenerar `despliegue/` y verificar `MANIFIESTO.sha256`.
6. Ejecutar `002_checkpoint_recuperacion_seguridad.sql`; debe devolver
   `S0_PRE_050A`. Archivar su salida junto al respaldo y el manifiesto.

## Orden SQL

Una sola sesion administrativa, conexion directa, `ON_ERROR_STOP=1`:

1. Preflight de 050a: Vault, `extensions.hmac`, 46 tablas, una sola empresa y
   presencia completa de 045.
2. `despliegue/050a_identidad_jwt_roles.sql`.
3. `despliegue/050b_rls_policies.sql`.
4. `despliegue/050c_acl_procedimientos.sql`.
5. Verificar 46/46 `FORCE RLS`, policies, siete roles sin `BYPASSRLS` y ACL.
6. `despliegue/055_retirar_compatibilidad_legacy.sql`.
7. Crear los tres LOGIN sin privilegios propios, miembros exclusivamente de
   `fashionstore_api`, `fashionstore_bootstrap` y
   `fashionstore_platform_admin`. Asignar sus contrasenas con `psql \password`
   para no incluirlas en comandos, archivos ni historial.
8. Probar cada login: `NOSUPERUSER`, `NOBYPASSRLS`, `NOCREATEDB`,
   `NOCREATEROLE`; sin token, el login API debe fallar cerrado.

Despues de cada COMMIT ejecutar y archivar
`002_checkpoint_recuperacion_seguridad.sql`. Estados esperados: `S1_050A_COMMIT`,
`S2_050B_COMMIT`, `S3_050C_COMMIT` y `S4_055_COMMIT`.

No crear una segunda empresa antes de completar el paso 8. `055` solo se ejecuta
despues de 050a-050c: mientras tanto 045 mantiene el modo monocompania.

## Configuracion central del nuevo despliegue

Configurar exclusivamente en su gestor de secretos:

- `DATABASE_URL`: login miembro de `fashionstore_api`.
- `DATABASE_BOOTSTRAP_URL`: login miembro de `fashionstore_bootstrap`.
- `DATABASE_PLATFORM_URL`: login miembro de `fashionstore_platform_admin`.
- `SECRET_KEY`: mismo valor que la clave activa de Vault.
- `SAAS_AUTH_ENABLED=true`.
- `SAAS_SUPERADMIN_ENABLED=true` y `SAAS_SUPERADMIN_KEY_SHA256` configurado.
- `SAAS_RESERVAS_ENABLED=false`.
- `SAAS_BITACORA_POR_TRIGGER=true`.
- `SAAS_ACTOR_SQL_OWNER=fashionstore_context_owner`.
- `SAAS_SP_SQL_OWNER=fashionstore_sp_owner`.

No iniciar con `postgres`, `service_role` ni Transaction Pooler. El login legacy
y todas las rutas no incluidas en la allowlist responden 503 con SaaS activo.

## Smoke antes de abrir trafico

1. Crear empresa + suscripcion mediante el endpoint superadmin y luego su primer
   administrador; ambas respuestas deben omitir contrasena/hash.
2. Login por `POST /auth/tenant/login` con slug y contexto `personal`.
3. Confirmar `/auth/tenant/me` y catalogo vacio de la empresa nueva.
4. Confirmar que el token nuevo no ve filas de Vanter Men y que un token Vanter
   no ve la empresa nueva.
5. Confirmar 503 en login y rutas legacy; CU comerciales no adaptados siguen
   cerrados y reservas permanecen bajo su segundo flag.

## Parada y recuperacion

- Cada 050a/050b/050c/055 usa `BEGIN`/`COMMIT` propio. Un error antes de COMMIT
  revierte todo el archivo. No ejecutar el siguiente.
- Tras error, timeout o perdida de conexion, ejecutar solamente el checkpoint:
  - `S0_PRE_050A`: 050a no confirmo; puede reintentarse 050a tras corregir la
    causa revisada.
  - `S1_050A_COMMIT`: conservar 050a y reanudar en 050b.
  - `S2_050B_COMMIT`: conservar RLS; reanudar en 050c.
  - `S3_050C_COMMIT`: conservar ACL/RLS; reanudar en 055.
  - `S4_055_COMMIT`: no repetir SQL; continuar con logins.
  - `INCONSISTENTE_DETENER`: no reintentar ni ejecutar rollback automatico.
- En S0-S3, 045 sigue instalado y bloquea empresas adicionales; FastAPI sigue
  detenido. En S2-S4, FORCE RLS impide trafico ordinario sin identidad valida.
- La recuperacion preferida es completar hacia adelante desde el checkpoint,
  porque evita tocar roles internos de Supabase y conserva objetos ya validados.
- Si se decide abandonar definitivamente la activacion, no improvisar `DROP
  ROLE`, `DROP OWNED` ni restaurar `public` en caliente. Mantener FastAPI detenido
  y preparar una ventana separada y autorizada: restaurar el respaldo de
  `public`, comprobar ownership/ACL, y retirar exclusivamente
  `fashionstore_private` y los siete roles `fashionstore_*` creados por 050a.
  El alcance y orden de esa retirada deben revisarse contra el checkpoint real.
- Error en logins o smoke: mantener el servicio detenido; no registrar empresas.

## Resultado de la ejecucion (2026-11-06)

Sesion: conexion directa como `postgres` (PG 17.6, `CREATEROLE` y `BYPASSRLS`,
`NOSUPERUSER`); `current_user = session_user = postgres`. Copias de `despliegue/`
verificadas contra `MANIFIESTO.sha256` y regeneradas sin cambios.

| Paso | Archivo | Resultado | Checkpoint |
|---|---|---|---|
| 1 | `050a_identidad_jwt_roles.sql` | OK | `S1_050A_COMMIT` |
| 2 | `050b_rls_policies.sql` | OK | `S2_050B_COMMIT` |
| 3 | `050c_acl_procedimientos.sql` | OK | `S3_050C_COMMIT` |
| 4 | `055_retirar_compatibilidad_legacy.sql` | OK | `S4_055_COMMIT` |

Verificado despues de 055: 7 roles sin SUPERUSER/BYPASSRLS, 46/46 tablas con
FORCE RLS, 111 politicas dirigidas solo a roles FashionStore, `anon` y
`authenticated` sin privilegios en `public`, 0 triggers y 0 funciones de 045,
0 `DEFAULT` en `empresa_id`, y Vanter Men intacto (1 empresa, 9 usuarios,
14 productos, 120 variantes, 265 inventarios, 40 ventas, 30 pagos, 806 bitacoras).

Logins creados sin contrasena (no autentican hasta fijarla con `\password`):
`fashionstore_api_login` (limite 20), `fashionstore_bootstrap_login` (10) y
`fashionstore_platform_login` (5); cada uno miembro solo de su rol de grupo y de
ningun rol `*_owner`. Evidencia en
`C:\Users\Harold\Documents\FashionStore_Backups\20261106_2130_activacion_seguridad\`.

Pendiente manual: contrasenas de los tres logins, los tres DSN, `SECRET_KEY`
igual al secreto activo de Vault, flags `SAAS_*` y el smoke de empresa nueva.

## Smoke local posterior (2026-11-06)

Con los tres logins, los DSN directos (`sslmode=require`) y los flags `SAAS_*`
configurados, el smoke en FastAPI local (empresa temporal `smoke-b26026`, id=3,
desactivada al finalizar, sin borrar filas) expuso dos defectos ajenos a las
migraciones aplicadas:

1. `050a` no concedia a `fashionstore_context_owner` el `USAGE` del esquema
   `vault` ni el `EXECUTE` de `vault._crypto_aead_det_decrypt(bytea,bytea,
   bigint,bytea,bytea)`. `actor_claims()` no podia materializar
   `fashionstore_private.jwt_keys` y toda peticion tenant abortaba con `42501`
   (el login respondia 401). Se corrigio con dos `GRANT` aditivos, sin conceder
   `SELECT` sobre `vault.decrypted_secrets`; ya estan incorporados a `050a` y a
   `despliegue/` (ver «Pendientes»).
2. `registrar_primer_administrador` insertaba `empleado` antes de
   `usuario_empresa` y violaba la FK compuesta `fk_tenant_empleado_usuario`
   (23503 -> 409 "Conflicto"). El orden ya es `sucursal -> usuario ->
   usuario_empresa -> empleado` en `saas_initial_admin_repository.py`.

## Pendientes

Sin pendientes abiertos: el único ítem de esta etapa quedó cerrado en esta
ventana (2026-11-06).

- [x] **Incorporados a `despliegue/050a_identidad_jwt_roles.sql` y a su fuente
      `050a_identidad_jwt_roles_PROPUESTA_NO_EJECUTAR.sql` los dos `GRANT` de
      Vault**, que ya estaban aplicados en la instancia de Supabase:
      `GRANT USAGE ON SCHEMA vault TO fashionstore_context_owner` y `GRANT
      EXECUTE ON FUNCTION vault._crypto_aead_det_decrypt(bytea,bytea,bigint,
      bytea,bytea) TO fashionstore_context_owner`. Van con el resto de privilegios
      de `actor_claims()`, después de crear el rol, antes de crear la vista
      `jwt_keys` y fuera del alcance de las transferencias de ownership. No se
      concede `SELECT` sobre `vault.secrets` ni `vault.decrypted_secrets` y nada
      se concede a `PUBLIC`. Copia y `MANIFIESTO.sha256` regenerados con
      `generar_despliegue.py` (050b/050c/055 sin cambios); `050a` queda con
      sha256 de copia `a238126905b3a61e…` y de original `1c08b0e0e06533c7…`.
      Verificado el 2026-11-06 sin ejecutar SQL (estructura, correspondencia
      fuente<->copia y manifiesto): 34 comprobaciones OK.

