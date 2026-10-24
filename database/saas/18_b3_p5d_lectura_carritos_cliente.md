# Bloque 3 — Paso 5D: consulta segura de carritos propios (CU15)

**Estado:** **SUBFASE 5D VALIDADA** por el estudiante en Windows: `231 passed, 2 warnings in 5.16s` (203 previas + 28 nuevas), sin fallos. Paso 5 continúa EN PROCESO. No tocar `main`, Supabase ni PostgreSQL local; mantener `SAAS_AUTH_ENABLED=false`.

## Objetivo

Completar las **lecturas de CU15** para el perfil cliente del tenant, sin reutilizar repositorios legacy ni consultas sin filtro por empresa. Mantener la separación entre carrito y reserva: consultar un carrito NO cambia existencias, fechas, estados o cantidades.

### Rutas nuevas

| Método | URL | Resultado |
|---|---|---|
| GET | `/tenant/carritos` | Lista paginada de carritos ACTIVO y vigentes del cliente del tenant. Resumen por carrito: sucursal, cantidad de líneas, unidades, subtotal, última actividad; total global independiente de paginación |
| GET | `/tenant/carritos/{carrito_id}` | Detalle de carrito propio ACTIVO y vigente: productos, SKU, talla, color, temporada, cantidad, precio unitario, stock disponible y subtotal por línea y global |

El detalle **NO expone `empresa_id`, `cliente_id`, datos privados de otros clientes, imágenes ni URLs de recursos**. Las imágenes necesitan un filtrado SaaS independiente para `recurso_producto`, por eso no se añaden en esta fase.

## Condiciones de seguridad y comportamiento

1. **Identidad:** `get_current_tenant_cliente` verifica contexto `cliente`. `CarritoTenantReadRepository._cliente_activo` comprueba perfil real con `cliente.empresa_id`, `cliente.usuario_id` y `estado=true`, aunque el token tenga una membresía válida. El cliente HTTP no puede determinar el tenant pasando `?empresa_id=` o `?cliente_id=`.
2. **Carrito:** la consulta exige `carrito.empresa_id` y `carrito.cliente_id` del perfil resuelto. Además, `sucursal.empresa_id` debe coincidir. Un ID ajeno o inexistente -> 404, un carrito propio pero no ACTIVO o vencido (más de 2 horas) -> 409. El listado solamente muestra ACTIVO y vigentes, sin cambiar sus estados.
3. **Líneas:** se proyectan columnas explícitas con SQLAlchemy Core, no objetos ORM con relaciones lazy. El `JOIN` exige `empresa_id` coincidente en `detalle_carrito`, `inventario`, `variante_producto`, `producto`, `categoria` y `temporada`. Los catálogos globales `talla` y `color` se unen por ID. `inventario.sucursal_id` debe corresponder a la sucursal del carrito.
4. **Fail-closed:** si existe una línea corrupta cuyo JOIN no coincide con el tenant/cadena, se rechaza el detalle o la lista completa con 409; nunca se muestra una suma parcial engañosa ni columnas ajenas.
5. **Lectura histórica:** una línea que era válida puede mostrarse aunque se haya desactivado el producto posteriormente, igual que en la lógica legacy; las mutaciones PATCH siguen validando `estado` activo y pueden rechazarla.
6. **Paginación:** `limit` 1 a 100, `offset` no negativo, orden descendente por actividad/ID. `total_carritos_activos` representa el total del cliente antes de paginar; no solamente la cantidad de la página.
7. **Sin efecto lateral:** el código nuevo utiliza exclusivamente `SELECT`, sin `UPDATE`, `INSERT`, `DELETE`, `commit`, `FOR UPDATE` ni llamadas a SP de PostgreSQL. Los GET no expiran ni reviven carritos.
8. **Guard:** abre únicamente `GET /tenant/carritos` y `GET /tenant/carritos/{id positivo sin cero inicial}`; sigue denegando `/carritos` legacy y GET a subrutas no auditadas.

## Archivos
- `app/modules/carrito/repositories/tenant_read_repository.py`: SELECT y proyecciones empresariales, validación de perfil, sumas y paginación.
- `app/modules/carrito/api/tenant_read_router.py`: modelos de respuesta limitados y rutas GET con dependencia cliente.
- `app/core/saas_route_gate.py`: allowlist de dos GET exactos.
- `app/main.py`: registra el router nuevo, sin alterar el legado.
- `tests_saas/test_tenant_carrito_transacciones.py`: 28 pruebas añadidas de lecturas (incluyendo parametrizadas) a las 203 ya existentes. Se conserva el mismo fixture SQLite de dos empresas y se actualiza un test previo del guard.

## Verificación del estudiante

PowerShell en `C:\SI2_Parcial1\fashionstore-api` con conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Resultado confirmado en PowerShell:** **231 passed, 2 warnings in 5.16s** (203 anteriores + 28 nuevas), 0 fallos. El `git pull --ff-only` sincronizó la rama hasta `de12222` antes de ejecutar pytest. No ejecutar una migración ni pruebas DML contra PostgreSQL para esta lectura. No es necesario repetir el preflight local: la integridad de carrito e inventario (48 FK tenant, 48 carritos, 274 inventarios) ya se verificó con READ ONLY en 5B.

## Límites no resueltos

- Los tests HTTP usan SQLite en memoria, usuarios/tenants ficticios y suplantación intencional del contexto **solo en el test**; no verifican login y membresías reales en PostgreSQL ni RLS.
- Las lecturas no prueban concurrencia ni el comportamiento de los triggers de base de datos. Las pruebas PostgreSQL de autenticación, escrituras con rollback y rol sin `BYPASSRLS` siguen pendientes.
- Las consultas de cliente de reserva, compra digital, stock reservado, pago, compras y devoluciones no se habilitan por estas rutas. El acceso de administrador a resúmenes comerciales sigue separado.
- El Paso 5 sigue EN PROCESO después de 5D, porque falta adaptar `sp_crear_reserva_desde_carrito` y el resto de transacciones comerciales. No pasar a Paso 6.

**Criterio de salida de 5D: ALCANZADO** para la suite SQLite con identidades simuladas. El Paso 5 sigue EN PROCESO porque no están migradas ni verificadas las transacciones de reserva, venta, pago, compra y devolución. Detenerse hasta que el estudiante diga `siguiente`.

## Evidencia de ejecución recibida

Con el entorno Conda `fashionstore-api`, el estudiante ejecutó `python -m pytest -q tests_saas` y obtuvo **231 aprobadas, 0 fallos, 2 advertencias en 5.16 segundos**. Las advertencias son `StarletteDeprecationWarning` por `httpx`/`starlette.testclient` y por `HTTP_422_UNPROCESSABLE_ENTITY`, ya conocidas de subfases anteriores.

No se ejecutó ninguna sentencia SQL ni prueba nueva contra PostgreSQL en esta subfase. La verificación de integridad local de la subfase 5B fue READ ONLY y no demuestra aún las lecturas SaaS con autenticación real en PostgreSQL. **Cierre: 5D validada dentro de su alcance ficticio; mantener `SAAS_AUTH_ENABLED=false`, `main` intacta y Supabase sin cambios.**
