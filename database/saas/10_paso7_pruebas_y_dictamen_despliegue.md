# Segundo parcial — Bloque 2 — Paso 7: pruebas de aislamiento y dictamen

**Estado:** BLOQUE 2 COMPLETADO (7/7 pasos), con **pruebas estructurales y unitarias locales superadas**, de acuerdo con la salida de PowerShell proporcionada por el estudiante el 23/10/2026. **NO APTO PARA DESPLIEGUE MULTITENANT**: siguen pendientes controles FastAPI y RLS. Se mantiene `SAAS_AUTH_ENABLED=false`. NO se desplegó ni modificó Supabase.

## Requisito rector
Examen Parcial-2 Sistemas 2 S2-2026, apartados **9.1–9.6** y **RNF01/RNF02**:
- Una sola PostgreSQL compartida; cada empresa es un tenant (no cada sucursal).
- Un usuario de Empresa A no puede consultar/modificar/eliminar datos de Empresa B, y el **backend** debe verificar empresa y pertenencia.
- Plan BASICO: hasta 2 sucursales y sin reportes IA; PROFESIONAL: hasta 10 e IA.
- Suscripción y estado vigentes deben condicionar operaciones.
- El enunciado no declara suficiente asegurar solo las FK; también exige protección del acceso mediante backend.

## Herramientas para la prueba local
- `database/saas/06_pruebas_integridad_multitenant_local.sql`: exige nombre de base + localhost, revisa 5 tenants, 31 tablas empresariales, 48 FK compuestas validadas, 28 UNIQUE, 27 triggers, suscripciones y conteos. Ejecuta tres intentos de inserción cruzada, exige SQLSTATE 23503 y el nombre de la FK correspondiente. Termina literalmente con `ROLLBACK`; **no contiene COMMIT**.
- `database/saas/06_ejecutar_pruebas_b2_p7_local.ps1`: fija host/puerto/nombre de base; solicita contraseña PostgreSQL local y confirmación `PROBAR`; compara conteos antes/después; lanza `python -m pytest -q tests_saas` sobre SQLite aislado.
- Destino: solo `127.0.0.1:5432 / fashionstore_restore_20261023_193554_594`.
- No ejecutar contra Supabase; no usar `supabase db push`.
- **Restricción**: los ensayos SQL usan usuario postgres (administrador) y NO demuestran la seguridad de SELECT de un usuario no privilegiado. Los intentos de INSERT cruzados sí demuestran las restricciones FK cuando terminen satisfactoriamente.
- Pueden consumirse valores de SEQUENCE aunque los INSERT de prueba fallen; eso solo produce saltos en futuros IDs. Los registros comerciales originales y sus IDs siguen intactos.

## Matriz de pruebas y resultados observados

| Prueba | Resultado esperado | Estado |
|---|---|---|
| Cinco empresas y límites de plan **en los datos** | Vanter 4/10, UrbanWear 1/2, Elegancia 3/10, Denim 2/2, SportLine 3/10 | Verificado en Paso 6 |
| 48 FK compuestas y 28 UNIQUE válidas | Conteos 48/28 | **SUPERADO**: 48 FK y 28 UNIQUE, validadas de nuevo |
| Producto Vanter enlazado a categoría UrbanWear | FK `fk_tenant_fk_producto_categoria` lo rechaza | **SUPERADO**: OK 3/6 |
| Inventario Vanter enlazado a variante UrbanWear | FK `fk_tenant_fk_inventario_variante` lo rechaza | **SUPERADO**: OK 4/6 |
| Cliente Vanter vinculado a usuario UrbanWear sin membresía Vanter | FK `fk_tenant_cliente_usuario` lo rechaza | **SUPERADO**: OK 5/6 |
| Transacción de pruebas sin cambio de datos | `ROLLBACK`, 5 empresas, 40 ventas, 30 pagos, 844 bitácora | **SUPERADO**: ROLLBACK y valores idénticos ANTES/DESPUÉS |
| Tests de JWT/endpoint tenant (unitarios aislados) | `python -m pytest -q tests_saas` completo sin fallos | **SUPERADO**: 18 passed, 1 StarletteDeprecationWarning, 0.63 s |
| Autorización real HTTP de Empresa A contra recurso de B | 403/404 sin filtrar datos B | **NO PROBABLE TODAVÍA**: rutas heredadas no integradas |
| RLS con rol PostgreSQL usado realmente por la API | Prohibir SELECT cruzado y bypassRLS | **PENDIENTE**: conexión postgres superusuario no lo demuestra |
| Exceso de sucursales BASICO por endpoint API | Rechazo por backend, sin crear rama 3 | **PENDIENTE de implementar** |
| IA generativa desde BASICO | Rechazo por backend | **PENDIENTE de implementar** |
| Caducidad/suspensión de suscripción en operación comercial | Rechazo por backend | **PENDIENTE de implementar** |

## Auditoría estática del backend existente (rama SaaS, 23/10/2026)

1. `app/core/tenant_dependencies.py`: contiene `get_current_tenant_context` y valida JWT/afiliación/rol/suscripción. Pone `app.empresa_id` en transacción, **sin sustituir los filtros SQL**.
2. `app/modules/autenticacion_seguridad/api/tenant_router.py` aporta POST `/auth/tenant/login` y GET `/auth/tenant/me`, ambos protegidos con `SAAS_AUTH_ENABLED` que queda false por defecto.
3. `app/modules/autenticacion_seguridad/repositories/tenant_repository.py` sí valida membresía activa, empresa, rol, suscripción y perfiles `cliente`/`empleado`; es solo la capa de autenticación.
4. Inspección directa de los siguientes ocho routers legados: `catalogo`, `inventario`, `ventas`, `reservas`, `reportes`, `sucursales`, `compras`, `pagos`. Ninguno utiliza `get_current_tenant_context`, `require_tenant_permission` ni `get_current_tenant_admin`; sus archivos tampoco muestran `empresa_id`.
5. En `app/modules/catalogo/repositories/repository.py`, `ProductoRepository.listar` y `obtener_por_id` consultan sin predicado `empresa_id`; `CategoriaRepository.crear` construye un registro sin `empresa_id`. Con el nuevo DDL de `empresa_id NOT NULL` eso compromete la escritura comercial.
6. Con las condiciones anteriores el requisito RNF02 **NO está cumplido de extremo a extremo**. Tampoco se puede demostrar ejecución segura de la lógica de plan desde el backend; datos correctos de demo no son prueba de enforcement del plan.
7. Los administradores ficticios de las cuatro empresas siguen `usuario.estado=false` y `usuario_empresa.estado=false`. La prueba HTTP inter-tenant no debe intentarse como si esos usuarios ya pudieran iniciar sesión.

## Dictamen de despliegue
**NO APTO PARA PROMOVER AL SUPABASE REMOTO NI ACTIVAR SaaS AUTH**. El Bloque 2 solo deja una copia local con datos y relaciones multitenant consistentes, con pruebas estructurales y unitarias del Paso 7 superadas. Falta otro bloque de implementación del backend, RLS y pruebas de acceso HTTP antes de exponer tenants independientes.

Próximas actividades del proyecto (fuera del Bloque 2):
- Adaptar modelos y repositorios a `empresa_id` obligatorio; forzar contexto de tenant en todas las rutas.
- Aplicar permisos basados en `usuario_empresa` de la empresa, no `usuario.rol_id` global.
- Aplicar reglas de estado/vigencia de suscripción y capacidades de plan en servicios FastAPI.
- Diseñar la estrategia de seguridad RLS/DB roles, probar con **rol sin BYPASSRLS**, y auditar view/reportes/funciones SQL.
- Provisionar cuentas admin de demo mediante procedimiento seguro sin claves conocidas en código; pruebas 403/404 por recurso.
- Diseñar migraciones finales no dependientes del nombre de la BD local, sincronizar historial de migraciones remoto y planificar rollback antes de tocar Supabase.
- Continuar módulos obligatorios del examen: offline/sincronización, reportes IA/ML, ICONIX y todas las verificaciones del PDF.

## Evidencia de ejecución local recibida (23/10/2026)
- Rama: `feat/saas-multitenant-parcial2`, PowerShell en `C:\\SI2_Parcial1\\fashionstore-api`.
- Base de datos local verificada: `fashionstore_restore_20261023_193554_594`, servidor `127.0.0.1:5432`, 46 tablas, cinco empresas, cinco suscripciones, 13 usuarios, 13 membresías, 13 sucursales, 9 clientes, 8 empleados, 18 productos, 124 variantes, 274 inventarios, 40 ventas, 30 pagos, 11 reservas y 844 registros de bitácora.
- 48 FK compuestas y 28 UNIQUE, 27 triggers.
- Pruebas SQL: `OK 1/6` a `OK 6/6`. Los tres intentos empresariales cruzados fueron rechazados por las restricciones esperadas. La salida mostró `ROLLBACK`, y el resultado `ANTES` fue idéntico a `DESPUES`.
- Pruebas de autenticación unitarias: `18 passed, 1 warning in 0.63s`; advertencia `StarletteDeprecationWarning` de `httpx` / `starlette.testclient` que no implicó fallo.
- La conexión de prueba devuelve `rol_actual_bypassrls: true`. **Esta batería no prueba aislamiento SELECT ni RLS real**, ni autorización de operaciones comerciales a través de FastAPI.
- El script imprimió `PASO 7: PRUEBAS ESTRUCTURALES Y UNITARIAS LOCALES SUPERADAS` y `NO desplegar ni habilitar SAAS_AUTH_ENABLED`.

**Conclusión:** Bloque 2 **7/7 completado como fase local de esquema, seed y pruebas estructurales**. El sistema SaaS multitenant global sigue **incompleto** para RNF02 y **no apto para desplegar**. La evidencia descrita se basa en la salida de consola proporcionada por el estudiante; el asistente no ejecutó comandos en la PC del estudiante.
