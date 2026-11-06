# 5BT / Etapa K — Matriz de decisiones pendientes y plan exacto de ejecución

**Sello:** 20261031_180047
**Estado:** `R1 = INDETERMINADO` · `B2 = NO AUTORIZADO`
**Consecuencia:** no se ejecutó ninguna tarea dependiente de datos reales. Este documento **no autoriza nada**; describe qué falta y qué se ejecutará exactamente cuando exista autorización.

---

## 1. Matriz de decisiones

| ID | Decisión | Evidencia buscada | Resultado de la búsqueda | Estado | Bloquea |
|---|---|---|---|---|---|
| **R1** | A (cerrar Data API) o B (excepciones mínimas nombradas) | `107_acta_decision_5bs.md` §Decisión 1; bloque `DECISIÓN R1:` firmado; cualquier acta nueva | **Confirmada el 31/10/2026** por el propietario: `DECISIÓN R1: A — CONFIRMADA`, transcrita en `107` con atribución humana | **DECIDIDA Y CONFIRMADA: A** · verificación PostgREST pendiente | GO_STAGING, cualquier `GRANT/REVOKE` remoto |
| **B2** | Autorizar `pg_dump` con datos reales del proyecto Supabase | Bloque `AUTORIZACIÓN B2` de `104_solicitud_autorizacion_datos_5bs.md` firmado | Bloque con marcadores `<texto>` **sin rellenar**; ninguna firma, ventana, custodio ni TTL declarados | **NO AUTORIZADO** | B2 real, restore real, punto de retorno, GO_PRODUCCION |

> Los archivos `MASTER_PROMPT_*` y `CONTEXTO_*` **no constituyen autorización** (lo declara el propio `MASTER_PROMPT` §P0.3). La elección R1 no puede deducirse del inventario de consumidores: el inventario es evidencia técnica, no una decisión.

---

## 2. Texto exacto que debe existir para desbloquear

### 2.1 R1

```
DECISIÓN R1: <A | B>
Si B: cliente(s) autorizado(s): <...>
Operación(es) autorizada(s): <...>
Responsable / fecha: <...>
```

Guardar como archivo nuevo (sugerido): `database/saas/110_acta_r1_b2_firmada_5bt.md` **o** rellenar `107` con firma. Sin ese texto, `R1` no pasa de `INDETERMINADO`.

> **Actualización Etapa L (5BU):** el propietario registró `DECISIÓN R1: A` el 2026-10-31 y quedó transcrita en `107` con atribución humana explícita. **Confirmación expresa del 31/10/2026:** `DECISIÓN R1: A — CONFIRMADA`, con límites explícitos (sin cambios de permisos, migraciones ni despliegues). B2 sigue **NO AUTORIZADO**.

### 2.2 B2 (formato de `104` §6)

```
AUTORIZACIÓN B2 — FashionStore SaaS
Autorizo / No autorizo: <texto>
Alcance autorizado: <datos + estructura | solo estructura>
Rol de lectura autorizado: <nombre del rol>
Ventana: <inicio> a <fin>, zona horaria <...>
Custodio del respaldo: <nombre>
TTL del respaldo: <horas>
Medios de borrado adicionales: <lista o "ninguno">
Firma / fecha: <...>
```

Campos **imprescindibles** (si falta uno, B2 sigue NO AUTORIZADO): alcance limitado a `public` (o alcance explícito), rol de lectura, ventana, custodio, TTL, responsable y confirmación de que el solicitante controla los datos.

---

## 3. Plan exacto — B2 (SOLO ejecutable con el bloque §2.2 firmado)

Preflight ya verificado en K: `pg_dump 17.7` en `%LOCALAPPDATA%\Temp\opencode\fs_pg17_5bp\pg17\pgsql\bin`, 136.62 GB libres, puerto **55452** libre y reservado, sin copia real existente.

| # | Paso | Comando / control | Criterio de parada |
|---|---|---|---|
| B2-0 | Releer la autorización firmada y verificar alcance | comparar con `104`; registrar referencia en el manifiesto | si el alcance incluye `auth/storage/vault` sin permiso explícito → DETENER |
| B2-1 | Medir tamaño del origen | `psql "<DSN rol lectura>" -Atc "SELECT pg_database_size(current_database())"` | si 3× tamaño > espacio libre → DETENER |
| B2-2 | Crear destino privado | `%LOCALAPPDATA%\Temp\opencode\fs_b2_<sello>`; `icacls` herencia quitada, solo operador; fuera de OneDrive/Git | si la ruta está en el repo o sincronizada → DETENER |
| B2-3 | Dump de roles | `pg_dumpall --roles-only --host <host> --port 5432 --username <rol_admin> --file roles.sql` | error → DETENER, no reintentar a ciegas |
| B2-4 | Dump con datos, solo `public` | `pg_dump --format=custom --compress=6 --schema=public --host <host> --port 5432 --username <rol_lectura> --dbname postgres --file base.dump` | si aparece otro esquema en el dump → descartar |
| B2-5 | Cifrado autenticado | AES-256-CBC+PBKDF2 200k **+ MAC HMAC-SHA256 con clave independiente** (o AEAD); verificar MAC **antes** de descifrar | sin MAC/clave separada → no declarar integridad |
| B2-6 | Ida y vuelta | descifrar a temporal, comparar SHA-256 plano vs descifrado, borrar temporal y plano | hashes distintos → DETENER |
| B2-7 | Manifiesto sin secretos | `manifiesto_b2.json`: bytes, sha256 cifrado, ttl, custodio, referencia de autorización, conteos agregados | nunca incluir DSN, claves, correos ni muestras |
| B2-8 | Clúster destino aislado | PG17.7 nuevo, `initdb` propio, puerto **55452**, base `fashionstore_b2_restore_<sello>` con `TEMPLATE template0`, marcador `DISECHABLE_B2` | si el puerto/base ya existe o es un lab previo → DETENER |
| B2-9 | Restore | `pg_restore --exit-on-error -d <destino> base.dump` | primer `ERROR` → conservar base parcial, registrar y DETENER |
| B2-10 | Invariantes | tablas, triggers, rutinas, vistas, políticas RLS, `last_value` de secuencias, conteos por tabla crítica, ACL 0 para `anon/authenticated/service_role`, `EXECUTE PUBLIC` 0 | cualquier diferencia → B2 NO validado |
| B2-11 | Smoke de aplicación | FastAPI contra el clúster restaurado con roles restringidos y flags en `false`; luego `SAAS_AUTH_ENABLED=true` solo en staging | `InsufficientPrivilege`/`ConnectionTimeout` inesperado → DETENER |
| B2-12 | Retorno | restaurar el mismo `.enc` en una segunda base desechable, comparar, destruir | diferencias → punto de retorno NO acreditado |
| B2-13 | Borrado y acta | destruir clústeres, borrar `.enc`/`.pass` al vencer TTL, registrar fecha/hora/responsable, conservar solo el manifiesto sin secretos | sin registro de borrado → TTL incumplido |

**Prohibido en todo el flujo:** subir el `.enc` a Git/CI/chat/IA, restaurar sobre `fashionstore`, reutilizar 55441/55442/55446/55448/55449/55450/55451, escribir contraseñas en argumentos, usar `service_role` para el dump.

**Cambio de herramienta necesario antes de ejecutar:** `102_ensayo_backup_restore_ficticio_5bs.py` usa por defecto el puerto 55449 (hoy del laboratorio 5BM repro del runner 101). Para B2 real hay que pasar un puerto y una raíz nuevos (55452) o ejecutar el runbook `103` §3–§6 a mano. No se modificó el script en K.

---

## 4. Plan exacto — R1

### Si la decisión es **A** (cerrar Data API)

1. Congelar `01100` + `01200` (checksums ya verificados en K: `8def9b2e…`, `aeb5718b…`).
2. Aplicar en **staging** en el orden del manifiesto `108`; `01200` es la última y falla cerrado.
3. Verificar en staging con PostgREST real: `anon`, `authenticated`, `service_role` deben recibir **401/403** en `/rest/v1/<tabla>` y en `/rest/v1/rpc/es_principal_runtime`. Cualquier `200` → detener activación.
4. Confirmar que el backend sigue operando por TCP directo con `postgres` (no depende de la Data API) y que `fs_saas_runtime` conserva sus `EXECUTE`.
5. Solo después: autorización de despliegue separada para producción.

### Si la decisión es **B** (excepciones mínimas)

1. Nombrar cliente, tabla, operación y rol por cada excepción (no hay ninguna nombrada hoy).
2. Diseñar `GRANT` mínimo por objeto + política RLS + auditoría; nada global.
3. Probar cada excepción con PostgREST en un proyecto staging autorizado.
4. Documentar por qué la excepción no puede resolverse por FastAPI.

En ambos casos: **no** se ejecutó ni se ejecutará en K ningún `GRANT`/`REVOKE` remoto.

---

## 5. Dependencias que siguen abiertas (idénticas a J, verificadas hoy por `106`)

| # | Dependencia | Tipo | Estado K |
|---|---|---|---|
| 1 | B2 autorizado + respaldo real restaurado | Humana + técnica | **ABIERTA** |
| 2 | Contrato de consumidores (A o B) | Humana | **ABIERTA** |
| 3 | Secretos en gestor externo (`SAAS_TENANT_ASSERTION_KEY`, `SAAS_SUPERADMIN_KEY_SHA256`, `SAAS_ADMIN_DATABASE_URL`) | Humana/operativa | **ABIERTA** |
| 4 | Proyecto staging separado + ventana aprobada | Humana/operativa | **ABIERTA** |
| 5 | Decisión de coexistencia SaaS vs instancia legacy del primer parcial | Arquitectura | **ABIERTA** |
| 6 | Prueba PostgREST real en staging | Técnica (requiere staging) | **ABIERTA** |

---

## 6. Lo que ya está verificado y no requiere repetirse

| Verificación | Resultado K | Evidencia |
|---|---|---|
| Integridad `01200` / `01100` | SHA-256 coincide con J | `EVIDENCIA_K_PREFLIGHT_5BT_20261031_180047.txt` |
| Manifiesto `108` (17 fases, 01200 en orden 148) | `VALIDACION ESTATICA: OK` | salida de `86 --plan` |
| Simulacro de puerta staging | 0 bloqueos técnicos, 4 dependencias humanas | `EVIDENCIA_SIMULACRO_STAGING_5BS_20261031_175936.json` |
| Inventario de consumidores | 0 consumidores directos, 0 `SUPABASE_*`, 0 credenciales | `EVIDENCIA_CONSUMIDORES_5BS_20261031_175938.json` |
| Suite offline | 859 passed / 0 failed / 0 errors / 0 skipped (8.31 s) | `EVIDENCIA_JUNIT_5BS_offline_20261031_180000.xml` |
| Contratos de artefactos J | 18 passed | `pytest tests_saas\test_acl_superficies_5bs.py` |
| Clasificación de módulos | 46 OFFLINE / 7 reproducible / 26 pendiente | `EVIDENCIA_CLASIFICACION_REGRESION_5BS_20261031_175954.json` |

---

## 7. Orden de desbloqueo recomendado

1. R1 = **A** escrito (o B con excepciones nombradas).
2. B2 firmado con alcance `public`, rol, ventana, custodio y TTL.
3. Ejecutar §3 (B2) en ventana aprobada.
4. Crear proyecto staging separado y aprovisionar secretos.
5. Ejecutar `105` §1–§7 completo, incluida la prueba PostgREST.
6. Recién entonces evaluar `GO_STAGING`; `GO_PRODUCCION` exige además aprobación de despliegue independiente.
