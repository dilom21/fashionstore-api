# 5BS / Etapa J — Acta de decisión pendiente

**Objeto:** cerrar los dos únicos bloqueos humanos que impiden pasar de “candidato” a “staging real”.
**Fecha de apertura:** 31 de octubre de 2026.
**Responsable de la decisión:** dueño del proyecto (estudiante) con visto bueno del docente si aplica.

---

## Decisión 1 — Consumidores de la Data API (R1)

**Hecho verificado (Etapa J):**

| Consumidor inventariado | Canal | ¿Usa Data API? | Evidencia |
|---|---|---|---|
| Backend FastAPI | TCP directo a PostgreSQL (`postgresql+psycopg`) | **No** | `100_catalogo_consumidores_supabase_5bs.py`; `DATABASE_URL` con host Supabase, usuario `postgres`, base `postgres` |
| Angular (`fashionstore-web`) | `environment.apiUrl` → FastAPI | **No** | 0 coincidencias de `supabase-js`, `createClient`, `rest/v1`, `apikey` en 425 archivos |
| Flutter (`fashionstore_mobile`) | `API_BASE_URL` → FastAPI | **No** | 0 coincidencias; solo comentarios que mencionan Supabase |
| Scripts del repositorio | `psql`/`pg_dump` locales | **No** | laboratorios con guardas de puerto |
| Backend `.env` | — | **No** | 0 variables `SUPABASE_*` |

**Lo que no se puede verificar desde este repositorio:** Edge Functions, integraciones externas, BI, jobs o clientes de terceros que usen la URL del proyecto Supabase con `anon`/`authenticated`/`service_role`.

**Opciones:**

- **A (recomendada):** cerrar la Data API a `anon`/`authenticated`/`service_role` (estado actual de `01100` + `01200`). Riesgo: si existe un cliente externo no inventariado, dejará de funcionar.
- **B:** reabrir excepciones mínimas sobre objetos de compatibilidad concretos, con validación de identidad y tenancy. Requiere nombrar el cliente y la operación exacta.

**Respuesta esperada (histórico de la plantilla):**

```
DECISIÓN R1: <A | B>
Si B: cliente(s) autorizado(s): <...>
Operación(es) autorizada(s): <...>
Responsable / fecha: <...>
```

### Confirmación expresa del propietario — Decisión 1 (R1)

```
DECISIÓN R1: A — CONFIRMADA
Fecha: 31/10/2026
```

**Texto de la confirmación (transcrito sin alterar su alcance):**

> El propietario del proyecto FashionStore confirma expresamente la opción A: mantener cerrado el acceso directo a las tablas empresariales mediante la Data API de Supabase y utilizar FastAPI como vía de acceso a dichos datos.
>
> Esta decisión no autoriza modificaciones en la base de datos de producción, cambios de permisos, despliegues ni ejecución de migraciones.
>
> La autorización para el respaldo de datos reales B2 permanece pendiente y deberá documentarse por separado.

**Registro y atribución:**

- **Decisor:** propietario del proyecto FashionStore (persona). Confirmación expresa emitida el **31/10/2026**.
- **Registro:** transcrito en este documento por OpenCode a petición del decisor. **No es una firma del agente**: el agente no decide, no firma y no sustituye al responsable. Sustituye a la transcripción provisional de la Etapa L, que queda como antecedente de la misma decisión.
- **Alcance confirmado:** acceso a los datos de FashionStore **exclusivamente mediante FastAPI**; la Data API de Supabase permanece cerrada a `anon`, `authenticated` y `service_role` sobre tablas empresariales. Se conservan únicamente los accesos internos imprescindibles ya documentados.
- **Efecto técnico:** el contrato A queda confirmado como dirección. Exige mantener `01100` y `01200` como cierre de privilegios y verificar con PostgREST real en un staging autorizado (`401/403`, nunca `200`).
- **Límites explícitos (declarados por el decisor):** esta decisión **no autoriza** modificar la base de datos de producción, cambiar permisos, desplegar ni ejecutar migraciones. `GO_STAGING` y `GO_PRODUCCION` siguen sujetos a sus propias puertas.
- **B2:** permanece **NO AUTORIZADO**. El bloque `AUTORIZACIÓN B2` de `104_solicitud_autorizacion_datos_5bs.md` continúa sin firmar y ese archivo no se modificó.

---

## Decisión 2 — Autorización de respaldo con datos reales (B2)

**Hecho verificado:** el procedimiento está probado con datos ficticios (`102`, `EVIDENCIA_BACKUP_RESTORE_5BS_*`). No se ha ejecutado ningún `pg_dump` con datos del proyecto Supabase.

**Solicitud:** `104_solicitud_autorizacion_datos_5bs.md`.

**Respuesta esperada:** el bloque `AUTORIZACIÓN B2` del documento `104`, firmado.

---

## Lo que queda bloqueado mientras no haya respuesta

| Hito | Estado | Dependencia |
|---|---|---|
| B1 (compatibilidad del esquema) | **GO (laboratorio)** | — |
| R1 (contrato de permisos) | **DECIDIDA Y CONFIRMADA: A (31/10/2026)** · verificación PostgREST pendiente | Confirmación expresa registrada arriba; falta prueba real en staging |
| B2 (respaldo con datos) | **NO-GO / NO AUTORIZADO** | Decisión 2: bloque `AUTORIZACIÓN B2` de `104` sin firmar |
| Staging | **NO-GO** | B2 + secretos + proyecto separado + prueba PostgREST |
| Producción | **NO-GO** | Staging real + B2 + aprobación de despliegue |

## Siguiente acción mínima

1. **R1:** hecho y confirmado por el propietario el 31/10/2026. Mantener el contrato A y probarlo con PostgREST en staging cuando exista proyecto autorizado. La decisión no autoriza cambios de permisos, migraciones ni despliegues.
2. **B2:** firmar el bloque `AUTORIZACIÓN B2` de `104` cuando el propietario lo decida (hoy no está firmado). Con la Etapa L, el instrumento de cifrado autenticado ya está probado con datos ficticios; eso no autoriza la extracción real.
