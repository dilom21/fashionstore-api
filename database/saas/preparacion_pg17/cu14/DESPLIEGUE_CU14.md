# CU14/CU23/CU24 — procedimiento controlado

No ejecutar hasta contar con autorización, respaldo verificado y API detenida.

1. Verificar `MANIFIESTO.sha256`, PostgreSQL 17, paquete CU11/CU13, RLS/FORCE
   RLS, owners restringidos y ausencia de los objetos/policies CU14.
2. Ejecutar `100` como instalador `CREATEROLE` autorizado con
   `psql -X -v ON_ERROR_STOP=1`. En Supabase el instalador es `postgres`:
   tiene `ADMIN OPTION` sobre los owners y `BYPASSRLS` para administrar el
   catálogo; ningún rol del paquete (`fashionstore_api`, `fashionstore_sp_owner`,
   `fashionstore_webhook`, `fashionstore_webhook_owner`) recibe `BYPASSRLS`.
   La transacción completa debe confirmar y restaurar exactamente las
   membresías preexistentes, incluida la concedida por `supabase_admin`.
3. Ejecutar `103_restringir_execute_cu14.sql` y exigir
   `CU14_EXECUTE_HARDENING=PASS`. Este parche posterior conserva identificable
   el SQL `100` realmente instalado y revoca exclusivamente los `EXECUTE`
   directos de `anon`, `authenticated` y `service_role` sobre los dos
   procedimientos públicos CU14. No modifica el procedimiento privado webhook.
4. Ejecutar `101` y exigir `CU14_POSTFLIGHT=PASS`; el verificador falla si
   cualquiera de esos tres roles conserva `EXECUTE` sobre los procedimientos
   públicos CU14.
5. Ejecutar `104_crear_webhook_login_PROPUESTA_NO_EJECUTAR.sql` como
   administrador con `CREATEROLE` para crear `fashionstore_webhook_login`
   (`LOGIN INHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`) con
   `PASSWORD NULL` y una única membresía `fashionstore_webhook` con
   `INHERIT TRUE, SET FALSE`. El script no modifica roles, ACLs, RLS ni
   propietarios existentes. Después, en una sesión `psql` interactiva,
   ejecutar `\password fashionstore_webhook_login`; la contraseña debe quedar
   solo en el gestor de secretos, nunca en SQL, `.env`, logs ni repositorio.
   Verificar la conexión autenticada con `105_verificar_webhook_login.sql` y
   exigir `CU14_WEBHOOK_LOGIN_CONNECTION=PASS`.
6. Configurar `DATABASE_WEBHOOK_URL`, `STRIPE_SECRET_KEY` Test Mode,
   `STRIPE_WEBHOOK_SECRET` y `STRIPE_CURRENCY=bob` en el gestor de secretos.
7. `SAAS_VENTAS_ENABLED` ya está en `true` desde CU13 y **no debe
   desactivarse** para preparar CU14. El flujo digital permanece bloqueado por
   dependencias, no por el flag:
   - `POST /tenant/ventas/digital` y `POST /tenant/pagos/stripe/intencion`
     fallan cerrados con 503 mientras `verificar_principal_ventas` no
     encuentre `sp_crear_venta_digital_tenant` /
     `sp_registrar_intencion_stripe_tenant` con owner, `SECURITY DEFINER`,
     `search_path` y ACL válidos.
   - `POST /tenant/pagos/stripe/webhook` responde 503 si falta
     `DATABASE_WEBHOOK_URL`, si el login del pool no acredita
     `fashionstore_webhook`, o si el procedimiento privado no existe o no
     tiene `EXECUTE`.
   - El proveedor Stripe falla de forma limpia si faltan
     `STRIPE_SECRET_KEY` o `STRIPE_WEBHOOK_SECRET`; no se crean cobros.
   - `GET /tenant/pagos/stripe/ventas/{id}/estado`, historial y comprobante
     solo reflejan datos existentes; no habilitan la compra digital.
   Verificar login API, login webhook, creación/reutilización de intención,
   firma RAW del webhook, compra propia, aislamiento, idempotencia y reembolso
   total/parcial antes de declarar CU14 operativo. `payment_intent.refunded` es
   un evento interno del adaptador; los eventos reales de Stripe
   (`charge.refunded`, `refund.created`, `refund.updated`) solo se traducen a
   él cuando el reembolso es total y exitoso.

Parada: ante error de `100` no continuar. Ante fallo de `101`, mantener la API
detenida y auditar. No ejecutar `102` automáticamente. La reversión `102`
detecta actividad pendiente (ventas digitales PENDIENTE, pagos Stripe PENDIENTE
o compensaciones pendientes) sin depender de la fecha, restaura membresías y
solo elimina los objetos CU14 reconocidos por los marcadores
`fashionstore-cu14-v2:*`. No toca CU11/CU12/CU13.

La cuenta Stripe actual es central y de pruebas; no implementa Stripe Connect
ni separación de fondos por empresa.
