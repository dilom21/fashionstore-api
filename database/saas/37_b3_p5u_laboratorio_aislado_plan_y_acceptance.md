# Bloque 3 / Paso 5U — Laboratorio aislado para demostrar RLS A/B (solo preparación)

**Estado: 5U PREPARADA, PRUEBAS LOCALES PENDIENTES.** Paso 5 comercial EN PROCESO, Bloque 3 **4/7**. Rama única `feat/saas-multitenant-parcial2`. Contrato en `BORRADOR_NO_EJECUTAR`, **`desplegable=false`** y **`iniciar_laboratorio_autorizado=false`**.

## 1. Por qué es necesario un clúster distinto

5T fue validada en Windows con **`514 passed, 2 warnings in 7.64s`**; el validador 5Q continuó declarando **8 recursos y 7 bloqueantes sin resolver**, RLS real no certificado. En 5S, el principal SQL actual `postgres` resultó `SUPERUSER/BYPASSRLS`, y `row_security_active()=false` en las 16 tablas auditadas, aunque todas tenían `relrowsecurity=true`. No se ha ejecutado aún un ensayo con rol PostgreSQL restringido.

El usuario administra un PostgreSQL 18 LOCAL en `127.0.0.1:5432` que hospeda, entre otras, `fashionstore_saas_reservas_sandbox_5h` **vacío** y la BD histórica restaurada `fashionstore_restore_20261023_193554_594`. **Un `CREATE ROLE` en el sandbox también crea el rol a nivel de todo ese clúster.** Por ello este diseño prioriza un **clúster desechable SEPARADO** antes de instalar roles de prueba, en lugar de reutilizar el servidor PostgreSQL actual.

Según la documentación de PostgreSQL 18, `pg_control_system()` expone el identificador del clúster `system_identifier`, asociado a la inicialización de la instalación, no a una BD individual. **Un puerto diferente o un nombre de BD distinto NO garantizan separación**. El futuro laboratorio necesita identidad de clúster realmente diferente y volumen/directorio de datos separado. Un clon físico como `pg_basebackup` puede conservar el identificador: debe rechazarse aunque funcione en otro puerto.

Referencias oficiales:
- https://www.postgresql.org/docs/18/functions-info.html — `pg_control_system()`, `system_identifier`.
- https://www.postgresql.org/docs/18/ddl-rowsecurity.html — reglas RLS, propietarios y `BYPASSRLS`.
- https://www.postgresql.org/docs/18/database-roles.html — roles compartidos a nivel clúster.

## 2. Estrategia propuesta para el futuro ensayo (NO ejecutada)

Elegir **UNA** alternativa después de confirmar disponibilidad y recibir autorización explícita para **iniciar infraestructura aislada y crear identidades allí**:

1. PostgreSQL 18 en un contenedor desechable con volumen de datos propio, puerto local diferenciado (por ejemplo **55432**, si está libre), sin montaje de directorios de datos del servidor original, sin acceso público a la red y sin claves de Supabase.
2. Una segunda instancia PostgreSQL 18 local creada por **initdb en un directorio nuevo totalmente ajeno** al `PGDATA` existente, proceso y puerto separado, cuya configuración se verifique ANTES de crear cualquier rol.

**No hay Docker Compose ni scripts `initdb`/destrucción ejecutables en 5U**. Nunca conectar el laboratorio a la BD restaurada, crear roles en `5432` ni ejecutar `SET ROLE` desde `postgres` como sustituto de credenciales SQL propias. El clúster fuente solo debe servir, en el futuro, para exportar un esquema desde el **sandbox vacío**, exclusivamente `--schema-only` y en modo de solo lectura; revisar el dump, extensiones, funciones, owners y ACL antes de una importación a un clúster nuevo. Ni siquiera esta exportación se realiza ahora.

**No cambiar o retirar la BD de origen**, sus roles, sus ACL ni las otras bases del equipo. La limpieza futura solo podrá destruir el volumen o directorio **identificado expresamente como del laboratorio**, tras inspeccionar la identidad de clúster y recibir aprobación de ese paso.

## 3. Archivos creados: contrato y validador sin efectos

- `database/saas/37_b3_p5u_laboratorio_rls_aislado_NO_EJECUTAR.json`: manifiesto NO DESPLEGABLE. Fuente exclusiva `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h`, destino futuro propuesto `127.0.0.1:55432/fashionstore_rls_lab_5u` (**no iniciado**), identificadores `system_identifier=null` porque **no se han medido**; ocho condiciones no negociables, dos empresas inventadas, **14 casos de aceptación** y evidencia PostgreSQL **`NO_EJECUTADO`**.
- `database/saas/25_validar_laboratorio_aislado_5u.py`: **validador offline estándar Python**, sin red ni procesos externos. Comprueba que nadie declare listo el laboratorio, cambie el origen por la BD histórica, use puerto 5432 como destino, exporte datos, invente identificadores de clúster, o altere expectativas de denegación A/B. `comparar_identidad_clusters_5u(id_origen,id_destino)` acepta **solo enteros positivos y distintos** como condición necesaria, **nunca suficiente** para aislar empresas. **No conecta, no consulta ni crea clústeres**.
- `tests_saas/test_laboratorio_aislado_rls_5u.py`: **18 tests unitarios totalmente offline**, con mutaciones inseguras deliberadas del JSON. Rechazan coincidencia de clúster, roles SQL fingidos, acceso a empresa B, falsificación GUC, divulgación de hashes y pruebas marcadas como ejecutadas sin evidencia. Nunca abren PostgreSQL.

Suite previa **514 passed**. **Total esperado: `532 passed`** (=514 +18), aún no validado en Windows.

## 4. Los 14 casos A/B que un laboratorio deberá superar de verdad

| ID | Principal SQL conectado | Acción | Resultado requerido |
|---|---|---|---|
| A01 | A | SELECT de carrito propio A | PERMITIR |
| A02 | A | SELECT carrito ajeno B | DENEGAR (ninguna fila) |
| A03 | B | SELECT carrito ajeno A | DENEGAR (ninguna fila) |
| A04 | B | SELECT carrito propio B | PERMITIR |
| A05 | A | INSERT reserva de empresa B | RECHAZAR mediante WITH CHECK |
| A06 | B | UPDATE inventario de empresa A | RECHAZAR sin modificaciones |
| A07 | A | Alterar `app.empresa_id` para B e intentar SELECT B | DENEGAR (probar que no depende solo de GUC) |
| A08 | B | Falsificar `app.usuario_id` y efectuar CALL | RECHAZAR identidad SQL falsificada |
| A09 | A | SELECT `usuario.password_hash` | RECHAZAR permiso |
| A10 | A | CALL SP de reserva legacy | RECHAZAR EXECUTE |
| A11 | A | Reserva con error a mitad de transacción | ROLLBACK íntegro |
| A12 | B | Consultar bitácora exclusiva de A | DENEGAR (ninguna fila) |
| A13 | A | Solicitudes de reserva concurrentes al mismo artículo | SIN DOBLE RESERVA ni sobreventa |
| A14 | A | SELECT inventario propio A | PERMITIR |

**Regla de validez:** para futuros casos usar dos tenants y usuarios estrictamente **ficticios**, conexión real con `LOGIN NOSUPERUSER NOBYPASSRLS` y sin herencia ni propiedad de tablas. Un simple `row_security_active()=true` o la existencia de una policy `USING(true)` NO certifican nada: comprobar con consultas A/B y `WITH CHECK` del motor real. Un principal único que solo use `current_setting('app.empresa_id')` editable **fallará la exigencia A07** ante SQL arbitrario. El mecanismo futuro de autenticación SQL por empresa requiere diseño separado; no está implementado ni aprobado.

El examen solicita aislamiento real en backend y al menos dos empresas independientes. La suite estática no sustituye esas evidencias de funcionamiento.

## 5. Comprobaciones locales de esta subfase (SIN PostgreSQL)

Desde `C:\SI2_Parcial1\fashionstore-api`, conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Esperado `532 passed` (aún no confirmado)**. Solo si se aprueba:

```powershell
python .\database\saas\25_validar_laboratorio_aislado_5u.py
```

Salida esperada (el estado bloqueado es intencional):

```text
5U COMPROBACION OFFLINE - NO INICIA CLUSTER NI CREA ROLES
5U MATRIZ CONSISTENTE: 14 casos A/B, 2 empresas ficticias
5U AISLAMIENTO DE CLUSTER PENDIENTE: system_identifier sin medir
5U NO EJECUTABLE: 7 bloqueantes y RLS A/B NO CERTIFICADO
```

**En 5U NO se requieren contraseñas, Docker, initdb, psql, dump, roles, policies, GRANT, seed, CALL ni cambios de BD.** No ejecutar manualmente esas operaciones por este documento.

## 6. Criterio de cierre y pasos siguientes

**5U solo podrá cerrarse como planificación de un laboratorio independiente y validación offline** cuando se confirmen `532 passed` y la salida del validador. Ni siquiera ese resultado permite certificar RLS real ni modifica el estado de los **siete bloqueos pendientes** del contrato 5Q. **Paso 5 comercial EN PROCESO, Bloque 3 4/7; `SAAS_AUTH_ENABLED=false`.** El próximo paso, 5V, requiere la orden `siguiente` y, **por separado**, aprobación específica antes de crear/arrancar un servidor PostgreSQL o ejecutar DDL incluso en un laboratorio nuevo. No avanzar a Paso 6 ni habilitar reservas/ventas/pagos.

**Rama `main`, Supabase, sandbox local y BD restaurada:** sin cambios en esta subfase.
