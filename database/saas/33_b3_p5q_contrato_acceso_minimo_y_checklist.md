# Bloque 3 / Paso 5Q — Contrato de acceso restringido, sin instalar RLS

**Estado: 5Q PREPARADA, PENDIENTE de pruebas locales.** Paso 5 comercial EN PROCESO, Bloque 3 **4/7**. Rama exclusiva `feat/saas-multitenant-parcial2`.

## 1. Evidencia que fundamenta el contrato

La subfase 5P quedó validada en Windows con **`454 passed, 2 warnings in 7.06s`**. La auditoría SQL fue **READ ONLY** sobre `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h`, con `5P PREFLIGHT OK`: **46 tablas vacías**, **48 FK tenant validadas**, **cero policies** en las 16 tablas críticas examinadas, **cero SP nuevos instalados** y `ROLLBACK`.

Del informe real de 5P: las **16/16 tablas** de bootstrap/comercio consultadas tienen RLS habilitado, `force_rls=f`, owner `postgres` y cero políticas; **104 registros ACL/default sobre 13 tablas**, todos a `postgres`, sin grants a PUBLIC observados en esa muestra; **9 ACL específicas por columna NULL**, incluidas las de `usuario.password_hash`; solo `postgres` aparece entre los roles filtrados, y tiene `rolsuper=t` y `rolbypassrls=t`. Los cuatro SP de reservas heredados muestran `EXECUTE PUBLIC=t`. **Estos alcances son muestras filtradas; no prueban el estado de cada tabla o rol del clúster.**

`AuthService.autenticar_credenciales` verifica `password_hash` en Python mediante Argon2 **antes de tener un tenant confirmado**. `AuthRepository.obtener_usuario_activo_por_correo` ejecuta `select(Usuario)` con `joinedload(Usuario.rol)`, y posteriormente `TenantAuthRepository.obtener_membresia_vigente` usa `usuario_empresa`, `empresa`, `rol`, `suscripcion`, `plan_saas`, `cliente`/`empleado`/`sucursal`. Crear una policy que exija `app.empresa_id` en todas esas consultas bloquearía el login por circularidad.

## 2. Resultado implementado de 5Q: declaración verificable, NO permisos aplicados

Se incorporó una **matriz machine-readable** `database/saas/32_b3_p5q_matriz_limites_roles_NO_APLICAR.json`, expresamente `estado=BORRADOR_NO_APLICABLE` y `desplegable=false`. No contiene contraseñas, URL de conexión ni SQL ejecutable. Define tres posibles funciones futuras, **NO roles creados**:

| Principal conceptual | Responsabilidad prevista | Prohibición o pendiente |
|---|---|---|
| `bootstrap` | Verificar credenciales/membresía antes de conocer empresa; la API necesita un hash Argon2 para comprobar password | No `SELECT` general de `usuario` a clientes ni rol comercial; interfaz restringida todavía POR DISEÑAR |
| `comercial` | Ejecutar operaciones permitidas del tenant con filtros y RLS efectivos | No leer `password_hash`, no `CALL` legacy, no acceso directo desde frontend, no `CALL` tenant hasta ensayos |
| `auditoria` | Consulta futura de bitácora autorizada por empresa | Sin hash, sin escrituras comerciales; política de eventos globales y actor/IP aún POR DISEÑAR |

El archivo define **8 recursos agrupados** y **7 controles bloqueantes abiertos**. Incluye dependencias de login, operaciones prohibidas y matriz de acceso por rol; **no es una instrucción de GRANT** ni garantiza que las columnas y permisos indicados ya estén configurados en PostgreSQL.

Se incorporó `database/saas/23_validar_diseno_acceso_5q.py`, verificador **OFFLINE de solo lectura de código y JSON**. Rechaza cambiar `desplegable` a verdadero, activar `postgres` en API, permitir SQL directo a clientes, tratar GUC como autenticación, entregar `usuario.password_hash` al principal comercial, permitir `CALL` nuevos/legacy, marcar bloqueos resueltos sin pruebas y eliminar restricciones. También revisa que `SAAS_AUTH_ENABLED` siga apagado por defecto, que el gate no habilite la ruta transaccional de reservas, que los tres SP sigan en `.sql.txt` y que el flujo de login del código no haya cambiado sin revisar el contrato. **No importa FastAPI/SQLAlchemy, no conecta a PostgreSQL, no ejecuta SQL ni lee secretos.**

`tests_saas/test_limites_roles_rls_5q.py` agrega **16 pruebas unitarias ESTÁTICAS**, incluyendo mutaciones negativas intencionales de la matriz para comprobar denegación de configuraciones inseguras. Total esperado de tests SaaS: **`470 passed`** = 454 previos + 16; **no validado aún en Windows**.

## 3. Decisiones críticas todavía no tomadas

### A. Bootstrap de contraseña y membresía

**No basta con separar conexiones.** Si una credencial técnica de bootstrap permite `SELECT` general sobre `usuario.password_hash`, una credencial robada o SQL inyectado puede enumerar hashes aunque las rutas HTTP sean correctas. Un futuro mecanismo acotado de verificación deberá revisar proyección ORM (hoy `select(Usuario)` carga el registro), scopes de consulta, datos devueltos, rate limit del backend, revocación, tratamiento de error y funciones privilegiadas sin exposición de otros tenants. Una función `SECURITY DEFINER` que devuelve hashes según correo tampoco es automáticamente segura; requiere owner limitado, `search_path` fijo, verificación de argumentos, revisión de permisos y riesgo de enumeración.

**Opciones abiertas:** servicio bootstrap aislado con derechos estrictos y consulta concreta, interfaz especializada en esquema protegido o proveedor de identidad externo. Ninguna es política instalada; **NO usar `USING(true)` ni `GRANT SELECT ON usuario TO PUBLIC` como atajo**.

### B. Operaciones de reserva `SECURITY INVOKER`

Los 3 SP nuevos siguen redactados como `.sql.txt`, no instalados. Los constructores de 5O toman IDs de `TenantContext`, pero `TenantContext` es instanciable por código Python y el principal SQL puede falsificar los GUC si ejecuta SQL arbitrario. Una credencial DB compartida y políticas que dependan solo de `current_setting('app.empresa_id')` son defensa en profundidad **contra errores del backend confiable**, NO aislamiento fuerte frente a inyección SQL o robo de credencial.

Adicionalmente, los SP `SECURITY INVOKER` requieren permisos de las tablas y de escritura transitiva de triggers: `UPDATE carrito` al tocar `detalle_carrito` e `INSERT bitacora` al modificar otras tablas. Es necesario decidir si dar acceso mínimo a un rol de servicio **con RLS real** o diseñar una interfaz privilegiada cuidadosamente auditada. No aprobar `GRANT ALL` o acceso SQL a clientes finales.

### C. RLS, roles y transacciones

Los roles en PostgreSQL tienen alcance de **CLÚSTER**; crear un rol afecta el espacio de nombres incluso aunque se haga conectado al sandbox. Una futura fase deberá contar con **autorización explícita** para creación de roles y políticas, plan de revocación compatible con legacy, datos exclusivamente ficticios en sandbox, y pruebas reales de `row_security_active()`, `USING`/`WITH CHECK`, SELECT ajeno oculto, cambios de empresa rechazados, bitácora, Kardex, transacciones atómicas y concurrencia. El uso de `postgres` o de un usuario con `BYPASSRLS` invalida la prueba de aislamiento.

## 4. Comandos de validación que debe ejecutar el estudiante

Desde PowerShell, en `C:\SI2_Parcial1\fashionstore-api`, con Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Esperado: `470 passed`, sin fallo (todavía NO confirmado).**

Solo si pytest pasa:

```powershell
python .\database\saas\23_validar_diseno_acceso_5q.py
```

Resultado esperado de la herramienta (no es un resultado de seguridad PostgreSQL):

```text
5Q VALIDACION ESTATICA OFFLINE — NO APLICA SQL NI ROLES
5Q MATRIZ CONSISTENTE: 8 recursos, 7 bloqueantes sin resolver
5Q BORRADOR NO DESPLEGABLE; RLS POSTGRESQL REAL NO VALIDADO
```

**No se necesita contraseña PostgreSQL ni se ejecuta PowerShell de auditoría SQL en 5Q.** Compartir ambas salidas textuales para validar esta subfase.

## 5. Límites de la entrega y criterio de cierre

`SAAS_AUTH_ENABLED=false` por defecto y rutas de reserva/pago/venta transaccional bloqueadas. **No se modificaron** `main`, Supabase, la base histórica restaurada ni el sandbox en esta subfase. No crear rol, policy, GRANT, REVOKE, seed, procedimiento ni `CALL` sin acuerdo expreso en una etapa posterior.

Se podrá **cerrar 5Q exclusivamente como diseño y validación estática** cuando pytest confirme las 470 pruebas y el validador acepte la matriz como **no desplegable**. Eso NO resuelve aún el bootstrap RLS, la identidad SQL ni el aislamiento frente a GUC manipulado, y **no completa el Paso 5 comercial** (Bloque 3 permanece **4/7**). Esperar `siguiente` para abrir otra subfase.
