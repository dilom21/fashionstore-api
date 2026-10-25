# FashionStore — Primera presentación CU01–CU05

Fecha de corte: 25 de octubre de 2026.
Rama: feat/saas-multitenant-parcial2.
Modo SaaS de la aplicación: APAGADO (SAAS_AUTH_ENABLED=false,
SAAS_RESERVAS_ENABLED=false). Nunca modificar bases PostgreSQL
127.0.0.1:5432, 127.0.0.1:55432 ni Supabase con estas comprobaciones.

## Estado verificable frente al enunciado

| CU | Actor y canal del enunciado | Avance real y limitaciones |
|---|---|---|
| CU01. Registrar y administrar empresas contratantes | Superadministrador SaaS, Web | Modelo y migración para empresa existentes. Falta API administrativa global segura, su autenticación y el alta completa. |
| CU02. Gestionar planes y suscripciones SaaS | Superadministrador SaaS, Web | Planes, suscripciones, vigencia, capacidades y límites implementados en consultas; cambio de plan de prototipo desde admin de empresa. Falta gestión global del superadmin y facturación/ciclo de vida. |
| CU03. Gestionar acceso al sistema por empresa | Usuarios del sistema, Ambas | /auth/tenant/login y /me con JWT y membresía vigente; laboratorio 5BE validó JWT+SQL+3 pools SCRAM. Falta conectar el principal SQL restringido con FastAPI empresarial completo. |
| CU04. Gestionar usuarios, empleados, roles y permisos por empresa | Administrador de empresa, Web | NUEVO: listar membresías, roles locales/base, cambiar rol asignado y revocar/reactivar membresía sin tocar usuario/rol global. Faltan CRUD de empleado y matriz de permisos específica de empresa. |
| CU05. Gestionar sucursales por empresa | Administrador de empresa, Web | Listar/crear/cambiar estado; comprobación de ciudad y límites del plan por empresa; pendiente demostración end-to-end en DB ficticia aislada. |

## Nuevo avance CU04

Endpoints exclusivamente para administrador empresarial autenticado en contexto personal:
- GET /tenant/empresa/usuarios
- GET /tenant/empresa/roles
- PATCH /tenant/empresa/usuarios/{usuario_id}/rol con {"rol_id": 2}
- PATCH /tenant/empresa/usuarios/{usuario_id}/estado con {"estado": false}

La identidad empresa_id procede del JWT y membresía revalidada: NO existe
campo empresa_id aceptado por el cuerpo de solicitud. Los cambios afectan
solamente usuario_empresa.rol_id y usuario_empresa.estado; NUNCA
usuario.rol_id ni rol_funcion globales. No exponer roles personalizados
exclusivos de otra empresa. Denegar el rol CLIENTE para empleados.

Las escrituras bloquean primero la fila empresa y luego la membresía
para serializar cambios. El administrador no puede retirarse su propio
acceso ni dejar a la empresa sin otro administrador activo. El middleware
permite solo las cuatro rutas nuevas por ruta/método exactos; /usuarios y
/roles globales permanecen bloqueadas en modo SaaS.

Advertencia: esto NO instala un LOGIN PostgreSQL por usuario ni RLS
productiva por sí mismo. Sigue prohibido activar los flags SaaS.

## Comprobación local y segura sin PostgreSQL

En PowerShell:

    cd C:\SI2_Parcial1\fashionstore-api
    conda activate fashionstore-api
    git pull --ff-only origin feat/saas-multitenant-parcial2
    python -m pytest -q tests_saas/test_cu04_membresias_empresa_sqlite_offline.py

13 pruebas previstas (aún no ejecutadas por el estudiante), sobre SQLite
completamente ficticia en memoria. No cargar .env ni conectar a PostgreSQL.

## Prioridades para completar la primera presentación

1. CU01: mecanismo de autenticación de SUPERADMINISTRADOR SaaS distinto
   de admin empresa, API segura de alta/desactivación, slug único,
   primera suscripción y auditoría.
2. CU02: CRUD administrativo de planes/suscripciones con vigencia,
   límite de sucursales y transacciones. Sin afirmar cobro automático.
3. CU04: alta/edición de empleados por empresa y matriz de permisos
   específica de tenant; no reusar los servicios globales heredados.
4. CU03 y CU05: integración API y RLS con principal SQL autenticado en
   un laboratorio nuevo y ficticio.
5. Documentación ICONIX y capturas verdaderas únicamente de funciones
   demostradas. No presentar simulaciones como funcionalidad productiva.

## Evidencia acumulada

Resultados confirmados por el estudiante: 5BC=17, 5BD=12, 5BE=15,
5BF=14, 5BG=22, 5BH=12, 5BI=10 controles: TOTAL 102. Las pruebas
de conexiones SCRAM, JWT/membresía, aislamiento SQL, concurrencia y
CU16–CU18 se realizaron sobre laboratorios DESECHABLES y no demuestran
que los CU01–CU05 funcionen íntegramente en frontend real.
