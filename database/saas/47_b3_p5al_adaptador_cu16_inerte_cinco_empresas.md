# Bloque 3 / Paso 5AL — Adaptador de creación CU16 seguro, aún INERTE

**Última evidencia recibida en Windows:** 656 passed, 2 warnings, 0 fallos; laboratorio 5AK con `A08=PASS`, `A11=PASS`, `A13=PASS` sobre un **procedimiento de ensayo exclusivo de PG18 independiente**, no la lógica definitiva CU16.

## Empresas definitivas — mantener cinco, no recrearlas

| Empresa | Slug | Plan | Sucursales |
|---|---|---|---:|
| Vanter Men | vanter-men | PROFESIONAL | 4 |
| UrbanWear | urbanwear | BASICO | 1 |
| Elegancia Sur | elegancia-sur | PROFESIONAL | 3 |
| Denim House | denim-house | BASICO | 2 |
| SportLine | sportline | PROFESIONAL | 3 |

La base histórica/restaurada `fashionstore_restore_20261023_193554_594` del servidor `127.0.0.1:5432` ya tiene las cinco empresas, sus 13 sucursales y los datos del primer parcial. **NO repetir su migración, no tocar sus datos ni credenciales**. Tampoco modificar Supabase o rama `main`.

## Entrega 5AL: integración técnica preparada SIN exposición HTTP

- `app/modules/reservas/services/tenant_reserva_transaccional_5al.py`: implementa un **adaptador que NO está enlazado con rutas HTTP** y aprovecha el constructor parametrizado `preparar_creacion_reserva` de 5O.
- La primera consulta de cada transacción comprueba que el LOGIN PostgreSQL real sea `NOSUPERUSER`, `NOBYPASSRLS`, `NOINHERIT`, no pueda crear DB/roles, tenga RLS efectivo en `empresa`, `reserva` e `inventario`, y vea **exactamente la empresa `empresa_id/slug` de TenantContext**.
- Exige exactamente un `sp_crear_reserva_tenant` `SECURITY INVOKER` con `EXECUTE` otorgado al principal; si no existe o el rol puede ver otros tenants, falla antes de realizar `CALL`.
- Después del procedimiento comprueba que el ID de reserva retornado sea válido y que la reserva realmente pertenezca a ese carrito, empresa y cliente asociado al `usuario_id` del contexto. **Un único COMMIT al final**; cualquier fallo después del inicio SQL provoca `ROLLBACK`.
- Test con sesiones falsas unitarias que simulan principal inseguro, ausencia de policies/SP, retorno malicioso, error SQL, parámetros incorrectos y transacción correcta. **Estos tests no demuestran todavía seguridad real ni autorización por membresía**.

**NO abrir** `POST /tenant/reservas`, `PATCH /tenant/reservas/{id}/cancelar`, `POST /tenant/comercial/ventas`, `POST /tenant/comercial/pagos`. Actualmente no se añadió el adaptador a `app/main.py` ni se cambió la allowlist de `saas_route_gate.py`. Mantener `SAAS_AUTH_ENABLED=false`.

## Trabajo restante para terminar CU16 y el resto del SaaS

1. En el **laboratorio independiente 55432** instalar y probar el **SP comercial** `sp_crear_reserva_tenant` (no el `sp_lab_reservar_5aj`). Preparar identidades demo activas exclusivamente allí, con `rol`, `usuario`, `usuario_empresa`, `cliente.usuario_id`, plan de reservas y suscripción activa; usar datos de ensayo distintos de los cinco finales.
2. Completar las políticas y privilegios mínimos necesarios para lectura de membresía/planes + escrituras de `reserva`, `detalle_reserva`, `inventario`, `movimiento_inventario`, `bitacora`, `carrito`. Ensayar triggers `SECURITY INVOKER`, secuencias y `FOR SHARE/FOR UPDATE` con un rol que **no pueda suplantar otro tenant vía GUC o parámetros**.
3. Integrar un endpoint HTTP CU16 con JWT validado, membresía, `permite_reservas`/plan/suscripción, consulta transaccional y códigos 401/403/404/409. Luego CU17/CU18, ventas, pagos, compras y devoluciones.
4. Crear y habilitar **de manera segura** los administradores ficticios de las **cinco empresas finales**, después de validar aislamiento de endpoints y límites de plan, sin contraseñas guardadas en Git.
5. Ensayar autenticación real PostgreSQL/HTTP para las cinco empresas, límites de BASIC/PROFESIONAL, reportes, offline/IA y demás requisitos del parcial, antes de activar SaaS o desplegar.

**Estado de 5AL:** código y pruebas disponibles para validación local. Esto NO significa que el SaaS sea desplegable. No ejecutar ningún DDL/SQL contra la base de las cinco empresas.

## Integración posterior — rutas CU16/CU17/CU18 detrás de DOBLE FLAG

Este documento describe el estado histórico 5AL. El código actual añade `POST /tenant/reservas`, `PATCH /tenant/reservas/{id}/cancelar` y `PATCH /tenant/reservas/{id}/finalizar-sin-compra` en `app/main.py`, pero el nuevo flag `SAAS_RESERVAS_ENABLED=false` mantiene las tres rutas en 503 incluso si se activa `SAAS_AUTH_ENABLED`. Los endpoints rechazan identidades sin contexto/rol, y los servicios exigen RLS real, un único tenant visible, suscripción vigente con `permite_reservas` y permisos `EXECUTE` exclusivos a cada SP tenant. **No activar ningún flag hasta ejecutar pruebas reales del SP definitivo en el laboratorio separado, validar HTTP y provisionar SQL principals restringidos**. No se ha aplicado DDL contra las cinco empresas ni Supabase.
