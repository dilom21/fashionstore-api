# Cadena propuesta SaaS — PostgreSQL 17 / Supabase

Estado: **preparada para revisión; no ejecutada**. Esta carpeta está deliberadamente fuera de `supabase/migrations`.

## Orden

1. `000_preflight.sql` — valida el baseline restaurado (41 tablas, 5 objetos SaaS ausentes y cantidades históricas conocidas). Sólo lectura y termina con `ROLLBACK`.
2. `010_saas_core_vanter.sql` — crea las cinco tablas SaaS, planes BASICO/PROFESIONAL, Vanter Men, configuración, suscripción y membresías de los usuarios históricos.
3. `020_tenant_expand_backfill.sql` — añade `empresa_id` a las 31 tablas empresariales y asigna todas las filas existentes a Vanter Men sin cambiar IDs.
4. `030_tenant_integrity.sql` — adapta unicidad, añade claves candidatas `(empresa_id,id)`, FKs compuestas, FKs de membresía e índices tenant.
5. `040_routines_views_hardening.sql` — adapta la vista, protege la titularidad y cierra la ejecución de procedimientos legacy. No instala los tres SP tenant: sus contratos requieren identidad SQL individual aún no provisionada.
6. `050_rls_acl_template.sql` — plantilla histórica bloqueada; queda conservada como evidencia y es reemplazada conceptualmente por las propuestas 050a-c.
7. `050a_identidad_jwt_roles_PROPUESTA_NO_EJECUTAR.sql` — roles NOLOGIN, Vault, verificación JWT y bootstrap aislado.
8. `050b_rls_policies_PROPUESTA_NO_EJECUTAR.sql` — policies tenant/actor, FORCE RLS y fronteras API/SP/platform.
9. `050c_acl_procedimientos_PROPUESTA_NO_EJECUTAR.sql` — ACL mínimo, auditoría privilegiada estrecha y contrato de SP.
10. `080_pruebas_seguridad_PROPUESTA_NO_EJECUTAR.sql` y `081_pool_identidad_PROPUESTA_NO_EJECUTAR.py` — pruebas futuras de RLS y reutilización del pool.
11. `060_postflight.sql` — verificaciones de estructura y preservación; sólo lectura y termina con `ROLLBACK`.

Los archivos `000`–`040` y `060` no contienen nombres de base, cantidades de laboratorio ni tenants ficticios. El archivo `050` no es promocionable todavía: una política basada únicamente en GUC sería manipulable por SQL ejecutado con el mismo login compartido.

## Decisiones conservadoras

- Se conservan las PK e IDs globales históricos; se agregan claves candidatas tenant en paralelo.
- `pago.referencia_transaccion` sigue siendo global, como en el esquema recuperado.
- `bitacora.empresa_id` queda nullable para eventos globales futuros; sus 806 filas existentes se asignan a Vanter Men.
- Los 9 procedimientos legacy quedan sin `EXECUTE` para `PUBLIC`, `anon` y `authenticated`; no se eliminan.
- La vista usa `security_invoker=true` y uniones por `empresa_id`.
- No se crea un login ni se guarda contraseña. Tampoco se concede acceso a `service_role`.

## Despliegue

`DESPLIEGUE_SUPABASE.md` es el procedimiento único de aplicación: orden exacto,
prerrequisitos (Vault, logins, entorno), comprobaciones previas y posteriores,
condiciones de parada y recuperación. `001_ventana_previa.sql` es la instantánea
de sólo lectura previa a la ventana.

Las propuestas `050a`–`050c` permanecen protegidas. Las copias ejecutables se
generan con `python generar_despliegue.py` en `despliegue/`, con el sha256 de cada
original y un `MANIFIESTO.sha256`; los archivos de `despliegue/` son artefactos
generados y no se editan a mano.

## Promoción posterior

Tras aprobar y probar esta propuesta en una base nueva, cada unidad aprobada debe crearse mediante `supabase migration new <nombre>` y copiarse al archivo generado por CLI. No copiar esta carpeta directamente ni aplicar archivos parciales.

