# 5BS / Etapa J — Runbook definitivo de respaldo, restauración y retorno

**Estado:** PROCEDIMIENTO DEFINITIVO. **B2 sigue `PENDIENTE DE AUTORIZACIÓN`.**
**Probado con:** datos **ficticios** del laboratorio 5BR (`102_ensayo_backup_restore_ficticio_5bs.py`).
**No probado:** datos reales del proyecto Supabase (requiere la autorización de `104_solicitud_autorizacion_datos_5bs.md`).

---

## 0. Qué acredita cada cosa

| Prueba | Acredita | NO acredita |
|---|---|---|
| `102 --ensayo` con datos ficticios | Que la herramienta (`pg_dump 17`, `pg_restore`, cifrado AES-256, SHA-256, roles, invariantes) funciona de extremo a extremo. | Que el volumen, el tiempo y los permisos reales de Supabase se comporten igual. |
| `pg_dump --schema-only` (Etapa H) | Estructura del esquema original. | Filas, secuencias vivas, bloat, ni permisos efectivos del proyecto real. |
| Staging real (pendiente) | Que la aplicación funciona contra una copia restaurada. | Que producción esté lista sin ventana ni aprobación. |

**Regla dura:** ningún `pg_dump` con datos reales se ejecuta sin autorización humana explícita, registrada y con ventana acordada.

---

## 1. Preflight obligatorio (antes de tocar nada)

Ejecutar y **archivar la salida** (sin DSN, sin contraseñas):

```powershell
# 1.1 Identidad del repositorio y del candidato
git rev-parse --abbrev-ref HEAD          # debe ser feat/saas-multitenant-parcial2
git rev-parse --short HEAD
git status --short                       # cambios locales permitidos; nunca se commitean aquí

# 1.2 Motor y compatibilidad
pg_dump --version                        # exigido 17.x
pg_restore --version
psql --version

# 1.3 Destino del respaldo: fuera de Git, en disco local cifrado
$destino = "$env:LOCALAPPDATA\Temp\opencode\fs_b2_<sello>"
Test-Path $destino                       # no debe existir
```

Checklist que el operador marca antes de continuar:

- [ ] Autorización escrita de `104_solicitud_autorizacion_datos_5bs.md` firmada.
- [ ] Ventana de mantenimiento aprobada (inicio, fin, responsable).
- [ ] Rol de lectura identificado, con privilegio **mínimo** (`pg_read_all_data` o `SELECT` en las tablas del esquema `public`). **Nunca** se usa `service_role` para el respaldo.
- [ ] Espacio libre ≥ **3×** el tamaño estimado de la base (`pg_database_size`).
- [ ] Carpeta destino fuera de OneDrive/Git y con ACL restringida al usuario operador.
- [ ] Motor `pg_dump` **igual o mayor** que el servidor (17.6 → usar 17.7).
- [ ] Se conoce el punto sin retorno (ver §6).

---

## 2. Modo `--schema-only` (permitido hoy)

```powershell
pg_dump --schema-only --no-owner --no-privileges `
  --format=custom --compress=6 `
  --host <host> --port <5432|6543> --username <rol_lectura> `
  --dbname <base> --file "$destino\estructura.dump"
```

- Se usa solo para comparar estructura y planificar.
- **No** sirve para restaurar datos ni para medir invariantes de negocio.
- Su hash se registra en el manifiesto (§4) igual que el de un respaldo con datos.

## 3. Modo con datos (BLOQUEADO hasta autorización)

```powershell
# 3.1 Roles (necesario para restaurar propietarios y ACL)
pg_dumpall --roles-only --host <host> --port <5432> --username <rol_admin> `
  --file "$destino\roles.sql"

# 3.2 Base completa en formato custom comprimido
pg_dump --format=custom --compress=6 --no-password `
  --host <host> --port <5432|6543> --username <rol_lectura> `
  --dbname <base> --file "$destino\base.dump"
```

Notas:
- `--schema-only` y datos son **dos autorizaciones distintas**; nunca se asumen entre sí.
- `--no-owner` **no** se usa cuando se quiere verificar ACL; se usa solo en restauraciones de inspección.
- No se ejecuta `pg_dump` contra un pooler en modo transacción si el rol lo prohíbe; usar puerto directo `5432`.

---

## 4. Cifrado, hash y manifiesto

> **Actualización Etapa L (5BU):** este apartado describe el formato **v1 (AES-256-CBC)**, conservado como historial del ensayo ficticio de la Etapa J. Para cualquier respaldo con **datos reales** rige `112_runbook_cifrado_autenticado_b2_5bu.md` (AES-256-GCM con tag verificado antes de restaurar). No se admite fallback a CBC.

```powershell
# 4.1 Contraseña en archivo, nunca en argumentos ni en el historial
$pass = "$destino\.pass"
# generar 32 bytes aleatorios y escribirlos SOLO en ese archivo
icacls $pass /inheritance:r /grant:r "$env:USERNAME:F"

# 4.2 Cifrado AES-256-CBC con PBKDF2
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt `
  -in  "$destino\base.dump" `
  -out "$destino\base.dump.enc" `
  -pass file:$pass

# 4.3 Verificación de ida y vuelta (obligatoria)
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 `
  -in  "$destino\base.dump.enc" `
  -out "$destino\verificacion.dump" `
  -pass file:$pass
Get-FileHash "$destino\base.dump"            -Algorithm SHA256
Get-FileHash "$destino\verificacion.dump"    -Algorithm SHA256   # deben coincidir
Remove-Item "$destino\verificacion.dump"
Remove-Item "$destino\base.dump"             # el plano NO se conserva
```

Manifiesto obligatorio (`manifiesto_b2.json`, sin secretos):

```json
{
  "fecha_utc": "...",
  "origen": {"host_clase": "supabase", "puerto_clase": "5432|6543", "base": "postgres"},
  "motor": "pg_dump 17.7",
  "alcance": "datos+estructura",
  "bytes_plano": 0,
  "bytes_cifrado": 0,
  "sha256_cifrado": "<hex>",
  "roles_incluidos": 0,
  "ttl_horas": 72,
  "custodio": "<rol humano>",
  "autorizacion": "<referencia del documento firmado>"
}
```

- **TTL:** 72 h por defecto; 7 días máximo con justificación escrita.
- **Custodia:** una sola persona; el archivo `.pass` se guarda **separado** del `.enc`.
- **Prohibido:** copiar el respaldo a Git, a un chat de IA, a un bucket público o a una carpeta sincronizada.

---

## 5. Restauración en ambiente separado (nunca sobre el origen)

Orden obligatorio:

1. **Cluster nuevo** PostgreSQL 17, loopback o red privada, base vacía creada con `TEMPLATE template0`.
2. **Roles primero:** ejecutar `roles.sql`. Los roles con contraseña conservan su hash SCRAM; **no** se reescriben contraseñas productivas.
3. **Base después:** `pg_restore --exit-on-error --no-password -d <base_destino> base.dump`.
4. **Semillas y secuencias:** `pg_restore` incluye `setval`; verificar `last_value` de las secuencias críticas.
5. **ACL y RLS:** no se “arreglan” a mano. Si faltan privilegios, el restore está incompleto.

```powershell
pg_restore --exit-on-error --no-password `
  --host <destino> --port <destino> --username <admin> `
  --dbname <base_destino> "$destino\base.dump"
```

Si el restore falla:
- **No** se reintenta con `--clean` sobre una base con datos.
- Se conserva la base parcial para diagnóstico, se registra el primer `ERROR` y se detiene el procedimiento.

---

## 6. Invariantes que se deben medir (y comparar con el origen)

| Grupo | Medición | Criterio |
|---|---|---|
| Objetos | tablas, triggers, rutinas, vistas, políticas RLS | iguales al origen |
| RLS | tablas con `relrowsecurity` y `relforcerowsecurity` | iguales al origen |
| Filas | conteo por tabla crítica (ventas, detalles, pagos, inventario, bitácora, reservas) | iguales al origen |
| Secuencias | `last_value` de secuencias de negocio | iguales al origen |
| ACL | privilegios de `anon`/`authenticated`/`service_role` en `public` y `saas*` | 0 (contrato A) |
| Rutinas | `EXECUTE` de PUBLIC | 0 |
| Integridad | `stock_actual >= stock_reservado`; total de venta = Σ detalles | sin violaciones |
| Aplicación | login A/B, catálogo, una venta de contado, una reserva | smoke test del §8 |

Script de referencia: `102_ensayo_backup_restore_ficticio_5bs.py` (mismo comparador; en producción se ejecuta con `--origen-real` cuando exista, hoy no implementado a propósito).

---

## 7. Puntos sin retorno y rollback

| Fase | ¿Reversible? | Acción si falla |
|---|---|---|
| `pg_dump` | Sí | Descartar el archivo parcial; el origen no se modifica. |
| Cifrado | Sí | Repetir; el `.dump` plano aún existe hasta el `Remove-Item`. |
| `pg_restore` en base nueva | Sí | Borrar la base destino; el origen no se toca. |
| Aplicar migraciones al destino | Sí, si hay respaldo previo del destino | Restaurar el respaldo del destino. |
| Cambiar la cadena de conexión de la aplicación | **NO** | Volver el flag y la URL a la versión anterior; reiniciar el proceso. |
| Aplicar migraciones al ORIGEN | **NO** | Solo con respaldo verificado y ventana aprobada; sin retorno inmediato. |

Regla: **el origen nunca es el primer destino de una migración.** Staging primero.

---

## 8. Recuperación de la aplicación (post-restore)

1. Levantar FastAPI apuntando al cluster restaurado con `DATABASE_URL` de un rol restringido y `SAAS_ADMIN_DATABASE_URL` separado.
2. Con `SAAS_AUTH_ENABLED=false`, verificar `/health` y las rutas legacy.
3. Con `SAAS_AUTH_ENABLED=true` **solo en staging**, ejecutar el smoke:
   - login empresa A y B;
   - catálogo A/B disjunto;
   - una venta de contado y su replay idempotente;
   - una reserva y su cancelación;
   - `GET /auth/me` con el token del tenant;
   - intento cruzado → 404/403.
4. Revisar logs: cero `ConnectionTimeout`, cero `InsufficientPrivilege` inesperado.

---

## 9. Limpieza segura

```powershell
# 9.1 Destruir el cluster de restauración (solo el de prueba)
pg_ctl -D <pgdata_destino> -m fast stop
Remove-Item -Recurse -Force <pgdata_destino>

# 9.2 Destruir el respaldo al vencer el TTL
Remove-Item "$destino\base.dump.enc"
Remove-Item "$destino\.pass"
Remove-Item "$destino\manifiesto_b2.json"   # opcional: conservar hash en el acta
```

- El manifiesto (sin secretos) puede conservarse como evidencia.
- El borrado se registra con fecha, hora y responsable.
- Si el respaldo se copió a otro medio, se borra también allí (lista de medios en el acta).

---

## 10. Lo que falta para cerrar B2 (una sola decisión)

1. Autorización firmada (documento `104`).
2. Ejecución real del §3 + §4 + §5 + §6 en una ventana aprobada.
3. Acta con: hash del respaldo, conteos origen/destino, diferencias (deben ser 0), firmante.

Mientras eso no ocurra, **B2 = `PENDIENTE DE AUTORIZACIÓN`** y producción **NO-GO**, aunque el ensayo ficticio esté en verde.
