# Despliegue en Supabase — procedimiento único

Estado: **preparado para revisión y autorización. Nada de este documento se ha
ejecutado.** Las tres propuestas siguen protegidas con su guarda `NO_EJECUTAR`;
las copias ejecutables se generan aparte en `despliegue/` (ver §2.1) y no
sustituyen la aprobación de la ventana.

Alcance: `010`–`040` (estructura y tenant), `050a`–`050c` (identidad, RLS y ACL),
más las comprobaciones previas y posteriores. Quedan fuera, deliberadamente,
CU16/17/18 (reservas) y el módulo superadmin.

---

## 1. Compatibilidad con el primer parcial: qué se rompe y cuál es el corte

Verificado en código, no supuesto:

1. **`020` cierra las inserciones sin `empresa_id`.** Añade la columna, la deja
   `NOT NULL` y **retira el DEFAULT** en el mismo bucle
   (`020_tenant_expand_backfill.sql:14-16`). El ORM no aporta valor por defecto
   (`app/core/tenant_models.py:14-16,26`: «No se asigna default de Vanter Men») y
   los servicios legacy no envían la columna. Resultado: `23502 not-null
   violation` en todo INSERT del primer parcial (`/categorias`, `/productos`,
   `/carrito`, `/ventas`, `/compras`, `/reservas`…).
2. **`040` cierra los 9 procedimientos legacy.** `REVOKE ALL ... FROM PUBLIC` y
   de `anon`/`authenticated` (`040_routines_views_hardening.sql:103-112`); `050c`
   remata con `REVOKE ALL ON ALL FUNCTIONS ... FROM PUBLIC` (`050c:19`). El único
   consumidor en FastAPI es `app/modules/compras/services/service.py:590`
   (`CALL sp_recibir_orden_compra`), alcanzable sólo por la ruta legacy `/compras`.
3. **`050b` activa RLS forzado en las 46 tablas** (`050b:190-198`), con policies
   dirigidas exclusivamente a roles `fashionstore_*`. Cualquier conexión que no
   sea miembro de esos roles deja de ver y de escribir datos.

Sonda ejecutada sobre la base local que ya tiene `010`–`040` (transacción
revertida, sin cambios): `insert into public.categoria(nombre,estado) values
(...)` devuelve **`23502`: «el valor nulo en la columna empresa_id ... viola la
restricción de no nulo»**. Es exactamente la escritura del primer parcial.

**No se necesita ningún cambio en `010`–`040`**: la aplicación ya dispone del
corte. `app/core/saas_route_gate.py` es una lista blanca *exacta*: con
`SAAS_AUTH_ENABLED=true` toda ruta no enumerada responde **503**
(`saas_route_gate.py:180,201-209`); las rutas migradas `/tenant/*` y
`/auth/tenant/*` sí pasan, con dependencia de tenant obligatoria. Con
`SAAS_AUTH_ENABLED=false` el middleware no interviene y el comportamiento del
primer parcial se mantiene intacto.

**Consecuencia operativa (el corte):** desde el `COMMIT` de `020` la base ya no
admite escritura legacy, así que la ventana es **atómica**: la API se detiene, se
aplican `010`→`050c` y se vuelve a arrancar con `SAAS_AUTH_ENABLED=true`. No hay
una alternativa SQL razonable: un `DEFAULT` fijo a Vanter Men reintroduciría
escritura entre empresas y contradice el modelo de `050b`, así que se descarta a

---

## 2. Antes de la ventana (sin tocar Supabase todavía)

### 2.1 Copias de despliegue (los originales quedan protegidos)

```
python database/saas/preparacion_pg17/generar_despliegue.py
```

Genera `despliegue/050a_identidad_jwt_roles.sql`, `despliegue/050b_rls_policies.sql`
y `despliegue/050c_acl_procedimientos.sql` con **una sola** diferencia respecto al
original: se retira la guarda `BLOQUEADO`. La cabecera de cada copia anota el
sha256 del original y `despliegue/MANIFIESTO.sha256` registra ambos hashes.
El generador aborta si un original pierde su guarda o si aparece más de una, y es
idempotente (regenerar no cambia nada). **No editar nunca los archivos de
`despliegue/`: se regeneran.**

### 2.2 Vault y secreto (prerrequisito bloqueante de `050a`)

`050a` exige `vault.decrypted_secrets` con `fashionstore_tenant_jwt_hs256_active`
y, opcionalmente, `fashionstore_tenant_jwt_hs256_previous` (`050a:36-39`). El
valor activo **debe ser idéntico a `SECRET_KEY`** de la API: los tokens se firman
en `app/core/security.py:30` y se verifican en
`fashionstore_private.actor_claims()` con `extensions.hmac` (`050a:137-140`). Se
crea una vez, con un valor aleatorio de 32+ bytes, desde el Dashboard/SQL editor;
**nunca se escribe en el repositorio**:

```sql
-- Sustituir <VALOR_ALEATORIO> al pegarlo; no guardar este SQL con el valor real.
select vault.create_secret('<VALOR_ALEATORIO>','fashionstore_tenant_jwt_hs256_active',
                           'FashionStore tenant JWT HS256 (activa)');
```

Rotación: el valor anterior se conserva como `..._previous`, de modo que los
tokens vivos siguen validando durante la transición.

### 2.3 Logins (ninguno lo crea la cadena; `050c:144-148` lo deja explícito)

Tres LOGIN dedicados, sin privilegios propios, miembros de su rol de grupo:

```sql
-- Pegar en el SQL editor del proyecto; la contraseña no se versiona.
create role fashionstore_api_login       login password '<...>' in role fashionstore_api;
create role fashionstore_bootstrap_login login password '<...>' in role fashionstore_bootstrap;
create role fashionstore_platform_login  login password '<...>' in role fashionstore_platform_admin; -- sólo si se abre superadmin
```

Requisitos: `NOSUPERUSER`, `NOBYPASSRLS`, `NOCREATEROLE`, sin acceso a
`service_role`. **El pool FastAPI no debe usar nunca `postgres` ni `service_role`:**
ambos evitan RLS por diseño y dejarían la separación multiempresa sin efecto real.
`service_role` conserva además los GRANT que Supabase aplica por *default
privileges*; revisar esa frontera es una decisión aparte, no parte de esta ventana.

### 2.4 Entorno de la API (en el gestor de secretos del despliegue)

```
DATABASE_URL=postgresql+psycopg://fashionstore_api_login:<...>@<host-directo>:5432/postgres?sslmode=require
DATABASE_BOOTSTRAP_URL=postgresql+psycopg://fashionstore_bootstrap_login:<...>@<host>:5432/postgres?sslmode=require
DATABASE_PLATFORM_URL=postgresql+psycopg://fashionstore_platform_login:<...>@<host>:5432/postgres?sslmode=require
SECRET_KEY=<mismo valor que el secreto activo de Vault>
SAAS_AUTH_ENABLED=true
SAAS_RESERVAS_ENABLED=false
SAAS_BITACORA_POR_TRIGGER=true
SAAS_ACTOR_SQL_OWNER=fashionstore_context_owner
SAAS_SP_SQL_OWNER=fashionstore_sp_owner
```

- Driver `psycopg` 3 (`requirements.txt:22-23`): el DSN debe ser
  `postgresql+psycopg://`.
- `DATABASE_BOOTSTRAP_URL` es **obligatorio**: `bootstrap_credencial()` y
  `bootstrap_empresa_id()` sólo se conceden a `fashionstore_bootstrap`
  (`050a:251,268`) y `050c` no los abre al rol de API. Sin ese pool el login por
  slug falla con `42501`. Si falta, `config.py:12-16` reutiliza `DATABASE_URL` y
  el fallo aparece en ejecución, no al arrancar.
- `SAAS_SP_SQL_OWNER` sólo interviene en CU16 (apagado); se deja puesto para
  cuando se habilite.

### 2.5 Respaldo y conectividad

1. `pg_dump -Fc` de `public` **inmediatamente** antes de la ventana, con hash e
   inventario de las 41 tablas; verificar restaurándolo en una base desechable.
2. Probar la conexión directa (no el pooler en modo transacción) con el rol
   administrativo: `psql "<URI>" -c "select version()"`.
3. Ejecutar `001_ventana_previa.sql` y **archivar su salida**: es la referencia
   contra la que se interpretan los fallos de `000`.

---

## 3. Ventana: orden exacto de ejecución

Convenciones: **una sola sesión administrativa**, conexión directa, un archivo a
la vez, `ON_ERROR_STOP` activo y sin continuar jamás tras un error.

```
psql "<URI_directa>" -v ON_ERROR_STOP=1 -f <archivo>
```

| # | Archivo | Qué deja | Salida que debe verse |
|---|---------|----------|-----------------------|
| 1 | `001_ventana_previa.sql` | nada (sólo lectura) | versión 17.x, 41 tablas, 0 columnas `empresa_id`, conteos y ACL legacy |
| 2 | `000_preflight.sql` | nada (sólo lectura) | `PRECHECK_OK` |
| 3 | `010_saas_core_vanter.sql` | 5 tablas SaaS + Vanter Men + miembros | `COMMIT` |
| 4 | `020_tenant_expand_backfill.sql` | `empresa_id` en 31 tablas, todo a Vanter Men | `COMMIT` |
| 5 | `030_tenant_integrity.sql` | unicidad tenant, 48 FK, índices | `COMMIT` |
| 6 | `040_routines_views_hardening.sql` | vista endurecida, inmutabilidad, `sp_*` cerrados | `COMMIT` |
| 7 | `060_postflight.sql` | nada (sólo lectura) | `POSTCHECK_SCHEMA_INTEGRITY_OK_RLS_AUN_BLOQUEADO` |
| 8 | `despliegue/050a_identidad_jwt_roles.sql` | roles, Vault-view, verificación JWT, bootstrap | `COMMIT` |
| 9 | `despliegue/050b_rls_policies.sql` | policies + `FORCE RLS` en las 46 tablas | `COMMIT` |
| 10 | `despliegue/050c_acl_procedimientos.sql` | ACL final, jerarquía de owners, membresía | `COMMIT` |
| 11 | §2.3 (logins) | 3 LOGIN dedicados | `CREATE ROLE` ×3 |
| 12 | §4 (verificación) | comprobaciones posteriores | todas las filas esperadas |
| 13 | §7 (arranque) | API en modo SaaS | smoke correcto |

Comprobaciones intermedias mínimas (una por paso, para no avanzar a ciegas):

```sql
-- tras 010
select count(*) from public.empresa;            -- 1
select count(*) from public.plan_saas;          -- 2
select count(*) from public.usuario_empresa;    -- = count(*) de public.usuario
-- tras 020
-- 34 = 31 tablas empresariales + suscripcion, usuario_empresa y empresa_configuracion
-- (010 ya creó las tres). information_schema cuenta además la vista de 040: 35.
select count(*) from information_schema.columns c
  join pg_class cl on cl.relname=c.table_name and cl.relnamespace='public'::regnamespace
 where c.table_schema='public' and c.column_name='empresa_id' and cl.relkind='r';   -- 34
select count(*) from public.producto p join public.empresa e on e.id=p.empresa_id
 where e.slug<>'vanter-men';                                -- 0
-- tras 030
select count(*) from pg_constraint where conname like 'fk_tenant_%';   -- 48
-- tras 040
select has_function_privilege('authenticated','public.sp_crear_reserva','EXECUTE');  -- false
select reloptions @> ARRAY['security_invoker=true'] from pg_class
 where oid='public.vw_inventario_stock'::regclass;          -- t
```

`000` y `060` son de sólo lectura y terminan en `ROLLBACK`: no cambian nada y se
pueden repetir sin riesgo. `010`–`040` son transaccionales por archivo
(`BEGIN`/`COMMIT` propios) y toman el mismo `pg_advisory_xact_lock`, de modo que
dos ejecuciones simultáneas no se solapan. `050a`–`050c` también son
transaccionales por archivo, pero **no son re-ejecutables una vez confirmadas**:
`050b` crea policies con `CREATE POLICY` (segunda pasada daría «already exists»).
Tras un `COMMIT` de `050a`–`050c`, un nuevo intento no es un reintento: es §6.

---

## 4. Verificaciones posteriores (antes de arrancar la API)

```sql
-- 4.1 RLS activo y FORZADO en todas las tablas de public
select count(*) filter (where c.relrowsecurity)     as rls,
       count(*) filter (where c.relforcerowsecurity) as forzado
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
 where n.nspname='public' and c.relkind='r';        -- 46 / 46

-- 4.2 Los 7 roles de grupo existen y ninguno es superusuario ni BYPASSRLS
select count(*) from pg_roles where rolname like 'fashionstore\_%';    -- 7
select rolname from pg_roles
 where rolname like 'fashionstore%' and (rolsuper or rolbypassrls);    -- 0 filas

-- 4.3 Los 3 logins existen y son NOBYPASSRLS
select rolname, rolcanlogin, rolsuper, rolbypassrls
  from pg_roles where rolname like '%\_login' order by 1;

-- 4.4 Fallo cerrado sin token (así se comporta el pool de API sin JWT)
set role fashionstore_api;
select count(*) from public.producto;      -- ERROR 42501 «Contexto no autenticado»
reset role;

-- 4.5 Ningún rol de Supabase puede ejecutar los procedimientos legacy
select p.proname,
       has_function_privilege('anon',p.oid,'EXECUTE')          as anon,
       has_function_privilege('authenticated',p.oid,'EXECUTE') as authenticated
  from pg_proc p join pg_namespace n on n.oid=p.pronamespace
 where n.nspname='public' and p.prokind='p' order by 1;   -- false/false en los 9

-- 4.6 Policies creadas (anotar el número; sirve de referencia para futuras ventanas)
select count(*) from pg_policies where schemaname='public';
```

## 5. Condiciones para detener la migración

| # | Señal | Acción |
|---|-------|--------|
| D1 | `psql "<URI>" -c "select version()"` no dice 17.x | No empezar. `000` y `050a` abortan por diseño. |
| D2 | `000` falla por **cantidades** (y `001` muestra que sólo crecieron filas) | Detener y decidir: el baseline cambió. No es un fallo de la migración, pero invalida la comparación con `060`. |
| D3 | `000` falla por estructura (tablas, columnas, routines) | Detener: hay deriva real respecto del respaldo. Restaurar/actualizar antes de reintentar. |
| D4 | Cualquier archivo de §3 devuelve error | Detener. El archivo activo revierte solo; **no** ejecutar el siguiente ni «arreglar a mano» en caliente. |
| D5 | `060` no imprime su literal | Detener antes de `050a`: la estructura o los datos no se preservaron. |
| D6 | §4 no da los valores esperados | No arrancar la API. La aplicación quedaría sin aislamiento o sin poder operar. |
| D7 | Smoke de §7 con error 500 | Parar el servicio, revisar logs con el rol sospechoso (grants/DSN) y volver a §4; no abrir tráfico. |

## 6. Recuperación

- **Fallo dentro de un archivo** (caso D4): PostgreSQL revierte ese archivo
  completo. La base queda como antes de ese paso. Corregir la causa y reintentar
  el mismo archivo; nunca saltarlo.
- **Fallo con `010`–`040` ya confirmados**: la base queda parcialmente migrada y
  **no admite tráfico legacy** (escrituras sin `empresa_id` fallan). Dos salidas:
  1. Restaurar el respaldo `-Fc` de §2.5 en el **mismo proyecto** (con la API
     detenida y el esquema `public` recreado desde el dump) y volver al estado
     previo.
  2. Restaurar ese respaldo en un **proyecto nuevo** y repuntar el DSN.
- No se improvisan `DROP` sobre producción ni se escriben scripts inversos en
  caliente: `010`–`040` son aditivos, pero su vuelta atrás revisada no existe
  todavía y no forma parte de esta entrega.
- **Tras `050a`–`050c`** la vuelta atrás tampoco es SQL suelto: `050b` deja RLS
  forzado; el camino previsto es el respaldo. Si sólo hay que desactivar el
  aislamiento funcional sin revertir, la palanca es el flag
  `SAAS_AUTH_ENABLED=false` **sólo si** las escrituras legacy vuelven a ser
  válidas, lo que ya no ocurre tras `020`.


---

## 7. Arranque y smoke (cierre de la ventana)

1. Arrancar la API con el entorno de §2.4 (`SAAS_AUTH_ENABLED=true`).
2. Comprobaciones, en este orden; si alguna falla, D7:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://<host>/health                     # 200
curl -s -o /dev/null -w "%{http_code}\n" http://<host>/productos                  # 503 (gate legacy)
curl -s -X POST http://<host>/auth/tenant/login -H 'Content-Type: application/json' \
     -d '{"empresa_slug":"vanter-men","correo":"nadie@ejemplo.invalido","password":"x"}'   # 401, nunca 500
```

3. Login real con una credencial administrativa conocida (la del primer parcial):
   debe devolver token y `GET /tenant/catalogo/productos` con ese token debe
   listar el catálogo de Vanter Men; `GET /tenant/empresa/me` debe devolver la
   empresa y el plan. Un 500 en el login apunta a `DATABASE_BOOTSTRAP_URL` o a
   `SECRET_KEY` distinto del secreto de Vault; un 200 vacío apunta a policies.
4. Registrar en el acta: hashes de `MANIFIESTO.sha256`, salida de `001`/`000`/`060`,
   valores de §4 y hora de cada paso.

## 8. Qué queda cerrado después (no impide migrar)

- **CU16/CU17/CU18 (reservas):** los tres SP tenant no existen como versión
  productiva (`050c:130-142` deja el contrato, no los cuerpos) y las rutas siguen
  detrás de `SAAS_RESERVAS_ENABLED=false`. `POST /tenant/reservas` permanece en 503.
- **Superadmin global:** requiere `SAAS_SUPERADMIN_ENABLED` y
  `SAAS_SUPERADMIN_KEY_SHA256`, fuera de esta ventana.
- **Rutas legacy:** todas en 503 mientras `SAAS_AUTH_ENABLED=true`. Reabrirlas
  exige migrarlas a `empresa_id`, no bajar el flag.
- **`040` ya no instala los SP tenant** y `050c` no los concede: no hay hueco
  funcional que esta ventana deje a medias.

## 9. Bloqueos reales para declarar la migración lista

1. **PG17 en el proyecto Supabase**: si el proyecto está en 15, `000` y `050a`
   abortan y todo el plan queda detenido. Verificarlo es el paso 0.
2. **Sin rama de pruebas**: la ventana se ejecuta sobre la única base. Cualquier
   error tras `020` obliga a restaurar respaldo (§6). Es un riesgo aceptado, no un
   defecto del SQL.
3. **Logins y secretos son actos manuales** (§2.2, §2.3): mientras no existan, la
   API no puede operar bajo RLS y `DATABASE_BOOTSTRAP_URL` no puede apuntarse.
4. **`SECRET_KEY` = secreto activo de Vault**: sin esa coincidencia el login
   devuelve 401 con firma inválida y no hay forma de detectarlo desde SQL.
5. **CU16/17/18 quedan fuera**: si se espera vender reservas en este despliegue,
   es un bloqueo funcional, no técnico de esta ventana.


