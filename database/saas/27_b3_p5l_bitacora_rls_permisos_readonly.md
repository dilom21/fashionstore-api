# Bloque 3 / Paso 5L — Auditoría de bitácora y permisos RLS (SOLO LECTURA)

**Estado: SUBFASE 5L VALIDADA en alcance estático/READ ONLY y dictamen de los cuerpos SQL originales recibidos.** Paso 5 comercial EN PROCESO; Bloque 3 4/7 pasos completados.

**Restricciones:** NO crear roles, usuarios, políticas, grants/revokes, seeds, migraciones, triggers o procedimientos; NO ejecutar `CALL` ni activar SaaS. Preservar `SAAS_AUTH_ENABLED=false`; `main`, Supabase y la base restaurada `fashionstore_restore_20261023_193554_594` no se modifican. Base única de auditoría: `127.0.0.1:5432 / fashionstore_saas_reservas_sandbox_5h`, transacción `READ ONLY` con `ROLLBACK`.

## 1. Evidencia previa de 5J / 5K

5K confirmó por PowerShell **`384 passed, 2 warnings in 6.05s`** y `5K PREFLIGHT OK` sin escrituras. De 20 tablas con RLS habilitado en 5J, **0 políticas RLS**. Seis funciones distintas son llamadas por diez triggers y todas son `SECURITY INVOKER`. Dos carecen de `SET search_path` propio.

La función `fn_registrar_bitacora()` obtiene `app.usuario_id` de `current_setting(...,TRUE)`, determina `empresa_id` desde la fila y ejecuta `INSERT INTO public.bitacora`; el valor GUC `app.usuario_id` **no constituye prueba de identidad**. En 5K también apareció `fn_completar_ip_bitacora()`, pero solo como metadata, sin su cuerpo. **Esto impide todavía dar por seguros los registros de auditoría, los permisos de escritura del futuro SP `SECURITY INVOKER` y la integridad de IP/actor.**

5K halló `EXECUTE` a `PUBLIC` en diez rutinas (6 funciones trigger y 4 SP heredados). El permiso `EXECUTE` no basta para leer tablas sujetas a RLS, pero es una superficie a revisar antes de habilitar una cuenta de aplicación no superusuario. No se revoca nada en 5L.

## 2. Qué inspeccionamos sin escribir

Archivos nuevos:
- `database/saas/19_auditar_bitacora_y_rls_sandbox.sql`: `BEGIN TRANSACTION READ ONLY` + `ROLLBACK`. Preflight que exige nombre de BD, host local, **46 tablas públicas vacías**, 48 FK tenant, 0 policies en el conjunto comercial previo, 0 `sp_*_tenant` instalados. Revisa estructura exacta de `bitacora` (columnas/NULL/DEFAULT/IDENTITY), constraints, índices, políticas específicas, RLS `relrowsecurity`/ `relforcerowsecurity`, ACL, triggers, cuerpos `pg_get_functiondef` de `fn_registrar_bitacora` y `fn_completar_ip_bitacora`, `EXECUTE` a `PUBLIC`, permisos de roles existentes y secuencias vinculadas.
- `database/saas/19_auditar_bitacora_y_rls_sandbox.ps1`: conecta solo a `127.0.0.1` y la base sandbox, `default_transaction_read_only=on`, solicita clave **local** de forma segura, no muestra ni almacena la contraseña, y guarda el informe en `$env:TEMP\fashionstore_5l_bitacora_rls_sandbox_LOCAL_REVISAR.txt` fuera del repositorio. Se niega a sobrescribir el archivo si existe.
- `tests_saas/test_bitacora_rls_preflight_5l.py`: **16 pruebas ESTÁTICAS**; no conectan a PostgreSQL ni ejecutan funciones.

**Resultado REAL confirmado de suite completa: `400 passed, 2 warnings in 6.48s`**, = 384 casos previos + 16 nuevos de 5L, sin fallos.

## 3. Propuesta de diseño RLS sin DDL ni credenciales

| Componente | Regla deseada para estudiar | Bloqueador a verificar |
|---|---|---|
| `bitacora` | Escritura de evento solo dentro de operación tenant autorizada; lectura solo de auditoría privilegiada de esa empresa | RLS/owner/ACL reales, campos `empresa_id` y FK, IP y actor |
| `fn_registrar_bitacora()` | Actor derivado del contexto validado en backend y no de body HTTP | `app.usuario_id` es GUC de sesión modificable; no prueba identidad ante SQL malicioso |
| `fn_completar_ip_bitacora()` | No confiar en cabeceras IP sin validación del proxy, ni permitir valores arbitrarios en auditoría | Falta revisar código real y secuencia de triggers |
| `carrito` y `detalle_carrito` | `UPDATE` de actividad permitido solo para cliente/empresa de la transacción | Trigger de detalle ejecuta `UPDATE carrito`, que requiere permisos/RLS/locks |
| `reserva` e `inventario` | Cambios únicamente a través del servicio de reserva con tenant confiable | Procedimientos `SECURITY INVOKER` necesitan SELECT/UPDATE/INSERT de varias tablas, RLS `USING` y `WITH CHECK` |
| Rol SQL de ensayo | `NOSUPERUSER`, `NOBYPASSRLS`, `NOCREATEDB`, `NOCREATEROLE`, acceso mínimo a una base | `CREATE ROLE` es global al clúster; aún no autorizado |

**Modelo de confianza a revisar:** el JWT firmado identifica usuario/tenant, pero la autorización se vuelve a contrastar en PostgreSQL (`usuario_empresa`/plan/suscripción). Solo el backend de confianza puede seleccionar el tenant por transacción; `current_setting('app.empresa_id')` o `app.usuario_id` por sí solos **no autentican al usuario**, pues una sesión SQL con acceso arbitrario puede configurarlos. Evitar conceder a clientes finales un acceso PostgreSQL directo con credenciales compartidas de backend.

Aún no definimos `CREATE POLICY`: deberán contemplarse tablas globales vs. empresariales, `USING`, `WITH CHECK`, SELECT requeridos por SP, triggers de bitácora sin recursión, grants mínimos y un rol de servicio que no sea propietario de tablas. Probar autorización de empresa A/B con datos académicos totalmente ficticios y cuenta real no superusuario será tarea FUTURA, no 5L.

## 4. Comandos Windows que debe ejecutar el estudiante

Desde `C:\SI2_Parcial1\fashionstore-api` con Conda `fashionstore-api`:

```powershell
git pull --ff-only origin feat/saas-multitenant-parcial2
python -m pytest -q tests_saas
```

**Confirmado: `400 passed, 2 warnings in 6.48s`, sin fallos.** El estudiante ejecutó después la auditoría READ ONLY en el sandbox, con preflight aprobado; no necesita repetir estos comandos.

Solo con la suite aprobada:

```powershell
.\database\saas\19_auditar_bitacora_y_rls_sandbox.ps1
```

El script anuncia `5L PREFLIGHT OK`, luego `ROLLBACK` y `AUDITORIA 5L SANDBOX READ ONLY COMPLETADA`. No añade, borra ni altera ningún objeto o fila.

Después, revisar el archivo generado antes de enviarlo al chat:

```powershell
notepad "$env:TEMP\fashionstore_5l_bitacora_rls_sandbox_LOCAL_REVISAR.txt"
```

Las definiciones pueden contener literales sensibles; **no subir este informe a GitHub, no pegar contraseñas**, no eliminar el archivo anterior para reintentar si ya existe: reportar el error para diagnóstico.

## 5. Criterios de cierre y continuación

**5L CERRADA exclusivamente como auditoría estática/READ ONLY + dictamen del contenido recibido**. Esto NO certifica que RLS funcione con un rol restringido ni autoriza DDL/DML. Las decisiones técnicas están en la sección 6.

**Aún no crear roles o políticas ni ejecutar seed**; la creación futura de roles afecta al CLÚSTER y necesita autorización expresa. Mantener `SAAS_AUTH_ENABLED=false`, `main`, Supabase y fuente restaurada sin cambios. Paso 5 comercial EN PROCESO, Bloque 3 4/7, y esperar `siguiente` para avanzar a otra subfase.

## 6. RESULTADO REAL DEL ESTUDIANTE Y DICTAMEN SOBRE BITÁCORA

**Evidencia de Windows:** fast-forward `89dfea6..ac411b5`; `python -m pytest -q tests_saas` devolvió **`400 passed, 2 warnings in 6.48s`** (0 fallos). Las advertencias son las deprecaciones Starlette/httpx y `HTTP_422_UNPROCESSABLE_ENTITY`, ya observadas en 5K.

**Evidencia PostgreSQL READ ONLY:** el estudiante ejecutó `19_auditar_bitacora_y_rls_sandbox.ps1` en `127.0.0.1:5432/fashionstore_saas_reservas_sandbox_5h`. `5L PREFLIGHT OK` confirmó 46 tablas vacías, incluida `bitacora`, 48 FK tenant, cero políticas comerciales y cero SP tenant. El informe adjuntado comienza `BEGIN`, `SET`, `SET`, `DO`, termina `ROLLBACK`, y el PowerShell imprimió `AUDITORIA 5L SANDBOX READ ONLY COMPLETADA`. No se ejecutaron triggers ni DDL/DML.

### 6.1 Estructura y políticas RLS reales de public.bitacora

- **8 columnas:** `id` (bigint IDENTITY), `usuario_id` nullable, `fecha_hora` NOT NULL con default `CURRENT_TIMESTAMP`, `ip` varchar(45) nullable, `accion` varchar(150) NOT NULL, `entidad_afectada` varchar(100) nullable, `descripcion` texto nullable y `empresa_id` bigint nullable.
- **7 restricciones** según el catálogo PostgreSQL (incluye PK id, UNIQUE `(empresa_id,id)`, 2 FK, y tres NOT NULL). La FK `empresa_id` referencia `empresa(id)` y la FK `usuario_id` referencia `usuario(id)` con ON DELETE SET NULL. No se observó en la tabla bitácora una FK tenant compuesta que verifique que `usuario_id` pertenezca a `empresa_id`: **ambas referencias son independientes**, y además ambas son nullable. Esto puede ser legítimo para eventos de usuario global, pero **no certifica la pertenencia del actor al tenant de cada evento**.
- **4 índices** (`bitacora_pkey`, `idx_bitacora_entidad_fecha`, `idx_bitacora_usuario_fecha`, `uq_tenant_bitacora_id`). Existe la secuencia `bitacora_id_seq`, propiedad de `postgres`, ACL `<DEFAULT>`; ello NO certifica permisos de inserción para un rol distinto.
- **RLS `relrowsecurity=t`, `relforcerowsecurity=f` y 0 políticas** para `bitacora`. También las otras siete tablas consultadas (carrito, cliente, empresa, inventario, movimiento_inventario, reserva, usuario) tienen RLS habilitado y 0 políticas. Ninguna política se agregó.
- ACL de tabla auditada: el único beneficiario listado es **`postgres`** (8 privilegios). **No aparecen grants a `PUBLIC` en bitacora**. En la sección de roles con privilegios efectivos aparece solo `postgres`, `rolsuper=t` y `rolbypassrls=t`, con INSERT/SELECT bitácora permitidos. **No hay ejecución comprobada con cuenta API restringida**.

### 6.2 Funciones originales y riesgo de actor/IP

- **Trigger `trg_bitacora_completar_ip`**: BEFORE INSERT en `public.bitacora`, activo (`tgenabled=O`), función `fn_completar_ip_bitacora()`, con `SECURITY INVOKER` (`prosecdef=f`) y `search_path=public, pg_temp`.
- Cuerpo real `fn_completar_ip_bitacora()`: si `NEW.ip` está NULL o vacía, lee `current_setting('app.ip', TRUE)`; si el valor es no vacío, **copia ese texto a `NEW.ip`**. No consulta `inet_client_addr()`, no hace verificación sintáctica de IPv4/IPv6 y no demuestra que el valor venga de un proxy confiable. Si el INSERT ya trae IP no vacía, **la deja tal cual**. `varchar(45)` limita longitud, no autentica origen.
- `fn_registrar_bitacora()`: también es `SECURITY INVOKER`; toma `app.usuario_id` como string configurable, transforma a bigint solo si es decimal; deduce `empresa_id` de `NEW/OLD` mediante JSONB y hace `INSERT INTO public.bitacora` con descripción del evento. No verifica en su cuerpo que el usuario del contexto exista/sea miembro del tenant; las FK separadas no imponen esa relación conjunta.
- Ambas funciones muestran **`EXECUTE PUBLIC=t`**. El permiso EXECUTE no anula RLS ni concede acceso de tabla por sí mismo, pero forma parte de la superficie de permisos a revisar. No se ejecutaron ni modificaron estas funciones en 5L.

### 6.3 Implicaciones concretas para futuras pruebas, aún NO implementadas

1. **RLS y permisos de auditoría:** con RLS habilitado sin políticas, un rol SQL limitado no tendrá filas visibles ni podrá insertar bitácora salvo diseño expreso de policies `WITH CHECK` más grants adecuados. Los triggers `SECURITY INVOKER` obligan a comprobar las escrituras transitivas y su rollback dentro de la operación principal. **No abrir bitácora con una política permisiva a todos** para hacer pasar las reservas.
2. **Identidad del actor:** la API debe validar autenticación y membresía `usuario_empresa` y vincular la operación a un contexto server-side de confianza. No aceptar `app.usuario_id`/`app.empresa_id` definidos por SQL arbitrario o cuerpo del cliente como identidad. Evaluar política de eventos globales (`empresa_id IS NULL`) separada de eventos empresariales; no suponer que una FK simple resuelve la pertenencia.
3. **IP:** elegir un mecanismo controlado de procedencia de IP si es requisito académico; `app.ip` por sí solo es dato mutable, y el trigger no comprueba su integridad ni captura automáticamente IP real del socket. Probar valores NULL, vacíos, inválidos, IPv4/IPv6 y límites de longitud exclusivamente cuando se autorice un sandbox de ensayo con datos ficticios.
4. **Control de privilegios:** examinar en futura fase grants de tablas/secuencias y EXECUTE de funciones/4 SP legacy con rol SIN `BYPASSRLS`; no usar `postgres` para demostrar seguridad; revocar EXECUTE sobre SP antiguos en sandbox requiere autorización explícita, no tocar jamás la restaurada ni Supabase.
5. **Orden y atomicidad:** la inserción en `bitacora` es parte de la misma transacción de la operación disparadora, y el trigger de carrito añade `UPDATE carrito`. Diseñar pruebas de fallo de auditoría, reversión de stock/Kardex, políticas de datos ajenos y bloqueos concurrentes antes de `CALL` de SP tenant.

**CIERRE 5L:** validada la consulta READ ONLY y dictamen del informe completo; **NO** se certificaron RLS para cuenta API, actor/IP fiables, reservas concurrentes ni SP instalados. El sandbox sigue vacío, sin policies ni roles de ensayo. **Paso 5 EN PROCESO / Bloque 3 4/7.** Mantener `SAAS_AUTH_ENABLED=false`, `main`, Supabase y base restaurada intactos. Esperar `siguiente` para la próxima subfase; no solicitar más comandos ahora.
