# Procedimiento posterior (no ejecutado)

## Ensayo aislado

1. Crear una base PostgreSQL 17 **nueva**, con nombre distinto de cualquier base existente. No reutilizar ni borrar `fashionstore_restore_validation_20261106_135445`.
2. Restaurar allí `fashionstore_public_full.custom` con `pg_restore 17.9`; conservar el log y comparar el inventario con `verification.json`.
3. Ejecutar `000_preflight.sql` con `ON_ERROR_STOP=1`. Debe terminar en `PRECHECK_OK` y rollback.
4. Aplicar en orden 010, 020, 030 y 040. Capturar stdout/stderr, duración y bloqueos. No ejecutar 050.
5. Ejecutar 060. Comparar además los conteos de **todas** las 41 tablas contra el inventario FASE 2, no sólo los once controles críticos incluidos en SQL.
6. Ensayar duplicados permitidos entre dos tenants ficticios únicamente en esa base desechable; comprobar que duplicados dentro del mismo tenant fallan y que las 48 FKs tenant impiden cruces.
7. Completar 050 con roles reales de laboratorio equivalentes a Supabase, login individual por usuario, dos owners NOLOGIN y mapa privado. Probar conexiones simultáneas reutilizadas, cambio malicioso de GUC, actor del mismo tenant y tenant ajeno.
8. Instalar copias revisadas de los tres SP tenant desde los borradores 12–14, con verificador en la primera instrucción. Probar concurrencia sobre la última unidad de stock y rollback integral.
9. Ejecutar tests FastAPI con flags aún apagados. El adaptador actual debe validar `session_user`, RLS, owner, ACL y `fs_verificar_actor_reserva` antes de habilitar rutas.

## Aplicación futura a Supabase

- Tomar un backup formal nuevo y verificar hash/inventario inmediatamente antes de la ventana.
- Confirmar PG17, drift cero frente al snapshot y cantidades exactas mediante 000.
- Convertir cada archivo aprobado en migración creada por Supabase CLI; no usar los nombres de esta carpeta como historial oficial.
- Ejecutar con conexión Direct, una sola sesión administrativa, `ON_ERROR_STOP`, ventana de mantenimiento y monitoreo de locks.
- No activar flags ni conceder el login de aplicación hasta que 050 y las pruebas de identidad individual estén aprobadas.
- Recuperación: 010–040 son transaccionales individualmente. Ante error, PostgreSQL revierte el archivo activo; no continuar con el siguiente. Si un archivo ya confirmó y se decide volver atrás, restaurar el backup en un proyecto/base separado o usar una reversión expresamente revisada. No improvisar `DROP` sobre producción.

## Bloqueos para declarar LISTO

- Falta definir y provisionar de forma segura los LOGIN PostgreSQL individuales que el código FastAPI exige realmente.
- Falta decidir cómo el pool obtiene esas credenciales sin almacenarlas ni reutilizar una identidad global.
- Faltan los nombres reales aprobados de owner verificador/owner SP y la tabla privada de mapeo.
- La vigencia de 365 días y la razón social `Vanter Men` proceden del SQL académico original; negocio debe ratificarlas antes de promoción.
- Deben revisarse los 15 cuerpos de funciones/27 triggers con el esquema ya migrado. Las FKs compuestas contienen cruces, pero esta propuesta sólo añade inmutabilidad y adapta la vista; no reescribe silenciosamente lógica no certificada.
- Debe auditarse el rol real de conexión de Supabase y `service_role`; permisos locales no son equivalentes.
