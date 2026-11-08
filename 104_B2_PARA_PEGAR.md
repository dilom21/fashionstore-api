# 5BS / Etapa J — Solicitud de autorización para respaldo con datos reales (B2)

**Estado (31/10/2026, actualizado tras Etapa 5BW):** CONSENTIMIENTO EXPRESO DEL PROPIETARIO **RECIBIDO Y REGISTRADO** (§6), alcance B2 acotado a `public`. **GATE DOCUMENTAL INCOMPLETO**: faltan la ratificación de la ventana exacta, el rol/usuario de extracción, la identidad del custodio y la firma/fecha del propietario. **B2 NO SE EJECUTA** hasta que el propietario complete el bloque §6.

**Referencia:** `112_runbook_cifrado_autenticado_b2_5bu.md` (procedimiento vigente AEAD v2), `110_cifrado_autenticado_b2_5bu.py` (gate PRE-B2), `103_runbook_backup_restore_retorno_5bs.md` (procedimiento histórico v1, CBC prohibido para datos reales) y `REPORTE_SAAS_5BU_ETAPA_L_20261031_182300.md` (estado previo).

> **Nota de conservación de historial:** las secciones §1–§5 se conservan ***sin alteración*** como registro de la solicitud original de la Etapa J (5BS). El ***alcance vigente y autorizado*** es el de §6; donde §1 difiera de §6, prevalece §6. La plantilla original de respuesta se conserva íntegra en §8 (histórica).

---

## 1. Qué se solicita (alcance exacto)

> **Vigencia:** corresponde a la solicitud ***original de 5BS***. Sustituida en alcance por §6 (Etapa M / 5BV): solo `public`, sin `pg_dumpall --roles-only`, TTL 24 h y cifrado AEAD v2 obligatorio.

Autorización para ejecutar **una sola vez**, en una ventana acordada, un respaldo con **datos** del proyecto Supabase `fashionstore` y una restauración de verificación en un ambiente separado.

| Elemento | Valor solicitado |
|---|---|
| Origen | Proyecto Supabase `fashionstore`, base `postgres`, esquema `public` (más `saas`, `saas_admin`, `saas_migracion` si existen). |
| Operación | `pg_dumpall --roles-only` + `pg_dump --format=custom --compress=6` **con datos**. |
| Rol usado | Rol de **solo lectura** con `pg_read_all_data` o `SELECT`; **nunca** `service_role` ni `postgres` si existe alternativa. |
| Motor | `pg_dump` 17.x (servidor 17.6). |
| Volumen estimado | Se medirá con `pg_database_size` antes de autorizar; el requisito es 3× ese tamaño libre en disco. |
| Destino del archivo | Disco local del operador, `%LOCALAPPDATA%\Temp\opencode\fs_b2_<sello>`, **fuera de Git**, cifrado AES-256. |
| Restauración de verificación | Cluster PostgreSQL 17 **nuevo y separado** (loopback o red privada), nunca el origen. |
| Duración máxima del archivo | **72 horas** (máximo 7 días con justificación escrita). |
| Custodia | Una sola persona designada; contraseña del cifrado en archivo aparte. |
| Borrado | Manual, registrado con fecha/hora/responsable, incluido cualquier medio adicional. |

## 2. Qué NO se solicita y no se hará

- **No** se modifica el proyecto Supabase: cero DDL/DML, cero `supabase db push`, cero cambios de flags.

- **No** se copian datos a Git, GitHub, issues, CI, chats de IA ni servicios externos.

- **No** se usa el respaldo para entrenar modelos ni para análisis de terceros.

- **No** se restaura sobre la base de producción.

- **No** se anonimiza el único origen (se prohíbe alterar el original).

- **No** se extraen datos de pago/tarjeta: si existen, el respaldo debe excluirlos o el procedimiento se detiene.

## 3. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Exposición de PII durante la copia | Cifrado AES-256 en reposo, ACL restringida, TTL, borrado registrado. |
| Copia en un equipo compartido | Destino fuera de OneDrive; verificación de ACL antes de ejecutar. |
| Impacto en producción por el dump | `pg_dump` toma un snapshot consistente sin bloquear escrituras; se ejecuta en ventana de baja carga. |
| Restauración accidental sobre el origen | Preflight de host/puerto/base y marcador `DISECHABLE`; el restore exige base nueva creada con `TEMPLATE template0`. |
| Hash de contraseñas en `roles.sql` | Se trata como material sensible: mismo cifrado y mismo TTL que el respaldo. **En el alcance vigente (§6) este artefacto no se genera.** |

## 4. Lo que se obtiene al aprobar

1. Evidencia real de que el respaldo se puede **restaurar** (hoy solo está probado con datos ficticios).

2. Medición real de invariantes (filas, secuencias, ACL, RLS) antes de cualquier migración.

3. Base para el **punto de retorno** de la activación: sin respaldo restaurado no existe rollback creíble.

4. Cierre de **B2**, hoy `NO-GO`.

## 5. Lo que sigue bloqueado si no se aprueba

- Staging puede avanzar **solo con datos sintéticos**, y así debe declararse.

- Producción permanece **NO-GO**.

- No se puede afirmar que el sistema tolera el volumen ni los permisos reales.

---

## 6. Registro de respuesta del propietario — Autorización B2 vigente

> Este es el **bloque vigente** que deberá revisar el gate PRE-B2 de `110_cifrado_autenticado_b2_5bu.py`.
>
> **IMPORTANTE:** sustituye **tú mismo** los campos entre `<...>` antes de ejecutarlo. El texto propuesto sobre borrado debe ser aceptado expresamente por ti. Ni ChatGPT ni OpenCode pueden atribuirse tu firma, inventar tu nombre, ratificar un rol privilegiado o establecer una ventana a tu nombre.

```text
AUTORIZACIÓN B2 — FashionStore SaaS — Registro Etapa M (5BV)

Autorizante: propietario del proyecto FashionStore SaaS
Forma de la declaración: mensaje escrito del propietario en la conversación de coordinación del 2026-10-31
Texto literal de la autorización: «Autorizo B2»
Fecha declarada: 2026-10-31 (hora de esta declaración no registrada)

Autorizo / No autorizo: SÍ — «Autorizo B2» (declaración escrita del propietario, 2026-10-31)
Alcance autorizado: UNA única copia de los datos reales del esquema public del proyecto Supabase FashionStore (ref zyitwlxfkphthlvlmuej, PostgreSQL 17.6), exclusivamente de SOLO LECTURA, sin DDL/DML/GRANT/ALTER ROLE ni modificaciones de infraestructura; cifrado local AES-256-GCM FSB2AEAD v2; restauración EXCLUSIVAMENTE en PostgreSQL 17 local aislado y desechable; sin staging cloud ni producción.

Rol de lectura autorizado: <ESCRIBE EL ROL REAL. SI ELIGES postgres, DEBES ESCRIBIR PERSONALMENTE QUE ACEPTAS EXCEPCIONALMENTE EL RIESGO DE USAR UNA CUENTA PRIVILEGIADA SOLO PARA ESTA EXPORTACION DE LECTURA>
Ventana: <AAAA-MM-DD HH:MM FUTURO> a <AAAA-MM-DD HH:MM FUTURO>, zona America/La_Paz (UTC-04:00)
Custodio del respaldo: <TU NOMBRE COMPLETO>, propietario del proyecto
TTL del respaldo: 24 horas como máximo desde el fin de la validación; después debe eliminarse el paquete cifrado (antes si el propietario lo ordena).
Medios de borrado adicionales: <CONFIRMA O EDITA: eliminar temporales y archivos planos, destruir la base y el cluster local de restauracion, revisar residuos en directorio de trabajo y TEMP, registrar fecha/hora/responsable y eliminar el paquete cifrado dentro de 24 horas; sin afirmar borrado forense del SSD>
Firma / fecha: <TU NOMBRE COMPLETO> — <FECHA REAL AAAA-MM-DD HH:MM America/La_Paz (UTC-04:00); escrita personalmente por ti>
```

**Control del almacenamiento — comprobado por el propietario (31/10/2026):** volumen `C:` protegido con BitLocker, cifrado del 100 % del espacio usado, método XTS-AES 128 y protección activada. Este control de cifrado en reposo **no sustituye** AES-256-GCM ni el control de acceso a los archivos mientras Windows está desbloqueado. OpenCode debe verificar la ruta real de los archivos temporales antes de ejecutar.

**Estado documental:** B2 cuenta con consentimiento general, pero **NO DEBE EJECUTARSE** hasta que el propietario sustituya personalmente los campos pendientes, ratifique la ventana y el rol, y el gate responda `ok=true`, junto con la verificación manual del alcance. La autorización no permite cambios en Supabase, Render, Vercel ni activar SaaS.

## 7. Campos que el propietario debe completar para habilitar B2

El agente **no** puede rellenar ninguno de estos campos. Se presentan con el motivo por el que bloquean.

| # | Campo | Por qué bloquea | Valor propuesto por el agente (sujeto a ratificación) |
|---|---|---|---|
| 1 | **Ventana exacta** (inicio y fin, zona America/La_Paz UTC−04:00) | `pg_dump` con datos reales solo puede ejecutarse dentro de una ventana aprobada. La conversación no fijó hora. | **Propuesta, NO autorizada:** 2026-10-31 16:00–18:00 (UTC−04:00), 2 h, supervisada. Si la fecha/hora caduca, se pide una nueva ventana. |
| 2 | **Rol / usuario de extracción** | Define el privilegio real sobre el origen. Si es `postgres`, es cuenta privilegiada y debe aceptarse el riesgo por escrito. | Rol existente de solo lectura si existe. No se crean roles ni se hace `GRANT` en Supabase. |
| 3 | **Identidad del custodio** | La custodia personal ya está autorizada como rol; falta el nombre que asume la responsabilidad del paquete cifrado y de la frase. | Propietario del proyecto (rol ya autorizado); falta nombre. |
| 4 | **Firma / fecha** | Es la ratificación documental del instrumento. Sin ella el gate PRE-B2 no abre. | Ninguna. El agente no firma, no fecha y no simula rúbrica. |
| 5 | **Medios de borrado adicionales** | Cierra el ciclo de destrucción del artefacto cifrado y de residuos. | Revisión de residuos en el destino y en `%TEMP%`, registro con fecha/hora/responsable. |

### Datos operativos ya decididos por el alcance autorizado (no requieren nueva decisión)

| Elemento | Valor |
|---|---|
| Origen | Solo esquema `public`, solo lectura, servidor PostgreSQL 17.6 |
| Esquemas excluidos | `auth`, `storage`, `vault`, `extensions`, `saas*`, y cualquier base externa |
| Formato de respaldo | `pg_dump -Fc -Z 6`, esquema + datos, **solo** `--schema=public` |
| Cifrado | `FSB2AEAD` v2 — AES-256-GCM, PBKDF2-HMAC-SHA256 600 000, salt 16 B, nonce 12 B, tag 16 B, streaming 1 MiB |
| Formatos prohibidos | v1 / AES-256-CBC, `.pass`, cualquier fallback silencioso |
| Restauración | PostgreSQL 17 local aislado, `127.0.0.1`, puerto no protegido, clúster nuevo y desechable |
| TTL | 24 h máximo desde el fin de la validación |
| Destino | Directorio privado fuera del repositorio y fuera de OneDrive/sincronización; NTFS con ACL restringida |

### Firma del propietario (a completar por él, en su equipo)

```

Completar en este mismo archivo el bloque §6:

  Rol de lectura autorizado: <rol real>

  Ventana: <AAAA-MM-DD HH:MM> a <AAAA-MM-DD HH:MM>, zona America/La_Paz (UTC-04:00)

  Custodio del respaldo: <nombre>

  Medios de borrado adicionales: <lista o "ninguno">

  Firma / fecha: <nombre y fecha — puño y letra o firma digital del propietario>

```

Sin esos cinco campos completados **por el propietario**, el procedimiento B2 permanece detenido y no se abre ninguna conexión al origen.

## 8. Plantilla original de respuesta (histórica, 5BS — no usar para el gate)

```

AUTORIZACIÓN B2 — FashionStore SaaS

Autorizo / No autorizo: <texto>

Alcance autorizado: <datos + estructura | solo estructura>

Rol de lectura autorizado: <nombre del rol>

Ventana: <inicio> a <fin>, zona horaria <...>

Custodio del respaldo: <nombre>

TTL del respaldo: <horas>

Medios de borrado adicionales: <lista o "ninguno">

Firma / fecha: <...>

```

Se conserva como registro de la Etapa J. La respuesta válida es la de §6.
