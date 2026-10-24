# Bloque 3 / Paso 5F — Hallazgos comprobados en los tres SP de reserva del primer parcial

**Estado:** REVISIÓN 5F VALIDADA en el alcance solicitado: el estudiante obtuvo `280 passed, 2 warnings in 6.40s` y exportó con éxito, en una transacción `READ ONLY`, las definiciones completas de los tres SP CU16/CU18. El análisis del contenido recibido en el chat identifica puntos concretos antes de cualquier migración. **No se han ejecutado ni modificado SP o datos. Paso 5 global EN PROCESO.**

**Fuentes:** texto de `pg_get_functiondef` y metadatos extraídos de `127.0.0.1:5432 / fashionstore_restore_20261023_193554_594` el 23-10-2026, compartidos por el estudiante. La extracción terminó en `ROLLBACK`. Se resume la evidencia, sin subir los cuerpos SQL completos al repositorio.

## Resultado principal: los SP originales NO están preparados para escribir en SaaS

Los tres SP son `SECURITY INVOKER` (`security_definer=false`) y su propietario actual es `postgres`. Los tres tienen permiso `EXECUTE` a `PUBLIC` según los metadatos extraídos, y fijan `search_path` a `public,pg_temp`.

**Matiz de privilegios:** `EXECUTE` para `PUBLIC` amplía quién puede intentar invocarlos, pero **no prueba por sí mismo que un rol cualquiera pueda cambiar inventario**; en `SECURITY INVOKER` también importan los permisos efectivos del invocador, RLS, funciones auxiliares y los grants sobre tablas. Aun así, estos procedimientos no deben exponerse al servicio SaaS. Evaluar en otra migración, no aquí, revocación de permisos, roles acotados y una API de SP tenant-aware.

### A. `sp_crear_reserva_desde_carrito` — CU16

**Lo que sí conserva:** fecha de atención futura; `FOR UPDATE` sobre carrito e inventarios; carrito `ACTIVO`; cliente/sucursal existentes y activos; carrito no vacío; procesamiento de líneas ordenadas por `inventario_id`; cantidad positiva y `cantidad <= stock_actual-stock_reservado`; incrementa solo `stock_reservado`; inserta `reserva`, `detalle_reserva` y movimiento Kardex `RESERVA`; finalmente marca carrito `CONVERTIDO`. Sin `COMMIT` interno: transacción controlada por el llamador.

**Lo que NO verifica:** no aparece `empresa_id` en ninguna cláusula SELECT/INSERT/UPDATE; no comprueba que `cliente.usuario_id = p_usuario_id`; `p_usuario_id` se limita a la atribución del movimiento Kardex; no comprueba membresía ni contexto empresa del actor; `fecha_actualizacion` se selecciona, **pero no se utiliza para verificar las 2 horas**; no comprueba tenant/sucursal empresarial de cada nivel de la cadena inventario→variante→producto→categoría→temporada; no valida que producto/variante/categoría/talla/color estén activos antes de reservar; no selecciona `empresa_id` en las inserciones de `reserva`, `detalle_reserva` ni `movimiento_inventario`.

**Impacto de esquema:** Bloque 2 agregó `empresa_id` NOT NULL a tablas empresariales y retiró expresamente el DEFAULT temporal de migración. La ejecución del antiguo INSERT omitiendo `empresa_id` es incompatible con ese contrato salvo que algún trigger lo supla: **no se ha verificado ni debe suponerse la existencia de tal mecanismo**. Esto implica probable rechazo de la operación en la copia SaaS, además del defecto de aislamiento. Las FK compuestas no reemplazan autorización.

### B. `sp_cancelar_reserva` — CU16

**Lo que sí conserva:** bloquea reserva `FOR UPDATE`, exige estado `PENDIENTE` o `CONFIRMADA`, procesa detalles ordenados por inventario, bloquea inventario, evita reducir `stock_reservado` por debajo de la cantidad de la línea, registra movimientos `LIBERACION_RESERVA` y marca reserva `CANCELADA`, manteniendo observaciones.

**Lo que NO verifica:** `reserva.empresa_id`, propietario `cliente.usuario_id`, autorización de actor/empresa/sucursal, coherencia de `detalle_reserva.empresa_id` e `inventario.empresa_id`. El parámetro `p_usuario_id` se inserta en Kardex, pero no se usa para autorizar la cancelación. El `INSERT movimiento_inventario` omite `empresa_id`. Una reserva ajena alcanzable por ID queda condicionada solo a permisos externos/constraints, insuficiente como diseño de SP multiempresa.

### C. `sp_finalizar_reserva_sin_compra` — CU18

**Lo que sí conserva:** bloquea reserva e inventarios; exige `CONFIRMADA` y líneas presentes; libera únicamente `stock_reservado` después de comprobar disponibilidad reservada; registra `LIBERACION_RESERVA`; marca `ATENDIDA`, conserva observación original cuando corresponde.

**Lo que NO verifica:** empresa de reserva/inventario/detalles, rol de empleado ni sucursal autorizada, y `p_usuario_id` solo se utiliza para Kardex. Omite `empresa_id` al insertar movimientos. Al igual que en cancelación, tampoco debe invocarse desde SaaS hasta aplicar validación tenant dentro de la transacción.

## Matriz de prioridad para una migración futura

| Prioridad | Requisito | Criterio de revisión |
|---|---|---|
| Bloqueante | Autorización de actor y empresa | Verificar `empresa_id`, `usuario_id`, perfil activo, pertenencia/permisos vigentes DENTRO de la operación |
| Bloqueante | DML con `empresa_id` | Todos los INSERT/UPDATE/joins de recursos empresariales validan/escriben tenant explícito |
| Bloqueante | Disponibilidad concurrente | Bloqueos ordenados por inventario, comprobación posterior al bloqueo; transacción atómica y rollback ante cualquier error |
| Alta | Estado y vigencia | Carrito ACTIVO y menos de 2 horas; cliente y sucursal activos, producto/variante/categoría/sucursal válidos |
| Alta | Cancelación / finalización | Solo actor autorizado, estados permitidos, una única liberación por inventario, Kardex trazable |
| Alta | Permisos SQL | Nuevos SP con `EXECUTE` solo para rol API acotado, revisar PUBLIC, RLS sin `BYPASSRLS`; no confiar en GUC como única credencial |
| Alta | Pruebas de integración | Ensayo PG aislado con roles reales, inventario simultáneo, dos reservas, reintentos, intentos cross-tenant y rollback de fallos |

**Diseño elegido:** no alterar los tres SP legacy ni cambiar código de reserva real del primer parcial. Preparar **tres procedimientos nuevos tenant-aware** en siguiente subfase, preservando CU16/CU18; inicialmente en archivos de borrador **NO ejecutables en producción** y ensayos aislados. Antes de la implementación real, inspeccionar triggers y restricciones vinculados con reservas/Kardex y grants efectivos, pues podrían añadir comportamiento no visible en el cuerpo del SP.

## Estado del proyecto tras evidencia 5F

- `280 passed, 2 warnings in 6.40s`, sin fallos (pruebas estáticas de 5F, sin ejercicios SQL reales).
- Exportación de las tres definiciones completada y validada; PostgreSQL intacto (`BEGIN READ ONLY` / `ROLLBACK`).
- Archivo SQL original extraído **no añadido al repositorio**.
- `POST /tenant/reservas/prevalidar` permanece informativo; `POST /tenant/reservas`, cancelación y finalización siguen cerrados con 503 mientras SaaS esté habilitado.
- `SAAS_AUTH_ENABLED=false` y `main`/Supabase intactos.
- **5F validada como auditoría y análisis de diseño; Paso 5 global todavía EN PROCESO (Bloque 3 4/7).** Detenerse aquí y esperar `siguiente`.
