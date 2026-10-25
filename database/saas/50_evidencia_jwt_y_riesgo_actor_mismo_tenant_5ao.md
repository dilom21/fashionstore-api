# Evidencia CU16–CU18: JWT, identidades y bloqueo de despliegue (5AO)

**Rama:** `feat/saas-multitenant-parcial2`.
**Estado:** FUNCIONES SQL VALIDADAS EXCLUSIVAMENTE EN LAB, DESPLIEGUE SaaS COMERCIAL NO AUTORIZADO.

## Resultados observados en Windows (24 octubre 2026)

- `5AN` devolvió `CU16 PASS`, `CU17 PASS` y `CU18 PASS`, con roles SQL `fashionstore_5ab_a` y `fashionstore_5ab_b`, membresías ficticias, suscripción activa y políticas RLS **temporales**.
- Después de ejecutar ambos escenarios se verificó `ROLLBACK Y PREFLIGHT OK`: laboratorio PostgreSQL 18 en `127.0.0.1:55432`, 41 policies originales y 0 SP comerciales instalados.
- En una ejecución previa se recibieron `744 passed` para `tests_saas`; posteriormente se agregaron pruebas 5AM/5AN y 5AO, que requieren ejecutar regresión nueva.
- Las cinco empresas de entrega en PostgreSQL 5432 y Supabase nunca participaron en el ensayo 5AN.

## Qué cubren las pruebas HTTP 5AO

`tests_saas/test_cu16_jwt_identidad_http_5ao.py` usa **JWT real firmado** con `emitir_token_empresa`, `leer_token_empresa`, la dependencia real `get_current_tenant_context`, el middleware de ruta y las rutas CU16, CU17 y CU18. Solo reemplaza con dobles de prueba las lecturas de membresía PostgreSQL y la ejecución de CALL. Comprueba:

- Ausencia, alteración, firma incorrecta o expiración de token: 401.
- Membresía revocada o empresa que no corresponde al token: 403.
- `usuario_id`, `empresa_id` y rol no pueden imponerse mediante JSON: 422.
- Dos identidades de la **misma empresa** mantienen actores diferentes; crear o cancelar carritos/reservas ajenos se rechaza en un **repositorio simulado**.
- Personal y clientes ejercen roles distintos en CU16–CU18; los flags no se activan permanentemente.
- No se conecta a la base de datos local ni a servidores externos.

## Hallazgo de seguridad pendiente: actor dentro del tenant

Las rutinas comerciales SQL del borrador `12`, `13` y `14` reciben `p_usuario_id`. Su validación consulta `usuario_empresa`, pero **no existe aún una prueba criptográfica o de principal SQL que ligue el `p_usuario_id` declarado a un usuario final específico**. Los roles SQL A y B representan **empresas**, no individuos. Por tanto, un actor que obtenga acceso SQL directo con el rol A podría pasar el identificador de otro miembro habilitado de la misma empresa; la sola validación de RLS empresa y `usuario_empresa` no puede descartar la suplantación.

La protección HTTP sí obtiene el `usuario_id` de un JWT firmado y revalida la membresía; nunca acepta ese ID por JSON. **Eso no autoriza exponer conexiones SQL ni implica que RLS pruebe por sí solo la identidad individual.** Asimismo, la futura conexión API debe adoptar un principal restringido verificable y ejecutar tests reales contra el SP instalado, además de evitar credenciales SQL públicas.

**Antes de habilitar comercio SaaS**: resolver el vínculo de identidad a nivel de principal/servicio de confianza, validar login/bootstrap restringido, instalar ACL+RLS+SP en ambiente de prueba no productivo, demostrar operaciones JWT→HTTP→PostgreSQL con rol no BYPASSRLS, probar dos usuarios dentro de un tenant y repetir los tests de concurrencia/stock. Hasta entonces mantener `SAAS_AUTH_ENABLED=false` y `SAAS_RESERVAS_ENABLED=false`.

**Regla:** no habilitar flags basándose únicamente en tests mock o resultados SQL aislados. No modificar Supabase ni la base 5432.
