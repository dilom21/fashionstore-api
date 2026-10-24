# Bloque 3 — Paso 5O: Contrato seguro de invocación backend y bootstrap RLS

**Estado: CÓDIGO INERTE PREPARADO EN RAMA FEATURE, PRUEBAS LOCALES PENDIENTES.** Paso 5 comercial EN PROCESO. Bloque 3 4/7. Aún quedan pendientes de resolver y probar los bloqueos de identidad SQL/RLS.

## Alcance protegido

Trabajar solo en `feat/saas-multitenant-parcial2`. No modificar `main`, Supabase, el PostgreSQL restaurado `fashionstore_restore_20261023_193554_594` ni el sandbox local `fashionstore_saas_reservas_sandbox_5h`. Prohibido en esta subfase crear `ROLE`, `POLICY`, `GRANT`, `REVOKE`, seed, instalar SP o ejecutar `CALL`. Mantener `SAAS_AUTH_ENABLED=false` y las operaciones de reserva/venta/pago del SaaS bloqueadas.

### Evidencia recibida de 5N

- `424 passed, 2 warnings in 6.99s` en Windows; 5 guardas `OK`.
- Se aplican `app.usuario_id` y `app.empresa_id` DESPUÉS de verificar JWT y membresía, y se reponen en transacciones nuevas de esa sesión mediante `after_begin`.
- Tres bloqueos vigentes: `bootstrap_login_sin_politicas_definidas`, `args_sp_no_vinculados_al_principal_sql_autenticado` y `guc_empresa_no_es_identidad_independiente`.
- PostgreSQL sandbox: última auditoría READ ONLY registró 46 tablas vacías, 48 FK tenant, ninguna política en tablas estudiadas, sin SP tenant instalados. **No se verificó de nuevo la base en 5O.**

## 1. Cambio funcional de 5O: interfaz de CALL *preparada*, nunca ejecutada

Archivo `app/modules/reservas/services/tenant_sp_calls_5o.py`. Se incorporaron tres constructores para los borradores 5G:

| Constructor Python | Borrador de procedimiento | Contexto local mínimo |
|---|---|---|
| `preparar_creacion_reserva(tenant, carrito_id, fecha_atencion, observacion)` | `sp_crear_reserva_tenant` | `contexto=cliente`, `rol_nombre=CLIENTE` |
| `preparar_cancelacion_reserva(tenant, reserva_id, observacion)` | `sp_cancelar_reserva_tenant` | CLIENTE o ADMINISTRADOR / ENCARGADO_SUCURSAL en personal |
| `preparar_finalizacion_sin_compra(tenant, reserva_id, observacion)` | `sp_finalizar_reserva_sin_compra_tenant` | ADMINISTRADOR, ENCARGADO_SUCURSAL o CAJERO en personal |

**Regla clave:** no admiten `usuario_id` ni `empresa_id` como argumentos de petición: ambos se copian **exclusivamente de `TenantContext`**, obtenido por la dependencia autorizadora SaaS. Las sentencias usan parámetros ligados `:empresa_id`, `:usuario_id`, etc. Se exige ID positivo y fecha de atención con zona horaria. Se devuelve objeto `LlamadaSPTenant(sentencia, parametros)` inmutable en su estructura y con mapa `MappingProxyType` de parámetros. Ninguna función abre conexión, hace `Session.execute`, `commit`, `rollback`, `CREATE`, ni invoca el SP. `p_reserva_id` se prepara como `None` por ser `INOUT`; su semántica PostgreSQL aún debe probarse en sandbox **en una fase futura autorizada**.

La validación preliminar de `rol_nombre` es defensa de capa HTTP y **no reemplaza** membresía/estado/sucursal/permisos `rol_funcion` ni comprobaciones de la transacción SQL. La clase `TenantContext` es instanciable por código Python; los constructores **confían en que el servicio de backend reciba el objeto desde `get_current_tenant_context`**, no ofrecen garantía autónoma de autenticación. No integrar estos constructores a endpoints ni instalar sus SP hasta resolver los riesgos pendientes.

## 2. Separación del bootstrap de login y las operaciones (diseño aún NO ejecutable)

**Hecho observado:** `/auth/tenant/login` llama primero a `AuthService.autenticar_credenciales`, y después a `TenantAuthRepository.obtener_membresia_vigente`. La consulta necesita leer `usuario`, `usuario_empresa`, `empresa`, `rol`, `suscripcion`, `plan_saas`, perfiles `cliente/empleado` y sucursal *antes* de la existencia de un contexto de empresa validado. RLS sin políticas bloquea esa consulta con una cuenta restringida.

**Propuesta A (preferida para evaluar, no aprobada):** separar **rol de autenticación** y **rol comercial** con credenciales distintas y restringidas, usadas exclusivamente desde backend, con conexiones/pools independientes. El rol de bootstrap solo podría validar credenciales y resolver una membresía activa de la empresa solicitada mediante una interfaz con contrato estrecho; evitar conceder acceso genérico `SELECT *` a `usuario`, hashes o membresías de otras empresas. Su implementación puede requerir una función en esquema privado con permisos controlados, owner NO superusuario, `search_path` fijo y firma/revisión de todas las dependencias; **no crear aún funciones `SECURITY DEFINER` o policies** hasta inspeccionar seguridad y probar no-recursión.

El rol comercial (futuro) no tendría `SUPERUSER`, `BYPASSRLS`, propiedad de tablas o permisos de administración. Una vez autorizado JWT y membresía, la API asignaría `app.empresa_id` y `app.usuario_id` por transacción y cada operación SQL tendría filtros de empresa y propiedad, más policies `USING/WITH CHECK` específicas. Los triggers de bitácora requieren `INSERT` compatible y los de carrito requieren `UPDATE` transitorio. Se deben auditar `EXECUTE` por defecto en los cuatro SP antiguos.

**Riesgo importante:** roles PostgreSQL pertenecen al CLÚSTER, no a la base; una sola creación de rol puede afectar otra base del equipo. No crear roles sin autorización explícita y un plan de acceso/concesiones/rollback con alcance global evaluado.

## 3. Límite de confianza explícito (no afirmar que GUC es autenticación)

El diseño objetivo inmediato presupone un **backend HTTP de confianza**, con SQL parametrizado, secretos fuera del repositorio y clientes sin acceso directo al puerto PostgreSQL. El contexto de la conexión se deriva de JWT/membresía, y RLS sirve como defensa adicional frente a errores de filtrado.

**Esto NO resiste SQL arbitrario bajo la credencial de servicio**. Un atacante con SQL injection o acceso a esa credencial puede modificar `app.empresa_id` / `app.usuario_id`, y `CALL ... (p_empresa_id, p_usuario_id)` con IDs de otra membresía válida. El constructor Python reduce el riesgo de que el **backend use IDs del cuerpo HTTP**, pero **NO vincula criptográficamente** el invocador de PostgreSQL con `p_usuario_id`. Así, el bloqueo de suplantación SQL **NO se declara solucionado** en 5O.

Para exigir resistencia frente a SQL arbitrario, estudiar en una fase futura un modelo de **principal PostgreSQL autenticado distinto por tenant o un mecanismo DB-side verificable no falsificable**, teniendo en cuenta el coste de roles, pool de conexiones y privilegios globales. No suponer que una función `SECURITY DEFINER`, por sí sola, elimina el riesgo.

## 4. Matriz mínima de pruebas futuras (NO ejecutadas)

| Escenario | Resultado requerido en futura prueba PostgreSQL |
|---|---|
| Login antes de contexto tenant | Resolver exclusivamente el usuario/empresa solicitados; negar accesos a terceros y a hashes de contraseñas fuera del flujo de auth |
| Principal SQL sin RLS bypass | Acceso propio permitido y ajeno bloqueado (empresa A/B) bajo policies + grants mínimos |
| Cliente envía `empresa_id` o `usuario_id` ajenos en HTTP | La API no los acepta como identidad de llamadas de reserva |
| Cliente intenta crear con contexto personal | Denegación temprana en contrato y en procedimiento por rol |
| `CALL` SQL directo con IDs ajenos | Debe quedar denegado por modelo DB-side final, o documentado como fuera del límite de confianza del backend; **no aprobado en 5O** |
| Fallo del trigger `bitacora` o Kardex | Rollback de reserva, stock y movimientos, sin operaciones parciales |
| Concurrencia sobre mismo stock | No duplicar reservas y nunca dejar stock inconsistente |

## 5. Pruebas de 5O y paso a ejecutar por estudiante

Nuevo archivo `tests_saas/test_contrato_invocacion_sp_5o.py`: **16 pruebas unitarias aisladas** de construcción de argumentos, contextos válidos e inválidos, IDs, fecha con zona, inmutabilidad del mapa, firmas de borradores y ausencia de operaciones SQL. No crean BD ni llaman procedimientos.

Suite anterior: **424 passed** confirmados. Esperado ahora: **440 passed**; todavía NO validado en Windows.

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

No ejecutar más scripts SQL/psql o automatizaciones en 5O. Si pytest falla, compartir el error completo antes de avanzar.

## 6. Estado

**5O PREPARADA**, sin integración ejecutable de reservas ni políticas/roles. **Paso 5 EN PROCESO y Bloque 3 4/7**. `SAAS_AUTH_ENABLED=false`; no alterar `main`, Supabase o bases PostgreSQL. Tras confirmar las pruebas, evaluar cuidadosamente cuál es el próximo cambio mínimo sin credenciales SQL nuevas. Esperar a que el estudiante diga **`siguiente`** antes de avanzar a 5P.
