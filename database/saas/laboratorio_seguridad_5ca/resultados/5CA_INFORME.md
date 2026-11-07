# Informe de validación de seguridad SaaS — laboratorio 5CA

Fecha: 2026-11-06 · PostgreSQL 17.9 local · Supabase remoto **no contactado**
· Base previa **no utilizada** · Repositorio original **sin modificar**

## 1. Arquitectura finalmente probada

Un único PostgreSQL 17 compartido, tres logins de servicio sin privilegios
administrativos y separación por `empresa_id` reforzada en el motor:

| Elemento | Cómo quedó probado |
|---|---|
| Identidad | JWT HS256 (mismas claims que emite FastAPI) colocado en `app.actor_token` con `set_config(..., true)`; `actor_claims()` lo re-verifica dentro de PostgreSQL con el secreto leído de Vault |
| Autorización | 110 políticas RLS, `FORCE ROW LEVEL SECURITY` en las 46 tablas, helpers `SECURITY DEFINER` con owners `NOLOGIN` |
| Pools | `fashionstore_5ca_api`, `fashionstore_5ca_bootstrap`, `fashionstore_5ca_platform` (LOGIN, `NOSUPERUSER`, `NOBYPASSRLS`, `INHERIT`) |
| Procedimiento privilegiado | `sp_crear_reserva_tenant()` `SECURITY DEFINER` con owner `fashionstore_sp_owner` y guardia `actor_coincide(empresa,usuario)` |
| Auditoría | `fn_registrar_bitacora()` `SECURITY DEFINER` con owner `fashionstore_audit_owner`; la API no tiene `INSERT` en `bitacora` |

Estructura resultante: 46 tablas, 1 vista, 145 FK validadas, 251 índices, 58
triggers, 46 con RLS y `FORCE`, 110 políticas y 14 funciones privadas —
idéntico a la etapa 2 de Codex (46/145/251/58).

## 2. Archivos nuevos (copia de trabajo; nada versionado se modificó)

`fashionstore-api/database/saas/laboratorio_seguridad_5ca/`

- `lab_common.py`, `lab_baseline.py`, `lab_install.py`, `lab_tests.py`,
  `lab_tests_fastapi.py`, `88_lab_validacion.py`, `90_lab_crear.py`,
  `99_lab_destruir.py`, `README.md`
- `fastapi_lab/database_lab.py`, `fastapi_lab/tenant_dependencies_lab.py`,
  `fastapi_lab/lab_app.py` (adaptación; el `app/` original no se editó)
- `sql/000_lab_vault_substituto.sql`, `sql/010_baseline_desde_catalogo.sql`,
  `sql/030_lab_fixtures.sql`, `sql/050a_lab.sql`, `sql/050b_lab.sql`,
  `sql/050c_lab.sql`, `sql/060_lab_correcciones.sql`, `sql/070_lab_sp_tenant.sql`
- `resultados/5ca_evidencia.json`, `resultados/5ca_resultados.md`,
  `resultados/5ca_salida_consola.log`

`git status` del repositorio muestra únicamente directorios **no rastreados**;
ningún archivo existente fue modificado y no hubo commit, push ni deploy.

## 3. Pruebas: 73 PASS / 0 FAIL

| Bloque | Códigos | Qué cubre |
|---|---|---|
| Estructura y roles | EST-01…05 | conteos, FORCE RLS, cero `SUPERUSER`/`BYPASSRLS`, login propio de la API |
| ACL | ACL-01…12 | API sin acceso a Vault, sin DDL, sin `bitacora` directa, sin procedimientos legacy, sin `SET ROLE`; bootstrap sólo por su función; platform_admin sin tablas comerciales |
| Identidad JWT | ID-01…16 | sin token, firma alterada, expirado, `alg=none`, sin `jti`, `scope` erróneo, empresa sin membresía, usuario inexistente, `iat` futuro, token de 9 KB, contexto incoherente, revocación de membresía y suspensión de suscripción en caliente, rotación de clave con `previous` |
| Aislamiento | AIS-01…20 | lecturas y escrituras cruzadas entre empresas, `empresa_id` inmutable, aislamiento entre clientes del mismo tenant, tablas de personal invisibles al cliente, administrador limitado a su empresa |
| Pool | POOL-01…03 | una sola conexión física sirviendo A1→B1→A3 y 40 reutilizaciones sin fugas, 3 hilos concurrentes sin intercambio de identidad, y demostración de que `set_config(..., false)` sí persiste (riesgo evitado por el parche) |
| SP y stock | SP-01…08 | reserva real con efecto en stock/movimiento/carrito, suplantación de usuario y de empresa rechazadas, sin DML directo sobre inventario/ventas, `platform` sin `EXECUTE`, carrera por la última unidad (1 éxito / 1 rechazo `23514`, stock coherente) |
| FastAPI real | FAPI-01…09 | login bootstrap+Argon2, `/lab/me`, RLS sobre el pool compartido, 401/403 correctos, token ajeno, autorización por rol de empresa y reserva por HTTP |

Evidencia completa por prueba en `resultados/5ca_resultados.md`.

## 4. Errores encontrados y corregidos

Los seis primeros son **defectos de la propuesta**, no del laboratorio; se
corrigieron en `sql/060_lab_correcciones.sql` y `sql/070_lab_sp_tenant.sql`
para poder ejecutar las pruebas. La propuesta original sigue igual.

| # | Severidad | Hallazgo | Corrección aplicada en el laboratorio |
|---|---|---|---|
| 1 | Alta (rompe todo) | `050c` exige `actor_coincide(p_empresa, p_usuario)` en cada SP y `050a` **nunca la crea** | función creada con owner `context_owner` y `EXECUTE` para `api` y `sp_owner` |
| 2 | **Bloqueante** | `050b` define `p_api_usuario_select`, pero `050c` no concede `SELECT` sobre `public.usuario` a `fashionstore_api`: la política es inaplicable y el login/membresía falla con *permission denied for table usuario* | `GRANT SELECT ON public.usuario TO fashionstore_api` |
| 3 | **Bloqueante** | `actor_claims()` es `SECURITY DEFINER` con owner `context_owner`, que no tiene `USAGE` sobre `extensions`; la verificación JWT muere con *permission denied for schema extensions* | `GRANT USAGE ON SCHEMA extensions` + `EXECUTE` de `extensions.hmac(bytea,bytea,text)` |
| 4 | **Bloqueante** | `fn_registrar_bitacora()` de `050c` pertenece a `fashionstore_audit_owner`, que no tiene `USAGE` sobre `fashionstore_private`: **toda escritura de la API** falla al dispararse el trigger | `GRANT USAGE ON SCHEMA fashionstore_private TO fashionstore_audit_owner` (+ `INSERT`/secuencia en `bitacora`) |
| 5 | Alta | El borrador del SP usa `FOR SHARE OF ue, ss`; PostgreSQL exige privilegio `UPDATE` para bloquear filas, así que el SP fallaba con *permission denied for table usuario_empresa* y forzaba a dar `UPDATE` sobre la membresía al owner del SP | se eliminó el bloqueo de fila y se conservó la guardia de identidad firmada |
| 6 | Media | `050c` no concede al owner del SP `UPDATE` sobre `carrito` ni `SELECT` sobre `movimiento_inventario`, necesarios para cerrar el carrito y auditar el movimiento | `GRANT` acotados a `fashionstore_sp_owner` |

Hallazgos del parche de aplicación (no de la propuesta SQL):

| # | Hallazgo | Consecuencia |
|---|---|---|
| 7 | Con token sin membresía vigente, `actor_claims()` aborta la consulta con `42501` en vez de devolver 0 filas | el `tenant_dependencies` original lo convertiría en **503** (esquema no disponible); el parche lo mapea a **403** |
| 8 | `set_config('app.actor_token', …, false)` deja el token en la sesión tras el commit (demostrado en POOL-03) | es obligatorio el tercer argumento `true` en cada `after_begin` |

Fallos propios del arnés, corregidos y reejecutados: fixtures sin `empresa_id`,
secuencias de identidad sin adelantar tras insertar IDs explícitos, trigger
`fn_proteger_detalle_venta` bloqueando líneas de una venta ya cerrada,
`SAVEPOINT` implícito de psycopg que falseaba la comprobación de residuo de GUC
y una aserción que pedía al cliente ver `movimiento_inventario` (tabla de
personal). Ninguno de esos fallos se ocultó: cada uno se corrigió y el ciclo se
repitió hasta 73/73.

## 5. Limitaciones de compatibilidad con Supabase

1. **Vault no probado de verdad.** Se usó un sustituto local que imita
   `vault.decrypted_secrets` con el secreto en claro. No valida cifrado, KMS,
   rotación desde Dashboard ni las ACL reales de Supabase. El hallazgo 3
   (USAGE de `extensions`) demuestra que las diferencias de privilegios del
   entorno administrado sí importan.
2. **La vista de Vault debe ser de derechos del propietario.** Con
   `security_invoker=true` la verificación falla; hay que confirmar cómo está
   definida `vault.decrypted_secrets` en el proyecto real antes de promover.
3. **Baseline reconstruido desde el catálogo**, no restaurado del respaldo
   binario (no disponible en este equipo). Los conteos coinciden con la etapa 2
   (46/145/251/58), pero no se reprodujeron los 806 registros históricos.
4. **Supabase impone roles y `pgrst`** (`anon`, `authenticated`,
   `service_role`): las propuestas los revocan si existen, pero el
   comportamiento del pooler (Supavisor) y de `service_role` frente a
   `FORCE RLS` no se puede probar en un clúster local.
5. **Sin TLS ni `verify-full`**: el clúster usa `ssl=off` y `sslmode=disable`.
6. **Los advisors de seguridad/rendimiento de Supabase** quedan pendientes.
7. **Un solo motor de pruebas**: 2 empresas y 5 usuarios sintéticos; no
   sustituye una prueba de carga ni la matriz de permisos por rol de negocio.

## 6. Cambios pendientes en el FastAPI original

Ninguno se aplicó (restricción del encargo). El parche mínimo validado en
`fastapi_lab/` es:

1. `app/core/database.py`: añadir en `after_begin`
   `set_config('app.actor_token', :token, true)` al lado de `app.ip`;
   `Session.info['actor_token']` se llena tras validar el JWT.
2. Nuevo `get_current_tenant_context`: publicar el token **antes** de consultar
   la membresía (hoy se consulta con `app.empresa_id` como si autorizara).
3. Mapear `sqlstate 42501` a **403** en `tenant_dependencies` (hoy → 503).
4. Pool `bootstrap` dedicado que llame a
   `fashionstore_private.bootstrap_credencial(:correo)` en lugar de
   `SELECT` sobre `public.usuario`.
5. Pool `platform_admin` dedicado para `/saas/superadmin` (hoy reutiliza
   `get_db`).
6. Dejar de insertar `bitacora` desde `tenant_write_repository` (la API ya no
   tiene `INSERT`); el trigger lo cubre.
7. Sustituir en `tenant_reserva_transaccional_5al.py` la comprobación de
   `session_user` por `actor_empresa_id()`/`actor_usuario_id()` verificados.
8. Mantener `SAAS_AUTH_ENABLED`, `SAAS_SUPERADMIN_ENABLED` y
   `SAAS_RESERVAS_ENABLED` en `false` hasta cerrar los puntos anteriores.
9. Añadir a `050a`/`050c` los seis huecos del apartado 4 **antes** de generar
   las migraciones definitivas con `supabase migration new`.
10. Verificar en el proyecto real: definición de `vault.decrypted_secrets`,
    privilegios de `extensions` y comportamiento del pooler.

## 7. Dictamen

**NO LISTO todavía para preparar el despliegue a Supabase**, con avance
sustancial: la arquitectura es viable y quedó demostrada (73/73) — el JWT
firmado como raíz de identidad, el RLS por actor, la separación de pools, la
ACL mínima, la guardia de los SP y la concurrencia de stock funcionan sin fugas
de identidad ni cruces entre empresas.

Bloquean el despliegue: (a) los seis defectos de las propuestas `050a-c`, que
deben incorporarse como migraciones nuevas revisadas; (b) la verificación real
de Vault y de los privilegios de `extensions` en el proyecto de Supabase; y
(c) los diez cambios de FastAPI, que hoy siguen sin aplicarse. Una vez
incorporados (a) y (c) y confirmado (b), el conjunto pasa a **LISTO** para
ensayo en un Supabase aislado con advisors y restauración real.
