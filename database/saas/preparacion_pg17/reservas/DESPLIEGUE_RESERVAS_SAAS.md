# CU11 — instalación controlada

Estado: propuesta local; no aplicada a Supabase.

1. Detener escritores y mantener `SAAS_RESERVAS_ENABLED=false`.
2. Crear respaldo actualizado y checkpoint de rutinas, ACL y RLS.
3. Regenerar `080` con `generar_migracion_reservas.py` y revisar el diff.
4. Ejecutar `080` con `ON_ERROR_STOP`; es una sola transacción.
5. Ejecutar `081` mediante `psql -v ON_ERROR_STOP=1`; exigir el mensaje
   `CU11_POSTFLIGHT=PASS` y código de salida cero. `FAIL` eleva una excepción y
   devuelve un código distinto de cero. Ante fallo posterior a COMMIT,
   mantener la API detenida y no activar el flag.
6. Probar con rol restringido: actor válido, actor falso, tenant cruzado,
   stock insuficiente, repetición, cancelación y dos transacciones concurrentes.
7. Solo después configurar exactamente los owners esperados por FastAPI y
   activar el flag en una ventana separada.

El instalador debe ser el mismo propietario administrativo autorizado que
posee los objetos `public`, con `CREATEROLE`, pero `NOSUPERUSER` y
`NOBYPASSRLS`. `080` no concede DML: exige y reutiliza las ACL mínimas ya
instaladas por 050c. Las membresías `SET` y `CREATE` usadas para transferir
ownership son temporales y `081` comprueba que no sobrevivan.
Las policies RLS exactas de 050b, incluida
`p_sp_tenant_movimiento_inventario`, se comprueban antes y después de instalar;
080 no crea, sustituye ni relaja ninguna policy.

Condiciones de parada: cualquier error de `080`, un resultado distinto de
`PASS` en `081`, un owner inesperado, `PUBLIC EXECUTE`, una membresía temporal
o una prueba cruzada aceptada. No activar el flag ni continuar con pruebas HTTP.

## Reversión controlada

Reversión: `082` elimina únicamente las cuatro rutinas que el preflight de
`080` exige ausentes. No intenta revertir datos creados por reservas. Si hubo
uso comercial, no ejecutar `082`: detener la API y completar/reparar hacia
adelante tras preservar evidencia y respaldo.

`082` requiere el mismo instalador `CREATEROLE`, habilita `SET ROLE` de forma
temporal, elimina primero los tres procedimientos como `fashionstore_sp_owner`
y luego el verificador como `fashionstore_context_owner`. No revoca ACL de
tablas, no elimina datos y retira ambas concesiones temporales antes del COMMIT.
Ejecutarlo solamente si el checkpoint confirma que los cuatro objetos fueron
creados por `080` y todavía no hubo reservas comerciales posteriores al corte.

080 deja en los comentarios de las cuatro rutinas un identificador de paquete,
el hash del cuerpo y un marcador temporal. El verificador conserva además los
valores de las secuencias de reserva, detalle y movimiento. 082 se niega a
ejecutar si cambia un cuerpo, owner, ACL, policy, FORCE RLS o cualquiera de esas
secuencias. El control por secuencia es intencionalmente conservador: también
bloquea la reversión después de un intento que terminó en rollback.

PostgreSQL no puede demostrar que un administrador privilegiado no haya
alterado simultáneamente rutinas, comentarios, secuencias y evidencia. Antes de
082 sigue siendo obligatorio un control externo: API detenida, manifiesto
verificado, checkpoint de catálogos y confirmación operativa de ausencia de uso
comercial. Si existe duda, no ejecutar 082 y reparar hacia adelante.
