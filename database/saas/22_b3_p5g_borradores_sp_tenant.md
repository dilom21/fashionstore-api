# Bloque 3 / Paso 5G — Borradores tenant-aware de reservas (CU16, CU17 y CU18)

**Estado: PREPARADO, pendiente de pruebas Windows y preflight PostgreSQL READ ONLY.**
**Paso 5 global EN PROCESO. No avanzar a Paso 6.** Rama exclusiva:
`feat/saas-multitenant-parcial2`; `SAAS_AUTH_ENABLED=false`.
No ejecutar `CREATE PROCEDURE`, no aplicar migraciones, no tocar `main` ni
Supabase ni el PostgreSQL restaurado.

## 1. Archivos elaborados: son texto SQL NO EJECUTAR

| Borrador (nunca ejecutado) | Accion empresarial prevista |
|---|---|
| `database/saas/borradores/12_sp_crear_reserva_tenant.sql.txt` | CU16: crear reserva propia desde carrito activo, bloquear inventarios y aumentar `stock_reservado` |
| `database/saas/borradores/13_sp_cancelar_reserva_tenant.sql.txt` | CU16/CU17: cliente cancela solo la propia; ADMINISTRADOR/ENCARGADO_SUCURSAL cancelan según su rol, permiso y sucursal |
| `database/saas/borradores/14_sp_finalizar_reserva_sin_compra_tenant.sql.txt` | CU18: ADMINISTRADOR/ENCARGADO_SUCURSAL/CAJERO finalizan una reserva CONFIRMADA sin venta asociada y liberan `stock_reservado` |

Los nombres son **nuevos**; no sustituyen ni reescriben los
`sp_crear_reserva_desde_carrito`, `sp_cancelar_reserva` y
`sp_finalizar_reserva_sin_compra` legacy. Su extensión `.sql.txt`, carpeta
`borradores` (fuera de `supabase/migrations`) y encabezado **NO EJECUTAR**
son deliberados: **ninguno se instala ni se invoca en este paso**.

### Diseño incorporado en cada borrador
- Parámetros `p_empresa_id`, `p_usuario_id` y recurso de la transacción; *al implementar FastAPI posteriormente* los primeros deben provenir exclusivamente de JWT empresarial y membresía validados, nunca de JSON controlado por cliente.
- Seguridad adicional DENTRO del SP: `usuario_empresa` activo, usuario/empresa/rol activos, suscripción ACTIVA vigente en Bolivia, plan activo con `permite_reservas`; `FOR SHARE` de membresía/suscripción y `FOR UPDATE` de carrito/reserva e inventarios.
- `SECURITY INVOKER` y `search_path=public,pg_temp`; **REVOKE ALL ON PROCEDURE ... FROM PUBLIC** explícito después del cuerpo, porque PostgreSQL suele otorgar EXECUTE público por defecto. La instalación futura deberá envolver CREATE+REVOKE en una misma transacción, usar rol de conexión no superusuario/no `BYPASSRLS` y conceder EXECUTE solo al rol API auditado. **Aún no hay GRANT a ningún rol, y no se instala nada**.
- `INSERT` de `reserva`, `detalle_reserva` y `movimiento_inventario` con `empresa_id` explícito. `UPDATE` de inventario, carrito y reserva con filtro por `empresa_id`. No se modifica `stock_actual`.
- Creación: fecha futura, carrito ACTIVO y con menos de 2 horas de vigencia, cliente dueño dentro de empresa, sucursal propia, ninguna reserva previa, todas las relaciones de inventario y producto correctas, cantidades positivas y disponibilidad suficiente bajo bloqueo de filas.
- Procesamiento de líneas con orden estable de `inventario_id`; ante un fallo, el procedimiento lanza excepción y el llamador debe hacer rollback de **toda** la transacción; no hay COMMIT interno. Se conservan movimientos Kardex `RESERVA` y `LIBERACION_RESERVA`.
- Cancelación: `PENDIENTE`/`CONFIRMADA`, cliente propietario o personal autorizado de CU17 (ADMINISTRADOR/ENCARGADO_SUCURSAL con permiso `GESTIONAR_RESERVAS/EJECUTAR`); rechaza reservas con venta asociada, libera reservado una sola vez y deja carrito CONVERTIDO.
- Finalización sin compra: `CONFIRMADA` y sin venta vinculada; ADMINISTRADOR/ENCARGADO_SUCURSAL/CAJERO con empleado activo y `GESTIONAR_RESERVAS/EJECUTAR`, alcance sucursal para no administradores, liberación única y reserva `ATENDIDA`.

## 2. Puntos que siguen PENDIENTES de seguridad y ejecución

Estos borradores **NO han sido probados contra PostgreSQL**. Las pruebas estáticas
no detectan errores PL/pgSQL de compilación/ejecución, triggers ocultos,
privilegios efectivos, RLS, tipado de timestamps, semántica de transacciones
ni carreras reales.

La integración futura requerirá un **PostgreSQL aislado** de pruebas y
una revisión de seguridad independiente de los resultados de pytest.

**Bloqueadores conocidos antes de cualquier CALL:**
1. Ejecutar el preflight de metadatos; revisar nombres, columnas, FK UNIQUE, permisos, propietarios, índices y triggers de reserva/Kardex.
2. Revisar en PostgreSQL **aislado de ensayo**, con datos ficticios y cuenta SQL sin `BYPASSRLS`, la compilación, grants, RLS, `FOR SHARE` y `FOR UPDATE`. La copia restaurada NO es la base de ensayo.
3. **Orden de bloqueos cruzados:** el POST de carrito de 5B actualmente consulta/bloquea inventario antes de bloquear su carrito ACTIVO. El nuevo borrador de crear reserva bloquea carrito **antes** de inventario. Esto podría producir **deadlocks** al agregar prendas y convertir simultáneamente. Debe armonizarse el orden en ambas rutas y probarlo antes de activar reserva; no afirmar concurrencia segura todavía.
4. Verificar que la comprobación previa de estado de variante/producto/categoría sea estable durante cambios concurrentes y que todos los triggers funcionen con `empresa_id` y auditoría del actor. Corregir el borrador si un caso dinámico lo requiere.
5. El `p_usuario_id` y `p_empresa_id` de un `SECURITY INVOKER` no son prueba criptográfica por sí mismos: el rol de conexión API debe ser restringido, el servidor debe derivarlos del token y el acceso directo a la BD/SP debe estar limitado. No confiar en un GUC `app.empresa_id` modificable por el llamador.
6. Comprobar permisos sobre secuencias de IDs y nuevas rutinas; los roles no administradores deben tener los mínimos permisos. El rol `postgres` superusuario no valida RLS efectivo.
7. Repetir pruebas de cinco empresas y casos hostiles: IDs ajenos, sin membresía, cliente inactivo, carrito vencido, inventario agotado, reserva duplicada, edición concurrente, cancelación repetida, venta vinculada, liberación dos veces, fallo en segundo item y conservación de Kardex.
8. El contrato nuevo no incorpora todavía rutas HTTP para crear/cancelar/finalizar; **continúan bloqueadas con 503** si SaaS está habilitado. Tampoco se activa `SAAS_AUTH_ENABLED`.

## 3. Archivos de verificación segura

`tests_saas/test_reserva_sp_borradores_5g.py`: **40 casos ESTÁTICOS** para
 comprobar nombre nuevo, archivos inertes, pertenencia/rol/plan,
`SECURITY INVOKER`, `REVOKE PUBLIC`, `empresa_id` en DML,
`FOR UPDATE`, Kardex, ausencia de COMMIT y bloqueo de rutas legacy.

`database/saas/12_preflight_borradores_reservas_local.sql` y `.ps1`: solo
`SELECT` a `pg_catalog` e `information_schema`, protegidos con
`BEGIN TRANSACTION READ ONLY`, `default_transaction_read_only=on` y
`ROLLBACK`. Inspeccionan columnas, CHECK/UNIQUE/FK, índices,
triggers de reservas/Kardex, rol SQL y flags RLS. No ejecutan borradores.

## 4. Verificación en Windows — SOLO estos comandos

Desde `C:\SI2_Parcial1\fashionstore-api`, Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Resultado esperado, todavía NO confirmado:** **320 passed** = 280 anteriores
+ 40 nuevas. Las pruebas son estáticas, no operativas PG.

Si pytest pasa:

```powershell
.\database\saas\12_preflight_borradores_reservas_local.ps1
```

El script pide contraseña de `postgres` LOCAL en
`127.0.0.1:5432 / fashionstore_restore_20261023_193554_594`; lista
metadatos y termina en `ROLLBACK`. Envíame la salida; si fuese extensa,
puedes copiar solo desde `PRECHECK 5G OK` y las secciones relativas a tablas
reserva, inventario y movimientos, o adjuntar la salida de texto.

**No ejecutar ninguno de los tres .sql.txt**; tampoco copiar/pegar
los `CREATE PROCEDURE` del borrador en psql. No hay pasos de escritura.

## 5. Criterio de cierre

Cerrar 5G **solo como preparación estática de procedimientos y preflight de
metadatos** tras recibir los resultados de Windows. El Paso 5 seguirá
EN PROCESO, con operaciones reales de reserva, venta, pago, compra y devolución
por implementar y ensayar. Detenerse hasta `siguiente`.
