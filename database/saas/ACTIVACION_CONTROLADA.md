# Activación controlada del SaaS — FashionStore API

Corte: 30 de octubre de 2026. Rama `feat/saas-multitenant-parcial2`.
**Dictamen actual: BLOQUEADO.** No se aplicó nada en Supabase, Render ni
ningún entorno productivo. Los flags siguen apagados por defecto.

## 1. Estado de partida comprobado

| Componente | Estado | Evidencia |
|---|---|---|
| CU01/CU02 API superadmin | HECHO (código) | 17 pruebas SQLite offline |
| CU01 primer administrador | HECHO (código) | 13 pruebas SQLite offline |
| CU04 membresías y roles | HECHO (código) | 13 pruebas SQLite offline |
| Núcleo SaaS y planes | HECHO (migración portable) | `database/saas/73_b3_p6_migracion_portable_y_staging_5bj.md` |
| Tenencia `empresa_id` 31 tablas | HECHO en staging | 31/31 columnas, 0 filas sin tenant |
| FK compuestas / UNIQUE por tenant | HECHO en staging | 48 FK, 28 UNIQUE, 31 índices |
| Idempotencia del bloque | HECHO | Segunda aplicación sin cambios |
| RLS con políticas por tenant | **BLOQUEADO** | Solo RLS habilitado sin políticas |
| Principal SQL restringido | **BLOQUEADO** | No instalado ni certificado |
| Escrituras comerciales tenant | PARCIAL | Catálogo/carrito/reservas; ventas/pagos/compras/devoluciones sin migrar |
| HTTP A/B con PostgreSQL real | **BLOQUEADO** | Pendiente Etapa C |
| Migración en Supabase productivo | **NO EJECUTADA** | Sin aprobación explícita |

## 2. Migraciones a desplegar (orden exacto)

1. `supabase/migrations/20261030000100_saas_core_portable.sql`
2. Registrar la empresa histórica (API superadmin o `INSERT` revisado).
3. `supabase/migrations/20261030000200_saas_empresa_id_portable.sql`
   con `SET saas.bootstrap_empresa_slug = '<slug>'` en la misma sesión y
   `SET saas.bootstrap_crear_membresias = 'true'` solo si se autoriza.
4. `supabase/migrations/20261030000300_saas_integridad_tenant_portable.sql`

**No** aplicar `20261023000100..000400`: son del laboratorio local y
`20261023000400` siembra tenants ficticios.

Ninguna está registrada aún en el historial remoto de Supabase; verificar con
`supabase migration list` antes de cualquier `db push`.

## 3. Preflight obligatorio (solo lectura)

```powershell
psql "$DESTINO" -v ON_ERROR_STOP=1 -f database/saas/71_preflight_migracion_saas_solo_lectura.sql
```

Criterios de aborto inmediato:

- Servidor con versión menor a 15 (la migración usa `ADD COLUMN` con DEFAULT no
  volátil y `jsonb`).
- `duplicados` con cualquier valor distinto de `0` en `categoria.nombre`,
  `temporada.nombre`, `variante_producto.sku`, `sucursal(ciudad,nombre)`,
  `proveedor.nit`, `cliente.ci`, `cliente.usuario_id`, `empleado.ci` o
  `empleado.usuario_id`.
- `usuarios_sin_membresia` mayor a 0 sin decisión documentada.
- Presencia de `empresa_id` en algunas tablas y no en otras (migración parcial
  previa): revisar manualmente antes de continuar.
- Triggers deshabilitados que deban permanecer habilitados.

## 4. Respaldo y restauración (no ejecutados)

- Respaldo lógico: `pg_dump --format=custom --no-owner --no-privileges`
  del esquema `public` + `pg_dumpall --globals-only` para roles.
- Verificar restauración en una instancia desechable **antes** del día de
  migración (`pg_restore --list` y restauración completa).
- Conservar el volcado fuera del repositorio y con retención definida.
- No se restauró nada en esta etapa: no se dispone de credenciales ni
  autorización para producción.

## 5. Orden de despliegue de la API

1. Respaldo verificado + preflight en verde.
2. Aplicar el bloque de migraciones (paso 2).
3. Verificación posterior (paso 6).
4. Desplegar la versión de la API **con flags apagados**; comprobar `/health`.
5. Activar por entorno, en este orden y solo con gates cumplidos:
   `SAAS_AUTH_ENABLED` → `SAAS_SUPERADMIN_ENABLED` → `SAAS_RESERVAS_ENABLED`.
6. Smoke tests (paso 7) y observación de logs anonimizados.

## 6. Verificación posterior

```powershell
psql "$DESTINO" -v ON_ERROR_STOP=1 -f database/saas/72_verificacion_post_migracion_saas_solo_lectura.sql
```

Debe mostrar: 5 tablas del núcleo, planes BASICO=2/PROFESIONAL=10 con IA solo
en PROFESIONAL, 31 tablas con `empresa_id`, 0 filas sin tenant, 48 `fk_tenant`,
28 `uq_tenant`, 31 índices, RLS en las 5 tablas, `relaciones_con_cruce=0`,
`clientes_sin_membresia=0`, `empleados_sin_membresia=0`.

## 7. Variables de entorno (solo nombres)

`DATABASE_URL`, `SECRET_KEY`, `SAAS_AUTH_ENABLED`, `SAAS_SUPERADMIN_ENABLED`,
`SAAS_SUPERADMIN_KEY_SHA256`, `SAAS_RESERVAS_ENABLED`, `SAAS_ACTOR_SQL_OWNER`,
`SAAS_SP_SQL_OWNER`.

Reglas: ningún valor en el repositorio; `SAAS_SUPERADMIN_KEY_SHA256` es el
SHA-256 de una clave ≥ 32 caracteres entregada fuera de banda; los flags de
reserva solo se activan con SP y principal SQL certificados.

## 8. Smoke tests mínimos tras activar

1. `GET /health` y `GET /health/db` responden 200.
2. `POST /auth/tenant/login` con empresa A devuelve JWT; `GET /auth/tenant/me`
   devuelve la empresa A.
3. El token de A no puede leer ni modificar recursos de B (GET/POST/PATCH/DELETE).
4. Un usuario `CLIENTE` recibe 403 en rutas de administración de empresa.
5. El header de superadmin sin clave válida recibe 401 y no revela detalles.
6. Suscripción vencida o empresa suspendida: login denegado.
7. Límite de plan: crear la sucursal 3 en BASICO debe fallar; en PROFESIONAL
   debe permitirse hasta 10 activas.
8. Reportes IA denegados en BASICO.

## 9. Rollback

1. Revertir el despliegue de la API a la versión anterior (flags apagados).
2. Si hay que revertir el esquema, usar el respaldo verificado del paso 4:
   restaurar en una instancia nueva y repuntar, o restaurar el esquema en la
   instancia actual con ventana de mantenimiento.
3. Reversión fina sin pérdida de datos: eliminar `fk_tenant_*`, `uq_tenant_*` e
   índices `idx_*_empresa`. Eliminar `empresa_id` es destructivo.
4. No ejecutar `DROP DATABASE`, `DROP SCHEMA`, `TRUNCATE` ni borrados masivos
   sin respaldo confirmado.

## 10. Criterio go / no-go

**No-go** mientras se cumpla cualquiera de estas condiciones:

- RLS con políticas por tenant no certificado con principal SQL no privilegiado.
- Pruebas HTTP A/B con dos empresas reales no ejecutadas sobre PostgreSQL.
- Escrituras comerciales esenciales (venta, pago, devolución, compra) sin
  tenantizar o sin bloqueo explícito verificado.
- Superadmin sin endurecimiento (rotación, rate limit, auditoría, HTTPS) más
  allá del header + SHA-256 de prototipo.
- Respaldo y restauración no comprobados.

**Go** solo cuando los cinco puntos anteriores tengan evidencia ejecutada y el
responsable autorice explícitamente la ventana de migración.

## 11. Etapa C (5BK) - principal SQL restringido, RLS y HTTP A/B

**Fecha:** 31 de octubre de 2026 (hora UTC del reporte 2026-10-31T01:07:32Z).
**Dictamen de la etapa: ETAPA C VALIDADA en laboratorio desechable.**
La tabla de la seccion 1 refleja el estado del corte anterior; esta seccion la
actualiza para el acceso SQL.

| Componente | Estado | Evidencia |
|---|---|---|
| Principal SQL restringido | HECHO y verificado | Rol `fs_saas_runtime`: `rolsuper=false`, `rolbypassrls=false`, `rolinherit=false`, 0 membresias, 0 tablas propias |
| RLS con politicas por empresa | HECHO | 37 tablas con RLS y `FORCE ROW LEVEL SECURITY`, 44 politicas, 31 tablas empresariales con `tenant_aislamiento` |
| Identidad no falsificable | HECHO | Asercion HMAC-SHA256 verificada por `saas.tenant_actual()` + revalidacion de membresia/plan/suscripcion en base |
| Bootstrap de login | HECHO | `saas.login_credenciales` y `saas.login_membresia` (SECURITY DEFINER, propietario NOLOGIN); el runtime no tiene SELECT sobre `public.usuario` |
| HTTP A/B con PostgreSQL real | HECHO | 39 pruebas de integracion, uvicorn real, sin mocks ni overrides |
| Migracion 00400 | HECHA en laboratorio | `supabase/migrations/20261030000400_saas_rls_principal_restringido_portable.sql` |
| Trigger historicos (27) | PENDIENTE | El staging ORM no los reproduce; es la Etapa D |
| Escrituras comerciales (venta/pago/compra/devolucion) | PENDIENTE | Sin tenantizar; flags apagados |

### 11.1 Que cambio en el acceso SQL

1. `saas.tenant_actual()` resuelve la empresa desde una asercion firmada
   `empresa|usuario|expira|nonce|hmac`. El secreto vive en `saas.rls_secret`
   (sin privilegios para el runtime) y en la configuracion del proceso.
2. Las politicas usan `empresa_id = saas.tenant_actual()`; `set_config('app.empresa_id', ...)`
   queda solo como dato de auditoria y no autoriza nada.
3. La funcion revalida en base membresia activa, usuario activo, empresa activa,
   rol activo, plan activo y suscripcion ACTIVA vigente. Una asercion valida
   deja de servir si se revoca la membresia.
4. El login ya no consulta `public.usuario` directamente: usa dos funciones
   cerradas con propietario NOLOGIN. Esa es la separacion bootstrap/tenant.
5. El principal no puede `SET ROLE`, ni `SET SESSION AUTHORIZATION`, ni ejecutar
   funciones fuera de las tres autorizadas.

### 11.2 Resultado de pruebas (observado)

- Suite nueva `tests_saas/test_rls_http_ab_staging_5bk.py`: **39 passed**, 0 failed, 0 skipped.
- Regresion A/B relevante (13 archivos offline de CU01/CU02/CU04, route gate,
  JWT tenant, ORM, 5BH/5BI y suites HTTP SQLite): **353 passed, 3 failed**.
  Los 3 fallos son preexistentes: `tests_saas/test_tenant_empresa_sucursales_planes_http.py`
  espera `PATCH /tenant/empresa/plan` en 200, pero el `saas_route_gate` de HEAD
  nunca autorizo ese PATCH (CU02 exige superadmin). No se amplio la allowlist.
- Barrido completo `pytest tests_saas`: **1086 passed, 16 failed, 1 skipped**.
  Los 16 fallos son preexistentes (3 del punto anterior + 13 de
  `test_reserva_transaccional_inerte_5al.py`, que exige `saas_actor_sql_owner`
  configurado por entorno y no lo esta en este corte).

### 11.3 Condiciones que siguen bloqueando la activacion

- Migracion y prueba sobre respaldo real despersonalizado con los 27 triggers y
  procedimientos del primer parcial (Etapa D).
- Tenantizacion de venta, pago, compra, devolucion y movimientos de stock.
- Endurecimiento de superadmin (RBAC, rotacion, rate limiting, HTTPS) y
  backup/restauracion verificado.
- Operacion del secreto HMAC: aprovisionamiento, rotacion y almacenamiento
  (hoy es variable de entorno del proceso, sin gestor de secretos).

**Ninguna etapa superada por si sola autoriza produccion.** El dictamen
`ETAPA C VALIDADA` se refiere exclusivamente a principal SQL, RLS y HTTP A/B en
un PostgreSQL desechable.

---

## 12. Etapa D (5BL) — compatibilidad con el esquema historico, triggers y bitacora

**Fecha:** 31/10/2026 (hora local 30/10/2026). **Rama:** `feat/saas-multitenant-parcial2`.
**Dictamen:** `ETAPA D VALIDADA PARCIAL` — ver 12.6.

### 12.1 Que se construyo

| Artefacto | Ruta | Finalidad |
|---|---|---|
| Reconstruccion historica | `database/saas/75_esquema_historico_reconstruido_5bl.sql` | 9 funciones de trigger (cuerpos auditados), 14 disparadores, 3 procedimientos CU16/CU18 con sus cuerpos, vista `vw_inventario_stock` |
| Migracion nueva | `supabase/migrations/20261030000500_saas_compatibilidad_5bl.sql` | Endurece triggers, define el contrato de `bitacora`, revoca rutinas legacy y agrega ajuste de stock tenant-aware |
| Laboratorio | `database/saas/75_laboratorio_esquema_historico_5bl.py` | Cluster PostgreSQL 18 desechable en `127.0.0.1:55442`, base `fashionstore_lab_5bl`, marcador de fidelidad y evidencia |
| Pruebas SQL | `tests_saas/test_esquema_historico_triggers_5bl.py` | 34 pruebas de triggers, SP, auditoria y RLS |
| Pruebas HTTP | `tests_saas/test_rls_http_ab_historico_5bl.py` | 10 pruebas HTTP A/B sobre el esquema historico |

No se modificaron las migraciones 00100..00400 ni los tests 5BK.

### 12.2 Contrato de auditoria (resuelto)

- **Evento empresarial:** `public.bitacora` con `empresa_id` obligatorio. La
  politica `bitacora_insercion_tenant` exige `empresa_id = saas.tenant_actual()`;
  `bitacora_lectura_tenant` limita la lectura al tenant.
- **Evento global nuevo:** `saas.bitacora_global`, sin privilegios para el
  runtime, escrito solo por el propietario NOLOGIN o por un administrador. Un
  principal tenant que intente fabricarlo recibe `42501`.
- **Filas historicas `empresa_id IS NULL`:** se conservan, no se reasignan ni se
  borran. Quedan invisibles al tenant y clasificadas en
  `saas.bitacora_historica_global` y `saas.bitacora_clasificacion`.
- **Append-only:** el runtime conserva `SELECT` e `INSERT`, pierde `UPDATE` y
  `DELETE`; un trigger rechaza cualquier insercion sin tenant.
- Se elimino la asimetria de 5BK (`WITH CHECK` aceptaba NULL, `USING` lo
  ocultaba): ahora NULL no se acepta en la bitacora empresarial.

### 12.3 Triggers y procedimientos (observado)

- 15 triggers en `public`: 14 historicos + `trg_bitacora_rechazar_sin_tenant`.
- 0 triggers `SECURITY DEFINER`; 0 sin `search_path` fijo.
- Las dos funciones que carecian de `search_path`
  (`fn_actualizar_actividad_carrito`, `fn_validar_sucursal_detalle_carrito`) se
  corrigieron.
- `fn_controlar_actualizacion_inventario` ahora prohibe cambiar `empresa_id`.
- Los validadores de sucursal comparan tambien `empresa_id` de carrito/reserva e
  inventario.
- 3 procedimientos historicos conservan `EXECUTE` para PUBLIC: **revocado**.
  El runtime no puede invocarlos. Se demostro ademas que
  `sp_crear_reserva_desde_carrito` omite `empresa_id` y falla con `23502`, por lo
  que **no es reutilizable** sin reescritura tenant (Etapa E).
- Unica via de stock expuesta: `saas.sp_ajustar_inventario_tenant` (INVOKER,
  deriva la empresa de `saas.tenant_actual()`, no acepta empresa por parametro).

### 12.4 Pruebas (numeros exactos de este corte)

- `pytest tests_saas/test_esquema_historico_triggers_5bl.py -q`: **34 passed**.
- `pytest tests_saas/test_rls_http_ab_historico_5bl.py -q`: **10 passed**.
- Suite 5BK preservada: **39 passed** (su propio laboratorio, sin triggers).
- Barrido completo `pytest tests_saas -q -p no:randomly`: **1148 passed, 1 skipped, 0 failed**.
  Baseline de Etapa C: 1086 passed, 16 failed, 1 skipped.
- Los 3 fallos de `PATCH /tenant/empresa/plan` se corrigieron ajustando el test al
  contrato real (503; la ruta no existe y CU02 la reserva a superadmin). No se
  amplio la allowlist. Se agrego una prueba que verifica que ni con
  `SAAS_SUPERADMIN_ENABLED=true` se abre el PATCH tenant.
- Los 13 fallos de `test_reserva_transaccional_inerte_5al.py` se resolvieron
  configurando `saas_actor_sql_owner` como fixture explicito y modelando las
  consultas reales del verificador de actor. Se agregaron dos pruebas de
  fail-closed (sin propietario y con propietario igual al principal).

### 12.5 Hallazgos y correcciones

1. La vista `vw_inventario_stock` no era accesible al runtime: 00400 no concedia
   `SELECT` sobre vistas. 00500 lo concede explicitamente y conserva
   `security_invoker=true`.
2. El cuerpo auditado de `fn_registrar_bitacora` ya incluia `empresa_id`; el
   cuerpo historico real (anterior a 00200) no. La reconstruccion usa la version
   pre-tenant y 00500 instala la version tenant-aware.
3. `bitacora` con filas NULL es un caso real: se reprodujo con una actualizacion
   de usuario (tabla global) y con una accion de tenant antes de 00500.
4. El alta de usuarios (CU01/bootstrap) sigue fuera del principal restringido:
   00400 revoca `INSERT` sobre `public.usuario`. Es un bloqueo dirigido para la
   etapa de superadmin, no se resolvio concediendo privilegios al runtime.

### 12.6 Fidelidad y por que el dictamen es PARCIAL

No existe respaldo ni `pg_dump` del esquema real accesible en este equipo (se
busco en el repositorio y en el entorno local; el PostgreSQL de `:5432` exige
credenciales no disponibles y esta protegido). La reconstruccion cubre:

- **40/41** tablas historicas (mapeo ORM; la tabla restante no esta identificada
  en ninguna fuente versionada);
- **14/27** disparadores documentados (cuerpos reales de 9 funciones auditadas +
  2 funciones de proteccion reconstruidas por comportamiento);
- **13/24** rutinas (3 SP con cuerpo auditado + 10 funciones);
- **1/1** vista, con definicion reconstruida.

La validacion demuestra compatibilidad funcional y de aislamiento sobre lo
reproducido, pero no sobre el esquema real completo. Para `VALIDADA` plena hace
falta un respaldo despersonalizado (o `pg_dump --schema-only`) del esquema del
primer parcial, restaurado en un cluster nuevo, y repetir el runner y las
suites. **No se activa produccion.**

### 12.7 Siguiente etapa

Etapa E: tenantizar ventas, pagos, compras, devoluciones y movimientos de stock
con SP nuevos (`sp_*_tenant`), reutilizando el patron de
`saas.sp_ajustar_inventario_tenant`; despues, bootstrap de usuarios via funcion
definer y endurecimiento de superadmin.

---

## 13. Etapa F (5BO) - Cierre del nucleo SaaS CU01-CU05, personal, RBAC y superadministrador

**Corte:** 31/10/2026 (UTC). **Ambito:** laboratorio desechable nuevo
`127.0.0.1:55445` / `fashionstore_lab_5bo`. **No se modificaron los laboratorios
E1 (`55443`) ni E2 (`55444`).**

### 13.1 Que se agrego

| Migracion | Contenido |
|---|---|
| `20261030000800_saas_personal_rbac_5bo.sql` | `public.sp_alta_personal_tenant` (SECURITY DEFINER, propietario `saas_personal_owner` NOLOGIN), `saas.aceptar_invitacion_personal` (invitacion de un solo uso), `saas.actor_actual()`, `saas.permiso_default`, `saas.permiso_tenant`, `saas.tiene_permiso_tenant`, excepcion auditada del trigger `fn_registrar_bitacora` |
| `20261030000900_saas_superadmin_hardening_5bo.sql` | `saas_admin.intento_auth`, `saas_admin.auditoria_operacion`, `estado_acceso`, `registrar_intento`, `auditar_operacion`, `listar_auditoria`, `limpiar_intentos` |

Endpoints nuevos (allowlist exacta; flags previos sin cambios):

- `POST /tenant/empresa/usuarios` - alta de personal con invitacion (201).
- `PATCH /tenant/empresa/usuarios/{id}/sucursal` - cambio de sucursal (200).
- `GET /tenant/empresa/permisos` - matriz RBAC efectiva (200).
- `PATCH /tenant/empresa/roles/{id}/permisos` - excepciones por empresa (200); ADMINISTRADOR 409.
- `POST /auth/tenant/invitaciones/aceptar` - consumo del token (200/400).
- `GET /saas/superadmin/claves` - identificadores vigentes (200).
- `GET /saas/superadmin/auditoria` - ultimos eventos globales (200).

### 13.2 Reglas de seguridad que ahora se cumplen

1. `public.usuario.rol_id` es **siempre CLIENTE** en el alta de personal; el rol
   empresarial vive solo en `usuario_empresa`. No hay escalada global.
2. El runtime tenant **no** recibe `INSERT/UPDATE` en `public.usuario` ni `USAGE`
   en `saas_admin`; el alta pasa por una funcion DEFINER con dueno NOLOGIN.
3. El actor se toma de la **asercion HMAC firmada** (`saas.actor_actual()`), no
   del GUC `app.usuario_id` (falseable).
4. Un correo ya registrado devuelve **409 neutro identico** en la propia empresa
   y en otra; nunca se vincula la cuenta existente.
5. `ADMINISTRADOR` no puede perder `PERSONAL/ADMINISTRAR` (matriz de solo
   lectura) y la empresa conserva al menos un administrador activo; bajo
   concurrencia el lock por empresa lo garantiza.
6. Superadmin: dos claves vigentes con `kid`, rate limit **en PostgreSQL**
   (compartido entre procesos) y auditoria sin secretos.

### 13.3 Numeros del corte

- Suite nueva `tests_saas/test_nucleo_saas_5bo.py`: **16 passed** (HTTP real
  sobre `uvicorn` + PostgreSQL).
- Barrido `pytest tests_saas -q` con E1/E2 aislados por variable de entorno
  (para no escribir en `55443/55444`): **1169 passed, 55 skipped, 3 warnings**.
- Reconciliacion con E2: `1207 = 1153 + 54` (5BM+5BN) y `1169 = 1153 + 16`; los
  54 tests de E1/E2 quedaron omitidos por decision explicita de no tocar esos
  laboratorios. **Cero regresiones en lo ejecutado.**

### 13.4 Dictamen

| Nivel | Estado |
|---|---|
| Laboratorio (nucleo CU01-CU05) | **VALIDADA** |
| Staging real | **BLOQUEADA** (esquema historico, backup/restore, migraciones demo, PG17) |
| Produccion | **BLOQUEADA** |

Ver `database/saas/82_matriz_preparacion_activacion_5bo.md`,
`database/saas/83_matriz_migraciones_lab_vs_produccion_5bo.json` y el checklist
versionado `database/saas/84_checklist_despliegue_5bo.md` (`5BO-F.1`, bloqueado).

### 13.5 Como operar SOLO el laboratorio 5BO

```powershell
python database/saas/78_laboratorio_operativo_5bo.py --status
python database/saas/78_laboratorio_operativo_5bo.py --stop
python database/saas/78_laboratorio_operativo_5bo.py --recrear-base --confirm-desechable
```

No detiene ni recrea `55443` (E1) ni `55444` (E2).

---

## 14. Apéndice 5BP / Etapa G — preactivación: PG17, migraciones controladas y respaldo

**Corte:** 31/10/2026. **Alcance:** backend. **Sin commit, sin push, sin producción.**
Se conservan intactas las secciones 1–13.

### 14.1 Qué cambió en esta etapa

| Tema | Antes (F/5BO) | Ahora (G/5BP) |
|---|---|---|
| Migraciones | `supabase db push` PROHIBIDO; lista informativa en 83 | **Manifiesto ejecutable** `85_...json` con orden, SHA-256, exclusiones y registro en `saas_migracion.version_aplicada` |
| Motor | PostgreSQL 18.6 | **PostgreSQL 17.7 real**, cluster nuevo en `127.0.0.1:55446` |
| Esquema histórico | 40/41 tablas, 14/27 triggers | **Sin cambio**: no hay catálogo autorizado; el comparador queda `BLOQUEADO (B1)` |
| Respaldo | No probado | `pg_dump -Fc` + `pg_restore` + cotejo: **0 diferencias** en laboratorio |
| Invitaciones | Token de 256 bits sin límite por origen | **Rate limit compartido en PostgreSQL** por IP y huella parcial de token (429 + `Retry-After`) |

### 14.2 Artefactos nuevos

- `database/saas/85_manifiesto_migracion_controlada_5bp.json`
- `database/saas/86_preflight_migracion_pg17_5bp.py`
- `database/saas/87_comparar_catalogo_historico_5bp.py`
- `database/saas/88_ensayo_backup_restore_5bp.py`
- `database/saas/89_runbook_staging_y_retorno_5bp.md`
- `database/saas/90_laboratorio_pg17_5bp.py`
- `database/saas/91_guia_secretos_staging_5bp.md`
- `supabase/migrations/20261030001000_saas_invitaciones_rate_limit_5bp.sql`
- `app/core/saas_invitation_rate_limit.py`
- `tests_saas/test_preparacion_activacion_5bp.py`
- `tests_saas/test_pg17_activacion_5bp.py`

### 14.3 Operación del laboratorio 5BP

```powershell
python database/saas/90_laboratorio_pg17_5bp.py --status
python database/saas/90_laboratorio_pg17_5bp.py --evidencia
python database/saas/90_laboratorio_pg17_5bp.py --stop
```

No detiene ni recrea E1 (`55443`), E2 (`55444`) ni F (`55445`).

### 14.4 Dictamen

| Nivel | Estado |
|---|---|
| Candidato para staging | **PARCIAL** (pipeline, PG17, backup/restore de herramienta y B9 validados; B1 abierto) |
| Staging real | **BLOQUEADO** (falta esquema histórico autorizado y copia despersonalizada) |
| Producción | **BLOQUEADA** |

Ver `REPORTE_SAAS_5BP_ETAPA_G_*.md` en `database/saas/evidencias/`.

