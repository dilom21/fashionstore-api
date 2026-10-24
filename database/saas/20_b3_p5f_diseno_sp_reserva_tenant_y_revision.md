# Bloque 3 — Paso 5F: diseño transaccional de reservas e inspección completa de SP

**Estado:** revisión/diseño preparados. **No se han examinado aún los cuerpos completos en PostgreSQL local, no se ha creado un procedimiento tenant-aware ni se ha ejecutado DML**. El Paso 5 continúa EN PROCESO, no saltar al Paso 6.

**Alcance:** tres SP legacy de PostgreSQL: `sp_crear_reserva_desde_carrito` (CU16), `sp_cancelar_reserva` (CU16) y `sp_finalizar_reserva_sin_compra` (CU18). Rama exclusiva `feat/saas-multitenant-parcial2`. Mantener `SAAS_AUTH_ENABLED=false`, `main` sin cambios y Supabase intocable.

## 1. Evidencia ya verificada en 5E

El estudiante ejecutó **262 pruebas aprobadas** (2 advertencias deprecatorias, 6.11 s) y auditoría PostgreSQL **READ ONLY** sobre cinco empresas: 48 FK compuestas, 11 reservas en Vanter Men, 16 líneas reservadas y 3 unidades de stock reservado. Los otros cuatro tenants no tienen reservas.

La auditoría confirmó que los tres SP existen, incluyen texto `FOR UPDATE` y `stock_reservado`, pero **no tienen coincidencias literales con `empresa_id` ni `app.empresa_id`**. Esto es una bandera roja de seguridad, no una demostración concluyente: falta leer el SQL completo y cualquier función o trigger auxiliar. **Los SP existentes NO están autorizados para SaaS**. Tampoco se pueden reutilizar los endpoints de reserva legacy, pues filtran solo por ID sin empresa.

## 2. Extracción segura de las DEFINICIONES completas, no ejecución

Se agregaron dos archivos de revisión:

- `11_extraer_definiciones_reserva_solo_lectura.sql`: `BEGIN TRANSACTION READ ONLY`, guarda de base local, 5 empresas, 48 FK tenant y 3 procedimientos; consulta `pg_proc`/`pg_get_functiondef` de los tres, el propietario, `SECURITY DEFINER`, `search_path` declarado y concesión `EXECUTE` a PUBLIC; termina con `ROLLBACK`.
- `11_extraer_definiciones_reserva_solo_lectura.ps1`: usa únicamente `127.0.0.1:5432` y la base `fashionstore_restore_20261023_193554_594`, fija además `default_transaction_read_only=on`; exporta **texto de las definiciones** a `$env:TEMP\fashionstore_P5F_SP_reservas_LOCAL_REVISAR.txt`, **fuera de GitHub**. Pide contraseña de PostgreSQL local, no imprime ni guarda la contraseña.

El texto `CREATE [OR REPLACE] PROCEDURE` devuelto por `pg_get_functiondef` **NO se ejecuta**, solo se almacena como texto. **No ejecutar** manualmente estas definiciones exportadas ni intentar un `CALL`. El archivo debe revisarse antes de adjuntarlo al chat por si la definición contiene detalles confidenciales; **nunca subirlo a GitHub ni ejecutar su contenido**. Lo ideal es compartir el archivo revisado de texto como adjunto en el chat para poder estudiar el código SQL original sin inventarlo.

## 3. CONTRATO PROPUESTO (aún no implementado) para los nuevos SP SaaS

El diseño definitivo **depende del código completo del procedimiento original**. Estos puntos son requisitos de seguridad, no hechos confirmados acerca del SP heredado:

### 3.1 Identidad, autorización y entradas
- Preferir nuevas firmas explícitas `sp_crear_reserva_tenant(p_empresa_id bigint, p_carrito_id bigint, p_usuario_id bigint, p_fecha_atencion timestamptz, p_observacion text, INOUT p_reserva_id bigint)`, `sp_cancelar_reserva_tenant(...)`, `sp_finalizar_reserva_sin_compra_tenant(...)`. **Son nombres/prototipos propuestos, no SQL ejecutable ni existentes en la BD**. Orden y tipos concretos se decidirán tras revisar las definiciones locales.
- FastAPI debe derivar `empresa_id` y `usuario_id` exclusivamente del token validado + membresía vigente, nunca del JSON; enviar valores enlazados, no interpolar SQL.
- Dentro de la misma transacción PostgreSQL, comprobar tenant/actor antes de cualquier cambio: `carrito.empresa_id=p_empresa_id`, `cliente.empresa_id=p_empresa_id`, `cliente.usuario_id=p_usuario_id`, perfil activo; comprobar membresía/permisos de la empresa del actor si el rol requerido lo exige. Para el personal de sucursal, comprobar `empleado.empresa_id` y `empleado.sucursal_id` y permisos `usuario_empresa` relevantes; un usuario no adquiere privilegios por conocer IDs.
- Si se usa `current_setting('app.empresa_id', true)`, validar valor y coherencia frente al argumento `p_empresa_id` **pero no tratar un GUC modificable como la única autenticación**. Emplear rol de BD acotado, perfil/rol comprobado y RLS correctamente configurado.
- Error de recurso ajeno y ausente deben ser indistinguibles (404); no filtrar SQL, otros clientes, IDs externos ni detalles de FK.

### 3.2 Crear reserva: orden de operaciones atomizado
1. En la **misma transacción** y con rol autorizado, comprobar identidad y tenant del cliente. Bloquear fila de carrito con `FOR UPDATE` usando `id`, `cliente_id`, `empresa_id`.
2. Exigir carrito `ACTIVO`, no vencido (>2 horas), con líneas, sucursal `empresa_id` correcta, fecha de atención futura y ninguna reserva anterior del carrito (UNIQUE en `reserva.carrito_id` como defensa concurrente).
3. Seleccionar **todas** las líneas con `detalle_carrito.empresa_id=p_empresa_id`, junto con inventario, sucursal, variante, producto, categoría y temporada del mismo tenant; tallas/colores globales. Rechazar relaciones ausentes/inconsistentes en vez de ignorarlas silenciosamente.
4. Ordenar inventarios por ID ascendente y bloquearlos todos en **orden estable** (`FOR UPDATE`) antes de comprobar disponibilidad. Después de bloquear, **releer** `stock_actual`, `stock_reservado`, cantidades y estados; la prevalidación HTTP previa no es suficiente.
5. Validar cada `cantidad > 0` y `cantidad <= stock_actual-stock_reservado`; ante un solo fallo, rollback de **toda** la operación. Evitar deadlocks por orden estable también en cancelación/finalización.
6. Crear `reserva` y `detalle_reserva` con `empresa_id=p_empresa_id` explícito; actualizar SOLO el `stock_reservado` de inventarios del mismo tenant, comprobando número de filas actualizado y que `stock_actual >= stock_reservado`. Crear `movimiento_inventario` tipo `RESERVA` con tenant y actor correctos; conservar la semántica real del Kardex/triggers del primer parcial.
7. Marcar carrito `CONVERTIDO` solo cuando todas las inserciones/actualizaciones anteriores hayan triunfado. `stock_actual` no debe bajar al crear una reserva. Devolver nuevo ID solamente después de la operación completa.
8. Aplicación realiza **un único COMMIT** al finalizar la transacción. Sin commits dentro de SP ni pasos parcialmente persistidos. En excepción usar rollback total.

### 3.3 Cancelar reserva / finalizar sin compra
- Bloquear y verificar `reserva.id`, `reserva.empresa_id`, `reserva.cliente_id`, sucursal, rol autorizado y estados válidos antes de actuar. El cliente solo cancela reservas propias cuando esté permitido; el personal debe pasar autorización de empresa/sucursal.
- Bloquear los inventarios de las líneas de reserva (orden ascendente), verificar `detalle_reserva.empresa_id`, `inventario.empresa_id` y cantidades. Liberar **exactamente una vez** el stock reservado; nunca hacerlo en una reserva `CANCELADA`/`ATENDIDA` ni permitir valores negativos.
- Registrar movimiento de liberación y auditoría preservando la semántica del SP original; estado final correcto. No reactivar carrito `CONVERTIDO`.
- La idempotencia o el conflicto 409 frente a reintentos se decidirá tras ver restricciones y SQL histórico, antes de publicar el contrato HTTP.

### 3.4 Roles y defensa en profundidad
- Preferir `SECURITY INVOKER` con cuenta API sin superusuario, sin `BYPASSRLS` y sin privilegio de propietario que eluda RLS. Si fuese imprescindible `SECURITY DEFINER`, revisar propietario, `search_path` fijo y RLS/privilegios efectivos de forma explícita.
- **No otorgar `EXECUTE` a PUBLIC** en SP SaaS nuevos; conceder exclusivamente al rol de servicio verificado. Si los SP legacy siguen disponibles, bloquearlos desde la capa SaaS y valorar `REVOKE` de sus permisos en una migración futura revisada (nunca ejecutada aquí).
- Mantener PK, 48 FK tenant, constraints, triggers y auditoría del primer parcial. El prototipo debe cubrir RLS con rol real sin `BYPASSRLS` y aislamiento de 5 empresas.
- Diseñar pruebas de doble reserva simultánea, carrera de stock desde dos carritos, cancelación repetida, cancelación de otra empresa, rollback ante fallo de Kardex, error a mitad de líneas, productos inactivos, usuarios sin perfil, inexistentes y cambios de estado concurrentes.

## 4. Qué se considera validado y qué NO

La revisión estática de rutas, archivos de extracción y doc puede probar que **no hemos habilitado nuevas mutaciones comerciales**. El resultado READ ONLY confirmará existencia, firmas, privilegios y cuerpo actual de los SP; **NO valida la seguridad dinámica ni garantiza aislamiento**. Hasta ejecutar pruebas reales aisladas y revisadas de PostgreSQL, 5F no certifica operaciones de reserva. La copia restaurada y Supabase **no se alteran**.

## 5. Comandos para el estudiante

En PowerShell, dentro de `C:\SI2_Parcial1\fashionstore-api`, Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Resultado esperado, todavía NO ejecutado por el estudiante: `280 passed`** = 262 pruebas anteriores + 18 casos nuevos de revisión estática de scripts y bloqueo de rutas. Estos tests **no verifican seguridad real de SQL PostgreSQL**, solo comprueban guardas del utilitario, consistencia de los tres nombres y bloqueo de endpoints legacy.

Solo si las pruebas pasan:

```powershell
.\database\saas\11_extraer_definiciones_reserva_solo_lectura.ps1
```

Adjuntar al chat el archivo `fashionstore_P5F_SP_reservas_LOCAL_REVISAR.txt` que se guarda en `$env:TEMP` **después de revisarlo por si contiene secretos**. La salida PowerShell y el archivo permiten revisar los cuerpos SQL y elegir una migración posterior. No subir ese archivo a GitHub. No activar `SAAS_AUTH_ENABLED`.

**Cierre de subfase 5F:** revisión documental y extracción de definiciones, sin efectuar ninguna reserva real. El Paso 5 seguirá EN PROCESO. Esperar `siguiente` antes de implementar un nuevo SP tenant-aware.
