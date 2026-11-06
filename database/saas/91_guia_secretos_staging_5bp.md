# GUÍA DE SECRETOS Y SEPARACIÓN DE PRINCIPALES — 5BP / Etapa G

**Versión:** `5BP-G.1`
**Alcance:** aprovisionamiento futuro de staging y producción del backend SaaS.
**Regla de oro:** este documento **no contiene secretos**, ni valores de ejemplo
reutilizables, ni rutas a archivos con secretos. Solo describe qué se necesita,
quién lo custodia, cómo se rota y cómo falla el sistema si falta.

---

## 1. Inventario de secretos

| Secreto | Variable | Consumidor | Si falta |
|---|---|---|---|
| Clave de firma JWT | `SECRET_KEY` | FastAPI (login tenant y legacy) | Firma inválida / error de arranque |
| Clave HMAC de aserción de tenant | `SAAS_TENANT_ASSERTION_KEY` | FastAPI firma, PostgreSQL verifica (`saas.rls_secret`) | **Fail closed**: RLS no resuelve tenant, toda consulta tenant denegada |
| Huellas SHA-256 del superadmin | `SAAS_SUPERADMIN_KEY_SHA256` | `app/core/saas_superadmin_auth.py` | **503** en `/saas/superadmin/*` |
| DSN del principal global | `SAAS_ADMIN_DATABASE_URL` | `app/core/saas_admin_db.py`, B9 | **503** en CU01/CU02 y en aceptación de invitaciones |
| Password `fs_saas_runtime` | dentro de `DATABASE_URL` | Runtime tenant | Sin conexión |
| Password `saas_admin_runtime` | dentro de `SAAS_ADMIN_DATABASE_URL` | Plano global | Sin conexión global |
| Contraseña del propietario del esquema | gestor de BD | Migraciones / DBA | Migración imposible |
| Tokens de invitación | no se almacenan | — | El token en claro vive **solo** en la respuesta HTTP al administrador; en base queda `sha256(token)` |

Claves que **no** son secretos y pueden versionarse: nombres de rol
(`fs_saas_runtime`, `saas_admin_runtime`, `saas_admin_owner`, `saas_personal_owner`,
`saas_rls_owner`, `saas_actor_sql_owner`), rutas, límites numéricos de rate limit y
la versión del manifiesto.

---

## 2. Qué nunca debe ocurrir

- Un secreto en Git, en un `README`, en un issue, en un chat, en una captura o en
  un archivo de evidencia (`database/saas/evidencias/*`).
- Un secreto en la línea de comandos (`--password`, DSN completo en el historial
  de PowerShell).
- Un secreto en el manifiesto del laboratorio **versionado**: los manifiestos
  locales viven en `%LOCALAPPDATA%\Temp\opencode\...`, fuera del repositorio.
- Reutilizar la clave HMAC del laboratorio en staging.
- Guardar tokens de invitación en claro, en logs o en la tabla de rate limit
  (solo se guarda `sha256(token)[:16]`).
- Conceder `SUPERUSER`, `BYPASSRLS`, `EXECUTE ... TO PUBLIC` o privilegios de
  tabla al principal global para "destrabar" una prueba.

---

## 3. Separación de principales (contrato que debe sobrevivir al despliegue)

| Rol | Login | Superuser | BypassRLS | Función |
|---|---|---|---|---|
| `fs_saas_runtime` | sí | no | no | API tenant; sujeto a RLS y a `FORCE ROW LEVEL SECURITY` |
| `saas_admin_runtime` | sí | no | no | Solo `EXECUTE` sobre funciones de `saas_admin`; **cero** privilegios de tabla |
| `saas_admin_owner` | no | no | no | Dueño de tablas/funciones del plano global |
| `saas_personal_owner` | no | no | no | Dueño de `sp_alta_personal_tenant` y aceptación de invitaciones |
| `saas_rls_owner` | no | no | no | Dueño de `saas.tenant_actual()`/`actor_actual()` y del secreto HMAC |
| `saas_actor_sql_owner` | no | no | no | Dueño de `fs_verificar_actor_reserva` |

Verificación (sin exponer valores):

```sql
SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolinherit
  FROM pg_roles
 WHERE rolname IN ('fs_saas_runtime','saas_admin_runtime','saas_admin_owner',
                   'saas_personal_owner','saas_rls_owner','saas_actor_sql_owner')
 ORDER BY rolname;

SELECT count(*) FROM information_schema.role_table_grants
 WHERE grantee = 'saas_admin_runtime';           -- debe ser 0

SELECT has_table_privilege('fs_saas_runtime','public.usuario','INSERT');  -- false
SELECT has_schema_privilege('fs_saas_runtime','saas_admin','USAGE');      -- false
```

---

## 4. Aprovisionamiento por entorno

| Entorno | Origen de secretos | Rotación | Notas |
|---|---|---|---|
| Laboratorio desechable | generados por el runner en `lab_5bp_secrets.json` (fuera de Git) | por corrida | Se descartan al eliminar el laboratorio |
| Staging | gestor externo del proveedor (p. ej. variables del panel del hosting) | trimestral o ante incidente | Nunca compartir con producción |
| Producción | gestor dedicado con auditoría de acceso | trimestral + rotación por `kid` | Doble custodia para la clave HMAC |

### 4.1 Rotación de la clave del superadministrador (sin downtime)

`SAAS_SUPERADMIN_KEY_SHA256` acepta lista con identificadores:

```
<kid_nuevo>:<hex64>,<kid_anterior>:<hex64>
```

1. Añadir la clave nueva como `vN`.
2. Reiniciar el proceso (lectura de entorno).
3. Verificar `GET /saas/superadmin/claves` → `rotacion_simultanea: true`.
4. Migrar a los clientes a la clave nueva.
5. Retirar la huella antigua y reiniciar.

`GET /saas/superadmin/claves` devuelve **huella parcial** y `kid`; nunca la clave
ni el hash completo.

### 4.2 Rotación de la clave HMAC de aserción

La clave vive en `saas.rls_secret` (sin privilegios para el runtime) y en el
entorno del proceso. La rotación exige **ventana coordinada**:

1. Detener tráfico tenant (flags OFF o mantenimiento).
2. Actualizar el secreto en PostgreSQL y en el entorno con el mismo valor.
3. Reiniciar el proceso y ejecutar la verificación A/B del runbook §7.
4. Reanudar tráfico.

Una discrepancia entre ambos lados produce **denegación total** de consultas
tenant (fail closed), no una fuga.

---

## 5. Fail-closed: comportamiento esperado si falta un secreto

| Falta | Respuesta observada |
|---|---|
| `SAAS_TENANT_ASSERTION_KEY` | Consultas tenant denegadas por RLS |
| `SAAS_ADMIN_DATABASE_URL` | `503` en CU01/CU02 y en `POST /auth/tenant/invitaciones/aceptar` (B9) |
| `SAAS_SUPERADMIN_KEY_SHA256` | `503` en `/saas/superadmin/*` |
| Flag de producto OFF | `503` en la ruta correspondiente |
| Función B9 ausente en la base | `503` en aceptación de invitaciones (nunca "sin límite" silencioso) |

La tabla anterior es deliberada: **ningún secreto ausente degrada a "sin
protección"**. Si un despliegue observa un comportamiento distinto, es un defecto
que debe corregirse antes de abrir tráfico.

---

## 6. Checklist antes de abrir staging

- [ ] Todos los secretos provienen del gestor, no del repositorio.
- [ ] `git grep` no encuentra valores de secreto (solo nombres de variable).
- [ ] La clave HMAC de staging es distinta de la de laboratorio.
- [ ] Existe al menos una clave de superadmin con `kid` y un plan de rotación.
- [ ] `saas_admin_runtime` tiene 0 privilegios de tabla.
- [ ] `fs_saas_runtime` no puede insertar en `public.usuario`.
- [ ] La aceptación de invitaciones responde `503` si falta el principal global.
- [ ] Los logs no contienen tokens, DSN ni cuerpos de request sensibles.
- [ ] La evidencia generada no contiene secretos (revisión manual del archivo).

---

## 7. Qué no implementa esta etapa

No se introduce un gestor de secretos propio, ni cifrado en reposo adicional, ni
HSM. La etapa **define el contrato** y deja el sistema fallando cerrado; la
elección del gestor corresponde al despliegue.
