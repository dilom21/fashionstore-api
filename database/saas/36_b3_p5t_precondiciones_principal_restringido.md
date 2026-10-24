# Bloque 3 — Paso 5T: precondiciones de principal PostgreSQL restringido

**Estado:** 5T VALIDADA Y CERRADA exclusivamente como preflight de código y pruebas unitarias simuladas; no se ha ensayado un rol SQL real. El Paso 5 comercial sigue EN PROCESO y el Bloque 3 sigue **4/7**. Rama exclusiva: `feat/saas-multitenant-parcial2`.

## 1. Evidencia de partida y por qué todavía NO crear roles

5S confirmó `498 passed, 2 warnings in 6.86s` y auditoría PostgreSQL READ ONLY con `5S PREFLIGHT OK`, 46 tablas vacías, 48 FK tenant validadas, ninguna política en las 16 tablas críticas examinadas y cero SP tenant instalados. En las 16 tablas `relrowsecurity=true`, pero `row_security_active()=false` para `postgres`, que tiene `SUPERUSER` y `BYPASSRLS`. Es una medición del **principal privilegiado**, no una prueba de aislamiento entre empresas.

Según PostgreSQL, los superusuarios y los roles `BYPASSRLS` siempre eluden RLS; un propietario de tabla normalmente también puede eludirlo. Los roles PostgreSQL son **globales al clúster**, incluso si `CREATE ROLE` se emite estando conectado al sandbox:

- https://www.postgresql.org/docs/18/ddl-rowsecurity.html
- https://www.postgresql.org/docs/18/sql-createrole.html
- https://www.postgresql.org/docs/18/functions-info.html

**Decisión:** no usar `SET ROLE` desde `postgres` para fingir una conexión segura. Debemos distinguir **`session_user` (principal de conexión)** de **`current_user` (principal efectivo)**: si la conexión autenticada sigue siendo un superusuario, éste puede recuperar sus privilegios. El próximo ensayo tendrá que utilizar una credencial local creada específicamente para pruebas, bajo autorización explícita y revisión de acceso del clúster.

## 2. Entrega 5T: evaluación preventiva, no despliegue

Se añadió el módulo **`app/core/saas_principal_preflight_5t.py`**. Define:

- `_SQL_PRECONDICIONES_5T`: un SELECT fijo sobre `pg_class`, `pg_namespace`, `pg_roles`, `pg_policies`, `row_security_active` y `pg_has_role`. Consulta solamente **metadatos**, nunca registros de clientes, contraseñas, inventario o ventas. No cambia la sesión ni ejecuta DDL/DML.
- `_evaluar_filas_5t(filas)`: evaluador puro que falla cerrado ante faltantes, NULL o datos contradictorios. Revisa **cuatro tablas críticas** del sandbox: `carrito`, `reserva`, `inventario`, `bitacora`.
- `evaluar_precondiciones_rls_5t(db)`: ejecuta la consulta de catálogo **solo cuando se llama expresamente pasando una sesión existente**. Devuelve `PrecondicionesRls5T(apto_para_ensayo, motivos)`; **no abre conexiones, no se ejecuta al importar, no se invoca desde endpoints, startup ni dependencias de autenticación en 5T**. Ante un error devuelve una evaluación negativa sin filtrar errores SQL.

Para que el dictamen sea `apto_para_ensayo=true`, se exigen conjuntamente:

| Condición requerida | Cómo se comprueba |
|---|---|
| Sandbox correcto | `current_database()=fashionstore_saas_reservas_sandbox_5h` |
| Principal autenticado y efectivo coinciden | `session_user=current_user` |
| Ninguno es superusuario/BYPASSRLS | Flags `rolsuper=false`, `rolbypassrls=false` para login y principal |
| No es propietario o heredero del propietario | `propietario != current_user` y `pg_has_role(...,'USAGE')=false` para las cuatro tablas |
| RLS aplicado realmente | `relrowsecurity=true` y `row_security_active(c.oid)=true` en las cuatro |
| Existen políticas | Al menos una policy por tabla examinada (no demuestra su corrección) |
| Metadatos completos | Cuatro tablas distintas, sin NULLs contradictorios ni inconsistencias |

**Importante:** incluso un `apto_para_ensayo=true` **NO declara seguro el SaaS**. Un `USING (true)` podría pasar la comprobación de existencia de policies sin aislar nada; falta verificar políticas reales y acceso entre empresa A y B, `WITH CHECK`, permisos sobre hashes y herencia, el mecanismo de identidad SQL y las transacciones. Una conexión con GUC modificables tampoco autentica el tenant por sí sola.

**No hay enlace a login o rutas SaaS en 5T.** Con `SAAS_AUTH_ENABLED=false`, todo el comportamiento HTTP actual permanece inalterado; la función de preflight está preparada como una barrera necesaria para ensayos futuros, pero todavía no se usa como gate operativo.

## 3. Pruebas nuevas y alcance estricto

`tests_saas/test_preflight_principal_restringido_5t.py` agrega **16 pruebas unitarias offline**: escenario ficticio restringido, sin tablas, faltante y duplicado, base ajena, superusuario, bypass, `SET ROLE` desde superusuario, propietario, heredero de owner, RLS deshabilitado para el principal, ausencia de policies, NULL, una sesión simulada con SELECT de catálogo, excepción SQL y ausencia de efectos de importación o integración en endpoints.

**Suite anterior: 498 passed confirmados. Ahora se esperan `514 passed` (498 + 16)**; esto todavía NO está confirmado localmente. Los tests no conectan PostgreSQL, no crean roles y no alteran ningún dato.

## 4. Decisiones pendientes antes del ensayo real (5U o posterior)

**NO crear roles en 5T**; `siguiente` permite preparar esta subfase, no concede autorización para afectar el clúster.

Antes de crear una cuenta de ensayo deberán acordarse expresamente:

1. Nombre de rol único específico del ensayo y de tipo **LOGIN**, `NOSUPERUSER`, `NOBYPASSRLS`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT` y no propietario. Contraseña fuerte solo local, sin registrar secretos en Git o informes.
2. Autorización específica para **crear y luego retirar** ese rol global del clúster PostgreSQL 18 que hospeda otras bases. Verificar y preservar la base restaurada histórica y toda otra base existente, y preparar limpieza de dependencias sin tocar permisos de otros sistemas.
3. `CONNECT` exclusivamente al sandbox, `USAGE` del esquema controlado, políticas y `GRANT` de mínimo privilegio **solo dentro del sandbox**. Ningún acceso a `usuario.password_hash` por la conexión comercial. No depender de `postgres`/`SET ROLE` como prueba de aislamiento.
4. Datos **exclusivamente ficticios**, al menos empresas A/B, y pruebas negativas positivas de lectura/escritura, `WITH CHECK`, stock, bitácora, rollback, concurrencia y ejecución de SP. Antes de tocar una tabla hay que auditar FKs, triggers, ACL, roles y el diseño exacto de las policies de esta base.
5. Revocación/limpieza documentadas y ejecución idempotente o abortable ante cualquier fallo.

**No se han generado instrucciones DDL ejecutables ni políticas `USING/WITH CHECK` para aplicar.** Un rol `NOLOGIN` activado con `SET ROLE` desde `postgres` no satisfará este contrato.

Los **siete bloqueantes** del manifiesto 5Q permanecen abiertos: bootstrap bajo RLS, hash Argon2 y credencial, identidad DB del invocador, GUC falsificables, policies+grants, `EXECUTE PUBLIC` legacy e integración PostgreSQL real. Un preflight no certifica ninguna de estas barreras.

## 5. Validación local pedida ahora

En `C:\SI2_Parcial1\fashionstore-api`, ambiente Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
python .\database\saas\23_validar_diseno_acceso_5q.py
```

**Confirmado:** `514 passed, 2 warnings in 7.64s`, 0 fallos; validador `5Q MATRIZ CONSISTENTE: 8 recursos, 7 bloqueantes sin resolver`, `5Q BORRADOR NO DESPLEGABLE; RLS POSTGRESQL REAL NO VALIDADO`. No hay script SQL/PowerShell 5T para ejecutar: todas las pruebas de esta subfase usan metadatos simulados y no requieren contraseña.

Si pytest falla, compartir la salida completa. **No ejecutar `CREATE ROLE`, `CREATE POLICY`, `GRANT`, `REVOKE`, `CALL`, seed ni migraciones en esta subfase**.

## 6. Estado

**5T CERRADA en alcance de precondiciones Python y pruebas offline**, Paso 5 EN PROCESO, Bloque 3 4/7, `SAAS_AUTH_ENABLED=false`. No modificar `main`, Supabase, sandbox, ni `fashionstore_restore_20261023_193554_594`. No avanzar a 5U hasta confirmar pruebas y recibir un nuevo `siguiente`. **No interpretar el preflight como aislamiento ya implementado.**


## 7. Evidencia local y dictamen de cierre 5T (24 de octubre de 2026)

El estudiante ejecutó `git pull --ff-only origin feat/saas-multitenant-parcial2`, fast-forward **`329e799..7302e68`**, y después `python -m pytest -q tests_saas` en el entorno Windows Conda `fashionstore-api`: **`514 passed, 2 warnings in 7.64s`**, cero fallos. Son las 498 pruebas anteriores y las 16 de `tests_saas/test_preflight_principal_restringido_5t.py`. Las dos advertencias se deben a deprecaciones conocidas en Starlette/httpx y `HTTP_422_UNPROCESSABLE_ENTITY`.

La ejecución `python .\\database\\saas\\23_validar_diseno_acceso_5q.py` confirmó literalmente:

```text
5Q VALIDACION ESTATICA OFFLINE — NO APLICA SQL NI ROLES
5Q MATRIZ CONSISTENTE: 8 recursos, 7 bloqueantes sin resolver
5Q BORRADOR NO DESPLEGABLE; RLS POSTGRESQL REAL NO VALIDADO
```

**Dictamen técnico:** las pruebas unitarias de precondiciones del principal SQL fueron satisfactorias usando metadatos simulados. **No se ejecutó** `evaluar_precondiciones_rls_5t` contra PostgreSQL real; no se creó ningún rol SQL restringido y no se verificaron políticas, ACL, `USING/WITH CHECK` o aislamiento A/B. Un futuro resultado `apto_para_ensayo=true` no probaría aislamiento entre empresas: se necesitarían tests de acceso permitido/denegado con rol sin `BYPASSRLS`, datos exclusivamente ficticios y conexión autenticada como ese rol.

**5T VALIDADA Y CERRADA en alcance de código/tests offline. Los 7 bloqueantes del contrato 5Q siguen abiertos. Paso 5 EN PROCESO, Bloque 3 4/7.** No se autoriza ni ejecuta `CREATE ROLE` (afecta a todo el clúster), `CREATE POLICY`, `GRANT`, `REVOKE`, seed o `CALL`. Mantener `SAAS_AUTH_ENABLED=false`, sin modificar `main`, Supabase, sandbox ni base histórica restaurada. **Esperar un nuevo `siguiente`** antes de la subfase 5U; ninguna orden adicional al estudiante en 5T.
