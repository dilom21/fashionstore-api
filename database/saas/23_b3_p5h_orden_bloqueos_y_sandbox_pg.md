# Bloque 3 / Paso 5H — Orden de bloqueos CU15/CU16 y preparación de sandbox PostgreSQL

**Estado:** código/plan/pruebas preparados en `feat/saas-multitenant-parcial2`; **PENDIENTE de que el estudiante ejecute la suite en Windows**. **Paso 5 global EN PROCESO**. No avanzar a Paso 6.

**Prohibiciones:** no modificar `main`, Supabase ni la copia restaurada `fashionstore_restore_20261023_193554_594`; mantener `SAAS_AUTH_ENABLED=false`. No ejecutar borradores `.sql.txt`, `CREATE PROCEDURE`, `CALL` ni operaciones de stock real.

## 1. Problema detectado en 5G

El código `CarritoTenantRepository.agregar_item` de CU15 obtenía bloqueo de cliente, expiraba carritos, **bloqueaba INVENTARIO**, y solo después bloqueaba o insertaba el carrito. El borrador de `sp_crear_reserva_tenant` de CU16, en cambio, bloquea **CARRITO antes de INVENTARIO**.

Con transacciones simultáneas para el mismo carrito/inventario, adquirir los mismos recursos en diferente orden puede contribuir a un ciclo de espera y un `deadlock`. Aunque el POST original también usa bloqueo de cliente, no se debe concluir que el diseño global sea seguro solo con eso: falta ensayar concurrencia PG con triggers y otras operaciones.

## 2. Cambio acotado a CU15

Se modificó **solo el orden de operaciones** en `app/modules/carrito/repositories/tenant_repository.py`, método `agregar_item`:

1. Verificar y bloquear cliente activo en la empresa del token.
2. Expirar carritos obsoletos de ese cliente, en la misma transacción.
3. **Buscar y bloquear** su carrito ACTIVO de la sucursal/empresa o insertar uno nuevo.
4. **Solo entonces** bloquear inventario `FOR UPDATE` y validar empresa, sucursal, variante/producto/categoría/temporada/talla/color activos, stock disponible.
5. Bloquear la línea, sumar cantidad, escribir detalle y actualizar fecha.
6. Un único COMMIT del router si todo funciona. Un 404, 409 o error SQL debe hacer ROLLBACK de toda la operación, **incluido un carrito recién insertado o carritos que se iban a expirar**. No modificar stock actual ni reservado.

Esto armoniza el orden principal **cliente -> carrito -> inventario -> línea** del POST con el prefijo **carrito -> inventario** del SP propuesto. El PATCH existente ya bloquea cliente y carrito antes de inventario, pero bloquea también la línea; el resto de rutas y triggers deberán probarse en conjunto.

**No se afirma que el riesgo de deadlock haya desaparecido**. Las carreras entre otros carritos/clientes y procedimientos, locks implícitos de FK/triggers, compras y actualizaciones del catálogo necesitan ensayos reales. Esta modificación tampoco despliega los SP 5G.

## 3. Pruebas nuevas

- Cuatro casos de `tests_saas/test_tenant_carrito_transacciones.py`: capturan orden de SQL emitido en SQLite para carrito nuevo, carrito existente, inventario inexistente y stock insuficiente. Para los dos errores se exige rollback completo sin carrito/detalle residual; se comprueba stock intacto.
- Ocho casos estáticos de `tests_saas/test_sandbox_reservas_5h.py`: guardas de doble opt-in, origen y destino fijados a localhost, `pg_dump --schema-only`, rechazo de base existente, verificación de fuente, restauración de secretos y `pg_restore`.

**12 pruebas nuevas** respecto de 320 previas. **Total esperado, todavía no confirmado: `332 passed`.** IMPORTANTE: en SQLite los `FOR UPDATE` se ignoran; capturar el orden de SQL no demuestra que PostgreSQL haya adquirido locks ni ausencia de deadlocks.

## 4. Preparación segura de un sandbox independiente (NO crear ahora)

El archivo `database/saas/13_preparar_sandbox_pg_reservas_local.ps1` fue creado como **utilidad FUTURA**, no se ha ejecutado ni activado.

- **Por defecto, sin parámetros**: imprime un plan **sin pedir clave ni conectarse al motor** y sale antes de `pg_dump`, `createdb` o `pg_restore`. Este modo de planificación puede usarse en la validación actual.
- Para **crear** un sandbox nuevo requiere expresamente dos switches (no se deben usar todavía). Solo permite origen `127.0.0.1:5432 / fashionstore_restore_20261023_193554_594` y destino fijo `fashionstore_saas_reservas_sandbox_5h`; falla si ya existe el destino.
- Antes de permitir creación valida origen: 5 empresas, 11 reservas, 274 inventarios y 48 FK tenant. El `pg_dump` se conecta con `default_transaction_read_only=on`.
- Exporta `--schema-only --format=custom` a un archivo temporal; crea una **DB distinta y vacía** desde `template0` y restaura `--no-owner --no-acl`. Comprueba que empresa/cliente/reserva/inventario estén vacíos. No inserta los cinco tenants ni cualquier dato original.
- No invoca ni instala los borradores `sp_*_tenant`. Preserva funciones y triggers existentes del esquema, sujetos a futura auditoría. Si restaurar falla, **no elimina automáticamente la base parcial**; requiere revisar manualmente, sin riesgo de borrar la original.
- Credenciales solo en proceso y se limpian; el dump temporal se elimina en un bloque `finally`.
- La instalación futura exigirá consentimiento explícito, plan de roles/API sin `BYPASSRLS`, seed académico ficticio y pruebas transaccionales en la NUEVA base. El `postgres` superusuario actual no valida aislamiento RLS.

**Esta subfase NO solicita ni autoriza crear esa base.** Su configuración y efectividad deben revisarse antes de cualquier uso futuro; no se ha verificado aún la compatibilidad real del esquema restaurado con `pg_restore` en una DB vacía.

## 5. Verificación en el equipo del estudiante

PowerShell en `C:\SI2_Parcial1\fashionstore-api`, entorno Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Esperado: `332 passed` (no confirmado).**

Si las pruebas pasan, ejecutar **ÚNICAMENTE el modo PLAN sin parámetros**, que debe imprimir `MODO PLAN SIN CAMBIOS` y no solicitar contraseña:

```powershell
.\database\saas\13_preparar_sandbox_pg_reservas_local.ps1
```

**NO ejecutar el script con switches de creación**. Tampoco ejecutar SQL en PostgreSQL en este paso. Enviar ambas salidas de PowerShell para registrar la validación 5H.

## 6. Criterio de cierre

Si los tests SQLite/estáticos pasan y el modo plan del script no realiza acciones, cerrar la subfase **solo como corrección del orden SQL emitido y preparación controlada de un sandbox**. El Paso 5 permanecerá EN PROCESO: faltan instalación segura, validación transaccional/roles RLS en PG separado, nuevos endpoints de reserva y venta/pago/compra/devolución. No activar `SAAS_AUTH_ENABLED` ni desplegar hasta completar todas esas pruebas. Esperar la palabra `siguiente` para cualquier próxima subfase.
