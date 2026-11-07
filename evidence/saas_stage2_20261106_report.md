# ETAPA 2 — pruebas locales de migraciones SaaS

Fecha: 2026-11-06. Destino exclusivo: `127.0.0.1:5432 / fashionstore_saas_stage2_20261106`, PostgreSQL 17.9. Base anterior `fashionstore_restore_validation_20261106_135445`: existente y no utilizada ni modificada. Supabase remoto: no conectado.

## Restauración

- El destino se comprobó ausente antes de crearlo desde `template0`.
- Primer `pg_restore --single-transaction`: falló porque el dump contiene `CREATE SCHEMA public` y PostgreSQL 17 ya lo creó. No quedó restauración parcial.
- Se comprobó que `public` contenía cero objetos, se retiró sólo ese esquema vacío del destino nuevo y se repitió `pg_restore --no-owner --no-privileges --single-transaction --exit-on-error`.
- Segunda restauración: correcta. Tamaño final del laboratorio tras migraciones: 14,833,331 bytes. La base anterior sigue existiendo (12,146,355 bytes).

## Estado por archivo

| Archivo | Resultado |
|---|---|
| `000_preflight.sql` | PASS: `PRECHECK_OK`, luego `ROLLBACK`. |
| `010_saas_core_vanter.sql` | PASS y COMMIT: cinco tablas, dos planes, Vanter Men, una suscripción/configuración y nueve membresías. |
| `020_tenant_expand_backfill.sql` | PASS y COMMIT: 31 columnas `empresa_id`, backfill íntegro y FKs a empresa. |
| `030_tenant_integrity.sql` | PASS y COMMIT: uniques tenant, 28 claves candidatas, 46 FKs empresa-padre, dos FKs de membresía e índices. |
| `040_routines_views_hardening.sql` | PASS y COMMIT en primera ejecución. Una prueba posterior detectó bitácora sin tenant; se corrigió el archivo y se aplicó al laboratorio la misma sustitución de función. Reprueba PASS. |
| `050_rls_acl_template.sql` | NO EJECUTADO, por diseño y restricción. |
| `060_postflight.sql` | PASS: `POSTCHECK_SCHEMA_INTEGRITY_OK_RLS_AUN_BLOQUEADO`, luego `ROLLBACK`. |
| `070_integrity_tests_rollback.sql` | PASS: UNIQUE tenant, FK compuesta e inmutabilidad; tenant ficticio ausente después de `ROLLBACK`. |

## Conservación histórica

Se calculó antes un hash agregado MD5 de todas las filas de cada tabla y después el mismo hash excluyendo únicamente la columna nueva `empresa_id`. Las 31 comparaciones fueron idénticas; no cambió ningún ID ni valor histórico preexistente.

Conteos preservados: bitácora 806; carrito 48; categoría 9; cliente 5; colección 3; configuración AR 8; detalle carrito 53; detalle devolución 4; detalle orden 8; detalle reserva 16; detalle venta 41; devolución 4; empleado 4; inventario 265; movimientos 57; órdenes 4; pagos 30; productos 14; producto-colección 14; promoción 1; promoción-producto 3; proveedores 2; proveedor-producto 14; pruebas AR 6; recursos 36; reservas 11; sesiones AR 6; sucursales 4; temporadas 2; variantes 120; ventas 40.

Datos SaaS: 1 empresa, 2 planes, 1 suscripción, 9 membresías y 1 configuración. La vista devuelve 265 filas, igual que inventario.

## Inventario posterior

- 46 tablas, 1 vista, 16 funciones, 9 procedimientos y 58 triggers de usuario.
- 145 FKs; cero sin validar.
- 251 índices; cero inválidos o no preparados.
- Los nueve procedimientos legacy conservan definición pero `PUBLIC` ya no tiene EXECUTE.
- `vw_inventario_stock` es `security_invoker`, incluye `empresa_id` y une las tablas empresariales por tenant.
- 38 tablas tienen RLS heredado; las cinco nuevas todavía no. FORCE RLS: 0. Políticas: 0. Esto es un bloqueo esperado de `050`, no una validación de seguridad.

## Hallazgo y corrección

La función legacy `fn_registrar_bitacora` insertaba sin `empresa_id`. Una actualización reversible de producto produjo inicialmente `producto_empresa=1` y `bitacora_empresa=NULL`. Se corrigió `040` para leer `empresa_id` de `NEW/OLD` mediante JSONB; tablas globales como usuario/rol siguen generando NULL deliberadamente. Repetición reversible: producto 1, bitácora 1. El SQL exacto aplicado al laboratorio está en `evidence/saas_stage2_20261106_apply_bitacora_fix.sql`.

Las dos funciones legacy que carecían de `SET search_path` (`fn_actualizar_actividad_carrito` y `fn_validar_sucursal_detalle_carrito`) quedaron corregidas en `040` con `public,pg_temp`.

## Pruebas entre empresas

Dentro de una única transacción se creó un tenant ficticio y se comprobó:

1. Un nombre de categoría existente en Vanter Men puede repetirse en otra empresa.
2. El mismo nombre repetido dentro del segundo tenant falla por `uq_categoria_empresa_nombre`.
3. Un producto del segundo tenant no puede referenciar una categoría Vanter: falla `fk_tenant_fk_producto_categoria`.
4. Cambiar `empresa_id` de una categoría existente falla por el trigger de inmutabilidad.
5. `ROLLBACK` eliminó todos los datos ficticios (`sin_tenant_ficticio=true`).

## Análisis de `050`

Con el código actual, la identidad SQL individual sí es necesaria para satisfacer el contrato implementado: `tenant_reserva_transaccional_5al.py` comprueba `session_user`, exige LOGIN no superusuario/no BYPASSRLS y llama a `fs_verificar_actor_reserva(empresa_id,usuario_id)`. El motor actual es compartido y sólo instala `app.empresa_id` mediante `set_config(..., true)`. Un actor capaz de ejecutar SQL con ese login también puede alterar ese GUC; por tanto, GUC solo no es una raíz de confianza.

Alternativas seguras, todas requieren trabajo posterior:

- **Login PostgreSQL por usuario**, que coincide con el contrato actual. Es fuerte frente a suplantación dentro del tenant, pero exige provisión/rotación de credenciales y un pool separado o dinámico por principal.
- **Supabase Auth/Data API con JWT verificado y RLS mediante `auth.uid()`/claims de aplicación**. Es compatible con pooling porque la capa Supabase establece claims por petición, pero requiere migrar el acceso directo de SQLAlchemy y mapear la identidad global actual; no puede asumirse sin cambios de aplicación.
- **Login compartido restringido más función privada que verifique criptográficamente el JWT en cada transacción** y derive empresa/usuario sin aceptar GUC libre. Puede conservar pooling, pero requiere código FastAPI, manejo de claves y pruebas de replay/revocación. Un setter `SECURITY DEFINER` que sólo reciba IDs no resuelve el problema.
- **Login por tenant o pool por tenant** impide cruces entre empresas, pero no impide que un usuario suplante a otro dentro de la misma empresa; es insuficiente para los SP privilegiados sin un segundo factor de identidad verificable.

Conclusión de seguridad: no ejecutar `050` ni considerar producción lista. Si no se modifica el acceso actual, el camino coherente es principal SQL individual. Si se desea conservar un único pool, debe cambiarse el contrato para usar identidad JWT verificada por una capa que el login compartido no pueda falsificar.

## Dictamen

Migraciones estructurales: **PASS LOCAL CON UNA CORRECCIÓN EN 040**. Conservación histórica e integridad compuesta: **PASS**. Seguridad RLS/ACL y SP tenant: **BLOQUEADA / NO LISTA PARA PRODUCCIÓN**.
