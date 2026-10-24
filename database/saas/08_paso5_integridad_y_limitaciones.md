# Bloque 2 — Paso 5/7: defensa estructural contra referencias cruzadas

**Estado:** migración y script de ejecución local preparados, NO ejecutados en el PostgreSQL del estudiante. Sin cambios en Supabase.  
**Rama:** `feat/saas-multitenant-parcial2`.  
**Fuente obligatoria:** PDF de Examen Parcial-2, Desafío VI, apartados 9.2 y 9.6 y RNF02.

## Archivos
- `supabase/migrations/20261023000300_integridad_tenant_local.sql`: DDL transaccional, limitado por `current_database()` a la copia local. **NO utilizar con `supabase db push`**; es un prototipo de migración para pruebas, no para despliegue.
- `database/saas/04_integridad_tenant_local.ps1`: comprueba host, base, datos iniciales, solicita confirmación `AISLAR`, ejecuta SQL local y verifica los conteos históricos.
- Destino fijo: `127.0.0.1:5432 / fashionstore_restore_20261023_193554_594`, PostgreSQL 18.

## Cambios previstos
1. Mantener las 31 tablas empresariales heredadas y sus claves primarias originales.
2. Añadir `UNIQUE(empresa_id,id)` a las tablas empresariales con clave primaria simple `id`. Las tres tablas puente con claves compuestas originales conservan sus PK y referencian sus padres mediante FK tenant.
3. Añadir una FK compuesta `(empresa_id, clave_padre) -> (empresa_id,id)` por cada FK histórica entre dos tablas empresariales, incluidas referencias opcionales a sus dos o más padres (reserva, ventas, inventario, pruebas AR, etc.). Las 60 FK antiguas permanecen y las FK nuevas se agregan como defensa paralela.
4. Añadir FK `(empresa_id, usuario_id)` desde `cliente` y `empleado` hacia `usuario_empresa(empresa_id, usuario_id)`. Así un perfil no vincula una cuenta de usuario que carezca de membresía del mismo tenant. El `usuario_id` de `cliente` puede ser NULL; la integridad en ese caso se maneja desde API.
5. Convertir a ámbito empresarial las restricciones de unicidad global de `categoria.nombre`, `temporada.nombre`, `variante_producto.sku`, `sucursal(ciudad_id,nombre)`, `proveedor.nit`, `cliente(ci,usuario_id)` y `empleado(ci,usuario_id)` (las dos últimas como restricciones independientes); conservar `pago.referencia_transaccion` global porque puede provenir de una pasarela que la exige única.
6. Conservar la integridad de las filas antiguas: no hay `UPDATE` ni `DELETE` de operaciones comerciales.

## Límites pendientes
- **El DDL no basta para RNF02:** aún debe aplicarse autorización por tenant en todos los endpoints y funciones SQL, aislamiento RLS válido para el rol real de aplicación y revisión de lectura/reportes y la vista.
- El usuario autenticado no necesariamente puede crear un cliente/empleado en otra empresa; FastAPI debe cotejar token, rol y membresía activa.
- Los índices con llave de identidad global (correos) se preservan.
- No se alteran las singularidades `reserva.carrito_id`, `venta.carrito_id` o `venta.reserva_id` porque esos ID ya son globalmente únicos.
- Los 27 triggers existentes se mantienen; no se desactivan.
- Los tres conjuntos de AR originales con RLS desactivado requieren controles explícitos adicionales.
- El uso de `bitacora.empresa_id IS NULL` para eventos de plataforma requiere separación y reglas explícitas.
- Se debe verificar en una etapa posterior que los triggers de escritura y las consultas del backend manipulen `empresa_id` correctamente.
- Todas estas modificaciones se probarán localmente primero, antes de diseñar una versión para despliegue remoto.

## Criterios de salida del paso
- Script devuelve `INTEGRIDAD TENANT LOCAL APLICADA Y VERIFICADA`.
- Se mantienen 46 tablas, 27 triggers y los conteos del Paso 4.
- Aparecen decenas de claves compuestas `fk_tenant_...`, junto con las 28 (?) restricciones de apoyo para IDs simples (conteo exacto será revisado contra catálogo local).
- Prueba adicional posterior: una FK compuesta debe bloquear relaciones cruzadas en transacción de prueba con rollback, sin alterar registros históricos. En esta etapa el script comprueba estructura y conteos; **todavía no se ha hecho esa prueba negativa cruzada**.
