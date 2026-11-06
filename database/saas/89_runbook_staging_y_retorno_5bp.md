# RUNBOOK 5BP — Ensayo de staging y retorno controlado

**Versión:** `5BP-G.1`
**Ámbito:** backend SaaS FashionStore (núcleo CU01–CU05 + migración 5BP).
**Estado del documento:** procedimiento **ensayado en laboratorio PostgreSQL 17.7
desechable**. No es un SLA de producción ni autoriza por sí mismo una ventana real.
**Fecha:** 31/10/2026.

---

## 1. Qué resuelve y qué no

| Resuelve | No resuelve |
|---|---|
| Orden determinista de migraciones, sin seeds demo, con checksums | La falta del esquema histórico autorizado (B1) |
| Ensayo real de `pg_dump -Fc` + `pg_restore` + cotejo de inventario | Un respaldo productivo: el origen del ensayo es sintético |
| Retorno por *flags OFF* + restauración de snapshot | Reversión automática de DDL destructivo |
| Criterios de aborto antes del primer DDL | Estimación de downtime productivo |

El ensayo de respaldo/restauración se etiqueta **VALIDACIÓN DE HERRAMIENTA**.
Solo se convierte en `RESTAURACIÓN PRODUCTIVA VALIDADA` cuando el origen es una
copia autorizada y despersonalizada del sistema real, restaurada y cotejada.

---

## 2. Insumos obligatorios antes de tocar staging

1. **Manifiesto** `database/saas/85_manifiesto_migracion_controlada_5bp.json`
   con `estado = CANDIDATO_LAB` y `aplicable_a_produccion = false`.
2. **Esquema histórico autorizado** (`pg_dump --schema-only` + catálogo 80/81),
   guardado fuera de Git. Sin esto, **B1 sigue abierto** y el plan no debe
   ejecutarse contra una copia real.
3. **Secretos** aprovisionados por gestor externo (ver
   `database/saas/91_guia_secretos_staging_5bp.md`). Nunca en `.env` versionado,
   ni en el manifiesto, ni en evidencia.
4. **Copia no productiva** con marcador de descarte en el nombre.
5. **Responsable de la ventana** y criterio de aborto acordados por escrito.

---

## 3. Preflight (sin conexión, sin escritura)

```powershell
python database/saas/86_preflight_migracion_pg17_5bp.py --plan
python database/saas/86_preflight_migracion_pg17_5bp.py --check-local
```

Criterios de aceptación:

- `VALIDACION ESTATICA: OK`.
- `sin_clasificar` vacío: **toda** migración de `supabase/migrations` está
  incluida en una fase o excluida con motivo.
- `20261023000400_cuatro_tenants_demo_local.sql` aparece **solo** como exclusión
  `datos_demo`.
- Ningún checksum divergente.

Si el preflight falla, **se detiene el procedimiento**. No hay reintento
automático ni "modo experto".

---

## 4. Punto de corte y ventana

1. **Congelar escrituras** de la aplicación (modo mantenimiento) o usar una copia
   restaurada, nunca migrar en caliente sobre el origen.
2. **Registrar el corte**: timestamp UTC, versión del código (`git rev-parse HEAD`),
   versión del manifiesto y checksums aplicados.
3. **Verificar que no hay sesiones largas** bloqueando DDL:
   ```sql
   SELECT pid, state, wait_event_type, now() - xact_start AS duracion
     FROM pg_stat_activity
    WHERE state <> 'idle' AND xact_start IS NOT NULL
    ORDER BY duracion DESC;
   ```
4. Declarar la ventana con duración conocida. En el laboratorio, la aplicación
   completa de las 15 fases midió **≈ 3,4 s** (PostgreSQL 17.7, base pequeña,
   loopback). **No extrapolar** ese número a producción.

---

## 5. Respaldo antes del cambio

```powershell
# La contraseña va en variable de entorno, nunca en la línea de comandos.
$env:FASHIONSTORE_LAB_5BP_DB_PASSWORD = '<desde gestor>'
python database/saas/88_ensayo_backup_restore_5bp.py --ensayo `
    --puerto 55446 --base fashionstore_lab_pg17_5bp --confirm-desechable `
    --salida <fuera_del_repositorio>\respaldo_5bp.json
```

El comando hace, en orden y sin intervención:

1. inventario de origen (tablas, triggers, rutinas con dueño/`SECURITY DEFINER`/
   `search_path`, políticas, secuencias, extensiones y **conteo exacto de filas**);
2. `pg_dump -Fc`;
3. `pg_restore -l` (prueba de legibilidad del archivo);
4. creación de una base **nueva** `fashionstore_restore_5bp_<sello>`;
5. `pg_restore --exit-on-error` **con** propietarios y ACL;
6. cotejo de inventario y eliminación de la base temporal (el dump se conserva).

Reglas duras del runner:

- Solo `127.0.0.1` y puertos permitidos; `5432`, `55432`, `55433`, `55440`,
  `55441`, `55442`, **`55443` (E1)**, **`55444` (E2)**, **`55445` (F)** están
  prohibidos.
- El nombre de la base origen debe llevar marcador (`lab`, `desechable`,
  `staging_tmp`).
- Nunca restaura encima de una base existente.
- El dump queda fuera del repositorio.

Resultado exigido: `diferencias: 0` y `sha256` registrado. Un `pg_dump` sin
`pg_restore` verificado **no cierra** B2.

---

## 6. Aplicación del pipeline

### 6.1 Laboratorio (único destino permitido hoy)

```powershell
python database/saas/90_laboratorio_pg17_5bp.py --init --confirm-desechable
python database/saas/90_laboratorio_pg17_5bp.py --status
```

### 6.2 Staging real (BLOQUEADO hasta cerrar B1/B2/B4)

Cuando exista copia autorizada, el procedimiento es el mismo runner con:

- `--base` con marcador de descarte,
- host de loopback o túnel explícitamente autorizado por el estudiante,
- manifiesto con `aplicable_a_staging_real = true` (hoy `false`).

El runner **no** acepta una URL de Supabase ni un DSN remoto: esa decisión exige
un artefacto de autorización separado, que no forma parte de esta etapa.

### 6.3 Lo que nunca se ejecuta

```powershell
# PROHIBIDO: arrastra el seed demo y no tiene orden ni checksums.
supabase db push
```

---

## 7. Validación posterior

| Comprobación | Consulta / comando | Esperado |
|---|---|---|
| Versión del motor | `SHOW server_version;` | `17.x` en el destino PG17 |
| Fases aplicadas | `SELECT fase, orden FROM saas_migracion.version_aplicada ORDER BY orden;` | 15 filas |
| Checksums | `SELECT fase, checksum FROM saas_migracion.version_aplicada;` | 64 hex por fase |
| Aislamiento | `has_table_privilege('fs_saas_runtime','public.usuario','INSERT')` | `false` |
| Plano global aislado | `SELECT count(*) FROM information_schema.role_table_grants WHERE grantee='saas_admin_runtime';` | `0` |
| RLS efectiva | `SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname='public' AND c.relforcerowsecurity;` | ≥ 31 |
| Funciones DEFINER | `pg_get_userbyid(proowner)` + `proconfig` | dueño `saas_personal_owner` y `search_path` fijo |
| B9 | `SELECT to_regclass('saas_admin.intento_invitacion');` | no nulo |
| Post-migración funcional | `database/saas/72_verificacion_post_migracion_saas_solo_lectura.sql` | sin hallazgos |

Pruebas automatizadas del corte:

```powershell
python -m pytest tests_saas/test_preparacion_activacion_5bp.py -q -p no:randomly
python -m pytest tests_saas/test_pg17_activacion_5bp.py -q -p no:randomly
```

---

## 8. Plan de retorno

### 8.1 Orden de acciones (de menor a mayor impacto)

1. **Apagar flags de producto** (`SAAS_VENTAS_ENABLED`, `SAAS_COMPRAS_ENABLED`,
   `SAAS_DEVOLUCIONES_ENABLED`, `SAAS_RESERVAS_ENABLED`,
   `SAAS_SUPERADMIN_ENABLED`, `SAAS_AUTH_ENABLED`). Con flags OFF el núcleo queda
   inerte y las rutas nuevas responden 503; las migraciones son **aditivas**, por
   lo que la base puede quedarse como está.
2. **Reiniciar el proceso** con la versión anterior del código.
3. **Restaurar el snapshot** solo si el paso 1–2 no basta y siempre en una base
   nueva con marcador, nunca encima de la base averiada.
4. **Revalidar** el inventario del snapshot restaurado contra el inventario
   tomado antes del cambio (mismo procedimiento del §5).

### 8.2 Puntos sin retorno (no se revierten con un script)

| Cambio | Por qué no es reversible automáticamente | Mitigación |
|---|---|---|
| `ALTER TABLE ... ADD COLUMN empresa_id` con backfill | El backfill escribió datos derivados | Snapshot previo; columnas se dejan (son aditivas) |
| `FORCE ROW LEVEL SECURITY` | Quitar RLS reabre el aislamiento | No se quita: se apagan flags |
| `REVOKE` de rutinas legacy | Reponer `EXECUTE` a PUBLIC reintroduce la escalada | No se repone; se corrige la ruta |
| Cambio de propietario de funciones | Alterar dueño puede dejar rutinas sin privilegios | Snapshot del catálogo (§5) |
| `DROP`/`TRUNCATE` (no usados en este manifiesto) | Pérdida de datos | No se ejecutan; si se requieren, ventana y snapshot obligatorios |

**Regla:** el retorno seguro es *flags OFF + snapshot verificado*. Ningún DDL de
este manifiesto promete reversibilidad automática.

### 8.3 Contingencia de propietarios y `SECURITY DEFINER`

Si una restauración deja funciones `SECURITY DEFINER` sin dueño válido o sin
`search_path`, la aplicación responde 503 (fail closed). Acciones:

1. Comparar el catálogo restaurado con el previo (`87_comparar_catalogo_historico_5bp.py`).
2. Reponer `ALTER FUNCTION ... OWNER TO <rol NOLOGIN>` según el catálogo previo.
3. Verificar `proconfig` con `search_path = pg_catalog, public, saas`.
4. **No** conceder `EXECUTE` a `PUBLIC` ni cambiar el dueño a un rol con LOGIN.

---

## 9. Condiciones de aborto (antes del primer DDL)

- Preflight estático fallido (checksum, clasificación, orden, demo).
- Motor distinto de PostgreSQL 17 en un destino declarado PG17.
- Base sin marcador de descarte o puerto prohibido.
- Respaldo sin `sha256` o sin `pg_restore` verificado.
- Catálogo histórico no disponible cuando el destino es una copia real.
- Diferencias de inventario distintas de cero tras la restauración.
- Cualquier fase que falle: el runner se detiene en esa fase y **no reintenta**.

---

## 10. Operación del laboratorio 5BP

```powershell
python database/saas/90_laboratorio_pg17_5bp.py --status
python database/saas/90_laboratorio_pg17_5bp.py --evidencia
python database/saas/90_laboratorio_pg17_5bp.py --stop
```

- Puerto `55446`, base `fashionstore_lab_pg17_5bp`, pgdata bajo
  `%LOCALAPPDATA%\Temp\opencode\fs_lab_5bp_pg17`.
- **No** detiene ni recrea E1 (`55443`), E2 (`55444`) ni F (`55445`).
- El manifiesto local y los secretos viven **fuera del repositorio**; no se
  versionan ni se comparten.

---

## 11. Lo que debe aportar el estudiante para pasar a staging real

1. `pg_dump --schema-only` **autorizado** del esquema histórico + catálogo 80/81.
2. Autorización escrita para restaurar una copia despersonalizada en un destino
   no productivo.
3. Credenciales de ese destino por gestor externo (no por chat, no por `.env`).
4. Decisión de ventana y responsable de la operación.

Mientras falte cualquiera de esos insumos, el dictamen es
**BLOQUEADO POR EVIDENCIA NO DISPONIBLE** para B1/B2 reales.
