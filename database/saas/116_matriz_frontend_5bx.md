# 116 — Matriz de integración Angular ↔ FastAPI SaaS (Etapa 5BX)

**Fecha:** 2026-10-31 · **Copia analizada (solo lectura):** `C:\SI2_Parcial1\fashionstore-web`
**Backend probado:** staging local 5BX (PostgreSQL 17 + FastAPI, modo SaaS ON y OFF).
**Alcance:** análisis y contrato. **No se modificó ningún archivo del repositorio Angular.**

---

## 0. Resumen ejecutivo

La aplicación Angular del primer parcial **no conoce el SaaS**: no existe ninguna
llamada a `/tenant/...`, no se envía `empresa_slug`, no hay selector de empresa y
los servicios siguen apuntando a las rutas legacy (`/productos`, `/usuarios`,
`/ventas/presencial`, `/reservas`, …).

Consecuencia medida en el staging: con `SAAS_AUTH_ENABLED=true`, el middleware
`BloqueoRutasLegacySaaS` responde **503** a todas las rutas legacy no migradas.
Por tanto, **encender SaaS sin tocar el frontend deja la interfaz administrativa
inoperante**. La activación exige un puente de rutas (este documento).

Estado por capa:

| Capa | Estado 5BX | Evidencia |
|---|---|---|
| Contrato HTTP backend A/B | **Funcional** (71/71 HTTP + 11/11 SQL) | `EVIDENCIA_STAGING_ON_5BX_*.json` |
| Modo OFF legacy | **Funcional con el DSN del legado** | `EVIDENCIA_STAGING_OFF_5BX_*.json` |
| Frontend Angular apuntando a SaaS | **NO integrado** (0 rutas `/tenant`) | este documento |
| CORS | **Habilitado** para `localhost:4200` y `127.0.0.1:4200` | `app/main.py` |

---

## 1. Cambios transversales (bloqueantes, afectan a toda la app)

### 1.1 Autenticación y sesión

| # | Archivo Angular actual | Cambio mínimo | Prioridad |
|---|---|---|---|
| T1 | `features/autenticacion-seguridad/auth/services/auth.service.ts` (`loginPersonal` → `POST /auth/personal/login`) | Nueva llamada `POST /auth/tenant/login` con `{correo, password, empresa_slug, contexto}`; guardar `empresa_slug` junto al JWT | **P0** |
| T2 | Idem (`loginCliente` → `POST /auth/clientes/login`) | `POST /auth/tenant/login` con `contexto:"cliente"` | **P0** |
| T3 | Idem (`cargarUsuario` → `GET /auth/me`) | `GET /auth/tenant/me` (devuelve `usuario_id, empresa_id, empresa_slug, empresa_nombre, rol, contexto, plan_codigo, permite_reportes_ia`) | **P0** |
| T4 | `core/interceptors/auth.interceptor.ts` (`RUTAS_LOGIN_SIN_TOKEN`) | Añadir `/auth/tenant/login` y `/auth/tenant/invitaciones/aceptar` | **P0** |
| T5 | `environments/environment.ts` (`apiUrl`) | Apuntar al backend SaaS del entorno (local `http://127.0.0.1:<puerto>` o staging cloud). **Requiere autorización del propietario del repo frontend** | **P0** |
| T6 | Manejo global de errores (`HttpErrorResponse`) | Mapear `503` → “módulo no habilitado en modo SaaS / ruta no migrada”, `403` → “sin permiso empresarial”, `409` → “límite de plan o conflicto”, `401` → cerrar sesión | **P0** |
| T7 | Login (pantalla) | Campo/selector de **empresa** (`empresa_slug`) o resolución por subdominio; hoy no existe | **P0** |

**Contrato exacto de login tenant** (verificado en staging):

```http
POST /auth/tenant/login
Content-Type: application/json

{"correo":"admin@staging.invalid","password":"…","empresa_slug":"empresa-a","contexto":"personal"}
→ 200 {"access_token","token_type","empresa_id","empresa_slug","empresa_nombre","rol","contexto"}
→ 401 credenciales o empresa no autorizada
```

`contexto` sólo admite `personal` o `cliente`. Un JWT de empresa A **nunca**
sirve para la empresa B (probado: 401 en login cruzado y 404 en recursos ajenos).

### 1.2 Riesgo de coexistencia (importante)

`SAAS_AUTH_ENABLED` es **global al proceso**, no por ruta. No se puede servir el
legacy y el SaaS tenantizado en la misma instancia sin un plan de corte. El
staging 5BX lo confirma: con ON, `/productos` y `/auth/personal/login` legacy
devuelven 503. Alternativas válidas para producción:

1. **instancia separada** para SaaS (recomendado), o
2. router de compatibilidad explícito (no existe todavía), o
3. mantener OFF hasta que el frontend esté migrado.

Además, apagar el flag **no basta** si se reutiliza el DSN restringido: el
principal `fs_saas_runtime` no tiene privilegios sobre `public.usuario`, por lo
que el login legacy responde 500 (medido). El legado debe conservar su propio
DSN y su propio proceso.

---

## 2. Matriz endpoint actual → endpoint tenant → cambio mínimo

Prioridad **P0** = imprescindible para operar la UI administrativa básica con
SaaS ON. **P1** = necesario para paridad funcional completa (hoy sin equivalente).

### 2.1 Autenticación

| Actual | Tenant | Cambio Angular | P |
|---|---|---|---|
| `POST /auth/personal/login` | `POST /auth/tenant/login` | payload + `empresa_slug` + `contexto` | P0 |
| `POST /auth/clientes/login` | `POST /auth/tenant/login` | `contexto:"cliente"` | P0 |
| `GET /auth/me` | `GET /auth/tenant/me` | ruta | P0 |
| — | `POST /auth/tenant/invitaciones/aceptar` | **pantalla nueva** de aceptación (`token`, `password`, `password_confirmacion`) | P1 |

### 2.2 Catálogo

| Actual | Tenant | Cambio Angular | P |
|---|---|---|---|
| `GET /productos/admin` | `GET /tenant/catalogo/productos` | ruta (misma forma de lista) | P0 |
| `GET /productos/admin/{id}` | `GET /tenant/catalogo/productos/{id}` | ruta | P0 |
| `POST /productos` | `POST /tenant/catalogo/productos` (`categoria_id,nombre,precio`) | ruta; hoy envía campos legacy extra | P0 |
| `PATCH /productos/{id}` | `PATCH /tenant/catalogo/productos/{id}` | ruta | P0 |
| `PATCH /productos/{id}/estado` | **sin equivalente** (`PATCH` de campos no incluye `estado`) | **bloqueo**: pedir endpoint o eliminar la acción | P1 |
| `POST /productos/{id}/variantes` | `POST /tenant/catalogo/productos/{id}/variantes` (`talla_id,color_id,sku`) | ruta | P0 |
| `PATCH /variantes/{id}` | `PATCH /tenant/catalogo/variantes/{id}` | ruta | P0 |
| `GET /categorias` | `GET /tenant/catalogo/categorias` | ruta | P0 |
| `GET /categorias/admin/{id}` | `GET /tenant/catalogo/categorias/{id}` | ruta | P0 |
| `POST/PATCH /categorias*` | `POST/PATCH /tenant/catalogo/categorias*` | ruta | P0 |
| `GET /catalogo/filtros`, `/promociones`, `/temporadas`, `/colecciones`, `/tallas`, `/colores`, `/recursos-producto` | **sin equivalente tenant** | P1 (no operativo con SaaS ON) | P1 |

### 2.3 Empresa, plan y sucursales

| Actual | Tenant | Cambio Angular | P |
|---|---|---|---|
| — (no existe) | `GET /tenant/empresa/me` · `GET /tenant/empresa/plan` · `GET /tenant/empresa/suscripcion` | **pantalla nueva** “Mi empresa” | P0 |
| `GET /sucursales` | `GET /tenant/empresa/sucursales` | ruta | P0 |
| `POST /sucursales` | `POST /tenant/empresa/sucursales` (`ciudad_id,nombre,direccion`) | ruta; mismo cuerpo | P0 |
| `PATCH /sucursales/{id}` | `PATCH /tenant/empresa/sucursales/{id}/estado` (`estado`) | **semántica distinta**: sólo estado | P0 |
| `GET /ciudades` | **sin equivalente tenant** | P1 (dependencia del alta de sucursal) | **P1→P0** |
| — | `PATCH /tenant/empresa/plan` | **bloqueado a propósito** (503 en el gate): el cambio de plan es de superadmin | — |

El límite de sucursales lo impone el backend: `BASICO=2`, `PROFESIONAL=10`
(probado: tercera sucursal en B → `409 Limite de sucursales activas alcanzado`).

### 2.4 Usuarios, personal y RBAC

| Actual | Tenant | Cambio Angular | P |
|---|---|---|---|
| `GET /usuarios` | `GET /tenant/empresa/usuarios` | ruta; forma de fila distinta (membresía) | P0 |
| `POST /usuarios` | `POST /tenant/empresa/usuarios` (`correo,nombres,apellidos,ci,sucursal_id,rol_id`) | **alta por invitación**: la respuesta 201 trae `invitacion.token` de un solo uso y no acepta `password` | P0 |
| `PATCH /usuarios/{id}/estado` | `PATCH /tenant/empresa/usuarios/{id}/estado` | ruta | P0 |
| `PATCH /usuarios/{id}` (rol/sucursal) | `PATCH …/usuarios/{id}/rol` y `…/sucursal` | **dos endpoints** | P0 |
| `GET /roles`, `GET /roles/{id}/permisos` | `GET /tenant/empresa/roles`, `GET /tenant/empresa/permisos` | ruta; matriz nominal por empresa | P0 |
| `POST/PATCH /roles*` | **sin equivalente** (roles globales legacy) | P1: la UI no debe crear roles globales en SaaS | P1 |
| `PATCH /roles/{id}/permisos` | `PATCH /tenant/empresa/roles/{id}/permisos` (`permisos:[{funcion,accion,permitido}]`) | **forma distinta** (lista nominal, no mapa legacy) | P0 |

### 2.5 Ventas, reservas, compras y devoluciones

| Actual | Tenant | Cambio Angular | P |
|---|---|---|---|
| `POST /ventas/presencial` (`reserva_id`, `items[]`) | `POST /tenant/ventas/contado` (`sucursal_id`, `operacion_id`, `lineas[{inventario_id,cantidad}]`) | **contrato nuevo**: exige `sucursal_id` y `operacion_id` (idempotencia). `estado` 201; replay 200 con `idempotente:true` | P0 |
| `POST /ventas/digital`, `/pagos/*`, `/reportes/*` | **sin equivalente tenant** | P1 | P1 |
| `GET /ventas/{id}/comprobante`, `/ventas/historial` | `GET /tenant/comercial/ventas` · `/tenant/comercial/ventas/{id}` | lectura básica disponible; **comprobante no existe** | P0/P1 |
| `POST /reservas` (carrito completo) | `POST /tenant/reservas` (`carrito_id`, `fecha_atencion` **con zona horaria**) | **cambio de payload**; `422` si la fecha no trae `Z`/offset | P0 |
| — | `POST /tenant/reservas/prevalidar` | recomendado antes de crear | P0 |
| `PATCH /reservas/{id}/cancelar` | `PATCH /tenant/reservas/{id}/cancelar` | ruta | P0 |
| `GET /reservas` | `GET /tenant/comercial/reservas` | ruta (recurso distinto) | P0 |
| `/reservas-sucursal/*`, `/atencion-reservas/*` | `PATCH /tenant/reservas/{id}/finalizar-sin-compra`; listas vía `/tenant/comercial/reservas` | **parcial**: no hay endpoints de atención dedicados | P1 |
| `GET /ordenes-compra`, `POST /ordenes-compra` | `GET/POST /tenant/ordenes-compra` | ruta | P0 |
| `POST /ordenes-compra/{id}/enviar` | idéntico bajo `/tenant` | ruta | P0 |
| `POST /ordenes-compra/{id}/recibir` | `POST /tenant/ordenes-compra/{id}/recepciones` (`operacion_id`, `lineas`) | **contrato nuevo** | P0 |
| `POST /ordenes-compra/{id}/detalles` | **no existe** (las líneas van en el POST) | **bloqueo**: revisar flujo de compras | P1 |
| `GET/POST /devoluciones`, `/{id}/aprobar|rechazar` | `GET/POST /tenant/devoluciones`, `/{id}/aprobar|rechazar|procesar` | ruta + `operacion_id` en `procesar` | P0 |
| `GET /devoluciones/ventas/{id}/disponibilidad` | **sin equivalente tenant** | P1: la UI debe calcular o pedir endpoint | P1 |

### 2.6 Resto de módulos sin equivalente tenant (P1, inertes con SaaS ON)

`/inventario`, `/movimientos-inventario`, `/proveedores`, `/promociones`,
`/temporadas`, `/colecciones`, `/tallas`, `/colores`, `/ciudades`, `/pagos`,
`/reportes/*`, `/bitacora`, `/asistencia-inteligente`, `/vestidor-virtual`.

Con SaaS ON estas llamadas reciben **503** del gate. No se pueden “arreglar”
desde Angular: requieren endpoints `/tenant/...` nuevos en el backend.

---

## 3. Contrato mínimo verificable (para el siguiente agente)

1. `POST /auth/tenant/login` con `empresa_slug` + `contexto`; guardar token y
   `empresa_slug` en `sessionStorage` (`vanter_access_token` + nuevo
   `vanter_empresa_slug`).
2. Interceptor: excluir del `Authorization` las dos rutas de login tenant.
3. `GET /auth/tenant/me` como fuente de identidad (rol, plan, contexto).
4. Renombrar servicios P0 con el prefijo `/tenant/...` y ajustar los payloads
   señalados como “contrato nuevo” (venta contado, recepción de compra,
   permisos por rol, creación de reserva).
5. Centralizar el mapa de errores `401/403/409/422/503`.
6. No introducir `empresa_id` desde el cliente: el backend lo deriva del JWT.
   Enviarlo **no** cambia el alcance (probado: campo extra → `422`).

## 4. Criterio de aceptación de la integración UI

- Login de empresa A y B con `empresa_slug` correcto; login cruzado rechazado.
- Catálogo y usuarios disjuntos entre A y B; recurso ajeno → 404.
- Límite de sucursales del plan visible y respetado (2 vs 10).
- Venta de contado con `operacion_id` idempotente (replay no duplica stock).
- Alta de personal por invitación y aceptación con token de un solo uso.
- Ninguna pantalla muestra 503 por rutas legacy no migradas en los flujos P0.
