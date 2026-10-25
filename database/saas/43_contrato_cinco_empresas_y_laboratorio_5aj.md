# FashionStore SaaS — Contrato definitivo de cinco empresas y aislamiento del laboratorio

**Rama:** `feat/saas-multitenant-parcial2`. **No ejecutar como migración.**

## Cinco empresas de entrega final (NO RECREAR)

| Empresa | Slug | Plan | Sucursales activas |
|---|---|---|---:|
| Vanter Men | `vanter-men` | `PROFESIONAL` | 4 |
| UrbanWear | `urbanwear` | `BASICO` | 1 |
| Elegancia Sur | `elegancia-sur` | `PROFESIONAL` | 3 |
| Denim House | `denim-house` | `BASICO` | 2 |
| SportLine | `sportline` | `PROFESIONAL` | 3 |
| **TOTAL** | **5 tenants** | | **13** |

Estas cinco empresas ya constan creadas en **127.0.0.1:5432 / fashionstore_restore_20261023_193554_594**, verificadas en el Bloque 2. Nunca ejecutar nuevamente la semilla `05_crear_cuatro_tenants_demo_local.ps1`, ni insertar duplicados, cambiar passwords o reescribir ventas históricas. Vanter Men mantiene sus datos y usuarios del primer parcial. Las otras cuatro son datos demo de cuatro admins iniciales inactivos, con credenciales no disponibles, y sus suscripciones respectivas.

## Dos empresas A/B de ensayo (NO SON LA ENTREGA FINAL)

Solo en el clúster físicamente separado **127.0.0.1:55432**, base `fashionstore_rls_lab_5u`, PGDATA `C:\SI2_Parcial1\fashionstore-rls-lab-5v-data`, PostgreSQL 18:

- `empresa-lab-a`, login SQL `fashionstore_5ab_a`.
- `empresa-lab-b`, login SQL `fashionstore_5ab_b`.

Estos dos tenants ficticios son **adicionales y aislados**. No sustituyen ni pertenecen a las cinco empresas de entrega. Solo este laboratorio está autorizado para roles/DDL/DML/ensayos de concurrencia ya acordados. Nunca tocar Supabase, la base histórica del 5432, `main` o `SAAS_AUTH_ENABLED=false` durante las pruebas.

## Resultado acumulado y alcance

- **645 passed** observados en Windows; 2 deprecaciones conocidas, 0 fallos.
- Matriz A01–A14: **11 PASS** reales: A01–A07 excepto las salvedades ya resueltas A05/A06, A09/A10/A12/A14. **A08, A11 y A13 aún pendientes** en lógica de reservas.
- A05/A06 validaron políticas WITH CHECK y USING en el laboratorio, revertidas sus concesiones tras ensayo; A10 denegó 8 CALL en las dos cuentas SQL.
- Los 3 borradores `sp_*_tenant` de CU16–CU18 **NO están instalados**; la mayoría de POST comerciales y flujos pagos/ventas/devoluciones SaaS siguen bloqueados. No habilitar SaaS ni considerar la aplicación lista para despliegue.
- Las pruebas A/B SQL deben mantenerse separadas de la aceptación HTTP y de la demostración de las **cinco** empresas finales.

## Regla de siguiente fase

Probar A08/A11/A13 en el laboratorio y distinguir entre **ensayo SQL de concurrencia** y **SP definitivo con membresía/JWT/plan e integración FastAPI**. No atribuir a funciones auxiliares de laboratorio cobertura de autorización de usuarios reales. Preparar después la integración segura de CU16–CU18 y el resto de operaciones comerciales.
