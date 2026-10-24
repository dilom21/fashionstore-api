# Bloque 3 / Paso 5Y — Contrato previo a instancia nativa PostgreSQL 18 aislada

**Estado: 5Y PREPARADA; pendiente de pruebas offline Windows.** Solo en `feat/saas-multitenant-parcial2`. **Paso 5 EN PROCESO, Bloque 3 4/7**. Mantener `SAAS_AUTH_ENABLED=false`, el manifiesto 5U no desplegable y los **7 bloqueantes** del contrato RLS 5Q.

## 1. Evidencia que sí tenemos

El estudiante confirmó en 5X **`574 passed, 2 warnings in 7.21s`**, cero fallos. El wrapper `28_auditar_identidad_cluster_fuente_5x.ps1` consultó metadatos de `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h` bajo transacción READ ONLY y `ROLLBACK`; PostgreSQL 18, 46 tablas, 48 FK tenant, cero políticas críticas. El **`system_identifier` del clúster original fue medido en un archivo TEMP LOCAL**, y NO se versiona su valor. El PGDATA fuente no fue publicado. El dictamen: `ORIGEN_MEDIDO_DESTINO_AUN_INEXISTENTE`.

5V había encontrado `initdb.exe`/`pg_ctl.exe`/`pg_config.exe` PostgreSQL 18 y 5W diagnosticó: la ruta futura `C:\SI2_Parcial1\fashionstore-rls-lab-5v-data` no existe, sí existe su carpeta padre, sin *reparse points* observados, ni solapamiento textual con proyecto/binarios. Windows reconoció un servicio PostgreSQL, pero no se ha identificado de forma canónica todo el `PGDATA` fuente. Se observaron cero listeners en el puerto 55432 **solo en el instante del diagnóstico**.

**Todavía no existe ningún segundo clúster PostgreSQL y ninguno de los 14 casos de pruebas A/B se ha ejecutado.**

## 2. Diseño 5Y: plan cerrado, NO un instalador

- `database/saas/41_b3_p5y_instancia_nativa_NO_EJECUTAR.json`: manifiesto `PLAN_NO_EJECUTAR` con `ejecucion_autorizada=false`, `entorno_autorizado=false`, `operaciones_sql_autorizadas=false` y `limpieza_autorizada=false`. Conserva como referencia **solo nombres de rutas y bases, no secretos ni ID real de clúster**. Destino PROYECTADO `127.0.0.1:55432` y PGDATA `C:\SI2_Parcial1\fashionstore-rls-lab-5v-data`, sin crearlos.
- `database/saas/29_validar_plan_instancia_nativa_5y.py`: verificador **totalmente offline**. Comprueba puertos, rutas, SCRAM-SHA-256, restricciones de red, compuertas pendientes y flags desactivados. Ni importa la aplicación ni crea motor SQL, proceso, archivo o conexión. Rechaza marcar el plan autorizado o completado sin ejecución posterior aprobada.
- `tests_saas/test_plan_instancia_pg18_5y.py`: **14 pruebas unitarias del CONTRATO (no las 14 pruebas A/B reales)**, no las 14 pruebas reales de aislamiento A/B aún pendientes. Son pruebas mutacionales sobre JSON, sin PostgreSQL. Suite esperada **`588 passed`** = 574 anteriores +14 nuevas; **no confirmado aún en Windows**.

## 3. Diez compuertas de una ejecución FUTURA

| Compuerta | Condición para poder continuar |
|---|---|
| G00 | Autorización explícita y separada del usuario para inicializar un clúster nuevo |
| G01 | Confirmación de PGDATA nuevo, no creado, sin enlaces y fuera del clúster original |
| G02 | Puerto 55432 verificado de nuevo antes del inicio |
| G03 | Solo después de autorizar: inicialización nativa PostgreSQL 18, contraseña del superusuario **introducida interactivamente**, sin imprimirla ni almacenarla en repositorio |
| G04 | Revisar configuración: autenticación `scram-sha-256` (NO `trust` ni `md5`), escucha `127.0.0.1` y `pg_hba.conf` restrictivo para `127.0.0.1/32`; sin aceptar tráfico remoto |
| G05 | Arranque exclusivo de la instancia desechable, sin registrar servicio Windows global ni tocar el servicio 5432 |
| G06 | Comparar `pg_control_system().system_identifier` del nuevo clúster con el ID de origen obtenido en TEMP; IDs distintos son **necesarios pero no suficientes** para aislamiento de datos |
| G07 | Solicitar y recibir **otra autorización SQL** para esquema vacío, roles `LOGIN NOBYPASSRLS NOSUPERUSER` y membresías ficticias |
| G08 | Solo en el clúster desechable, revisar grants, revocaciones, policies y triggers; ningún acceso comercial a `usuario.password_hash` ni `EXECUTE PUBLIC` de SP legacy |
| G09 | Ejecutar realmente los **14 casos A/B** (consulta propia, ajena, `WITH CHECK`, cambio adversarial de GUC, bitácora, integridad, rollback, concurrencia). Documentar también un plan de limpieza, con consentimiento separado para eliminar el laboratorio |

**Reglas no negociables:** Cada cambio futuro requiere **autorización explícita y separada** para su alcance. Nunca ejecutar `SET ROLE` desde `postgres` del servidor original para simular autenticación SQL de tenant. No importar clientes, contraseñas, ventas, inventario histórico ni filas privadas. No copiar `PGDATA` físico; si en un futuro se necesita esquema, debe partir **exclusivamente del sandbox vacío** por una operación `--schema-only` revisada y expresamente autorizada. No crear roles en el clúster 5432; PostgreSQL gestiona roles a nivel de clúster. No comenzar pruebas reales con una GUC editable como identidad independiente de SQL.

## 4. Fundamento en documentación de PostgreSQL

La [documentación oficial de `initdb` de PostgreSQL 18](https://www.postgresql.org/docs/18/app-initdb.html) indica que `initdb` crea un clúster nuevo y su directorio; por defecto la autenticación local puede usar `trust` si no se especifica un método. Para el futuro laboratorio se exige una contraseña de superusuario ingresada sin eco y `scram-sha-256` desde la inicialización. [PostgreSQL 18: Password Authentication](https://www.postgresql.org/docs/18/auth-password.html) explica SCRAM. [`pg_hba.conf`](https://www.postgresql.org/docs/18/auth-pg-hba-conf.html) regula qué conexiones se autentican. Un puerto diferente sin una ruta/identidad de clúster diferentes **no garantiza independencia**.

**No hay scripts `initdb`, `pg_ctl`, `pg_hba` o DDL ejecutables nuevos en 5Y**. La configuración mencionada arriba es un contrato para preparar un ensayo posterior, no una instalación completada.

## 5. Validación solicitada AHORA (sin PostgreSQL)

En Windows PowerShell, Conda `fashionstore-api`, dentro de `C:\SI2_Parcial1\fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Esperado:** `588 passed` (574 +14), por confirmar localmente. Si la suite pasa, ejecutar **solo**:

```powershell
python .\database\saas\29_validar_plan_instancia_nativa_5y.py
```

Salida esperada:

```text
5Y REVISION OFFLINE - NO EJECUTA initdb/pg_ctl NI SQL
5Y PLAN CONSISTENTE: 10 compuertas, 14 pruebas A/B futuras
5Y SEGURIDAD: SCRAM-SHA-256, loopback, sin trust, sin secretos
5Y BLOQUEADO: autorizacion explicita e instancia independiente pendientes
5Y NO CERTIFICA RLS - 7 bloqueantes abiertos
```

**Esta salida bloqueada es un éxito del validador, NO una autorización para instalar PostgreSQL.** No se necesita contraseña de PostgreSQL en 5Y; no ejecutar manualmente `initdb`, `pg_ctl`, crear roles, tocar el servicio PostgreSQL 5432, escribir `pg_hba.conf` ni crear datos.

## 6. Próxima decisión

**5Y solo podrá cerrarse como plan y pruebas offline** después de recibir las dos salidas anteriores. En ese momento corresponderá pedir una respuesta explícita distinta de `siguiente` si el usuario desea **autorizar la creación de la segunda instancia nativa**, restringida a la ruta y puerto propuestos y sin DDL de negocio. Si autoriza, la siguiente fase debe ser un ensayo cuidadosamente delimitado, con plan para abortar sin dañar nada existente y evidencia de `system_identifier` distinto. La autorización de clúster **no incluye** autorización de roles, ACL, policies, creación de tenants, seed ni eliminación posterior.

**Estado persistente:** `SAAS_AUTH_ENABLED=false`; **7 bloqueantes** abiertos; 14 casos A/B sin ejecutar; Paso 5 EN PROCESO y **Bloque 3 4/7**. `main`, Supabase, PostgreSQL original, sandbox y restaurada sin cambios.
