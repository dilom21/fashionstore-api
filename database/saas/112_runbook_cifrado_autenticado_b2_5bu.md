# 5BU / Etapa L — Anexo B2: cifrado autenticado (AES-256-GCM) y PRE-B2

**Estado:** procedimiento vigente para cualquier respaldo B2 **futuro y autorizado**.
**Sustituye, para datos reales, al §4 de `103_runbook_backup_restore_retorno_5bs.md`** (AES-256-CBC + PBKDF2 + SHA-256 externo). El historial de la Etapa J se conserva intacto: ese formato queda **prohibido** para datos reales y solo se admite como registro histórico del ensayo ficticio.
**B2 sigue `NO AUTORIZADO`.** Este anexo no autoriza ninguna extracción.

---

## 0. Por qué se cambia

| Problema del formato J (v1) | Consecuencia | Corrección v2 |
|---|---|---|
| AES-256-CBC sin MAC | El ciphertext puede alterarse sin detección criptográfica | AES-256-GCM (AEAD) con tag de 128 bits |
| SHA-256 del archivo publicado aparte | Quien altera el paquete puede recalcular el hash | El hash del plaintext va **dentro** de la cabecera autenticada (AAD) |
| Contraseña en archivo `.pass` junto al respaldo | Compromiso del paquete y de la clave en un solo lugar | Frase solo en memoria / gestor de secretos; sin `.pass` |
| Sin control de versión de formato | Riesgo de descifrar con el esquema equivocado | Cabecera versionada; v1 se rechaza, sin fallback |

---

## 1. Formato v2 `FSB2AEAD`

| Campo | Bytes | Contenido |
|---|---|---|
| `magic` | 8 | `FSB2AEAD` |
| `version` | 1 | `0x02` |
| `suite` | 1 | `0x01` = AES-256-GCM |
| `len_header` | 4 | uint32 big-endian |
| `header` | N | JSON canónico UTF-8 (**autenticado como AAD**) |
| `ciphertext` | M | AES-256-GCM |
| `tag` | 16 | tag GCM (autentica cabecera + ciphertext) |

Cabecera (claves): `formato`, `version`, `suite`, `kdf`, `iteraciones`, `salt_b64`, `nonce_b64`, `bytes_plano`, `sha256_plano`, `fecha_utc`, `nombre_original`, `metadatos`.

**AAD** = `magic ‖ version ‖ suite ‖ len_header ‖ header`. Cambiar iteraciones, salt, nonce, tamaño o metadatos invalida el tag.

### Parámetros

| Parámetro | Valor | Nota |
|---|---|---|
| Cifra | AES-256-GCM | clave de 256 bits |
| KDF | PBKDF2-HMAC-SHA256 | mínimo aceptado 200 000 iteraciones |
| Iteraciones | **600 000** | valor por defecto del instrumento |
| Salt | 16 bytes aleatorios | uno por archivo |
| Nonce | 12 bytes aleatorios | uno por archivo; nunca se reutiliza con la misma clave |
| Tag | 16 bytes | verificación obligatoria antes de publicar plaintext |
| Procesamiento | streaming 1 MiB | no carga el volcado completo en RAM |
| Dependencia | `cryptography==46.0.3` | declarada en `requirements.txt` |

---

## 2. Secuencia obligatoria (verificar → descifrar → restaurar)

```powershell
# 0) Puerta PRE-B2: falla cerrada si falta autorización, el destino es inseguro
#    o el puerto está ocupado/protegido.
python database\saas\110_cifrado_autenticado_b2_5bu.py preflight `
  --origen-host <host> --origen-puerto 5432 `
  --destino "$env:LOCALAPPDATA\Temp\opencode\fs_b2_<sello>" `
  --puerto-restore 55452 `
  --autorizacion database\saas\104_solicitud_autorizacion_datos_5bs.md

# 1) Cifrar (frase interactiva; NUNCA por argumento)
python database\saas\110_cifrado_autenticado_b2_5bu.py cifrar `
  "$destino\base.dump" "$destino\base.dump.fsb2" --meta etapa=B2 --meta alcance=public

# 2) Verificar el tag ANTES de crear cualquier base destino
python database\saas\110_cifrado_autenticado_b2_5bu.py verificar "$destino\base.dump.fsb2"

# 3) Solo si (2) devuelve "autenticado": true
python database\saas\110_cifrado_autenticado_b2_5bu.py descifrar `
  "$destino\base.dump.fsb2" "$destino\base.dump"
```

Reglas:

1. `verificar` **no escribe plaintext**: autentica descartando los bloques.
2. `descifrar` escribe a un temporal oculto y solo lo publica tras el tag; ante cualquier excepción borra el temporal.
3. `pg_restore` se ejecuta **después** de (3) y el `.dump` plano se elimina en el mismo bloque `finally`.
4. Si se usa `--passfile`, el archivo de frase debe vivir **fuera** de la carpeta del respaldo, con ACL restringida, y destruirse al vencer el TTL. Para datos reales se prefiere gestor de secretos o entrada interactiva.
5. La salida no se sobrescribe: si existe, el instrumento aborta (`DestinoExistente`).

---

## 3. Semántica de rechazo (fail-closed)

| Situación | Excepción | Efecto |
|---|---|---|
| Frase de paso incorrecta | `AutenticacionFallida` | Sin plaintext, sin base destino |
| Bit alterado en ciphertext | `AutenticacionFallida` | Sin plaintext, sin base destino |
| Bit alterado en el tag | `AutenticacionFallida` | Sin plaintext, sin base destino |
| Cabecera manipulada (iteraciones, salt, nonce, tamaño) | `AutenticacionFallida` | AAD no coincide |
| Cabecera de otro respaldo intercambiada | `AutenticacionFallida` | clave/nonce/tag ajenos |
| Archivo truncado | `AutenticacionFallida` | tag incompleto o inválido |
| Paquete legado v1 (`Salted__` / CBC) | `FormatoNoSoportado` | **sin fallback** a CBC |
| Firma desconocida | `FormatoNoSoportado` | no se intenta descifrar |
| Salida existente | `DestinoExistente` | no se sobrescribe |

En todos los casos el flujo aborta **antes** de `CREATE DATABASE` y antes de `pg_restore`.

---

## 4. PRE-B2: qué impide empezar una extracción real

`preflight` devuelve `ok: false` y código de salida 1 si:

1. el bloque `AUTORIZACIÓN B2` de `104` no está rellenado (detecta `Autorizo / No autorizo:`, `Alcance autorizado`, `Custodio`, `TTL` y `Firma / fecha` con marcadores `<...>`);
2. el destino está dentro del repositorio Git;
3. el destino está en una carpeta sincronizada con la nube (OneDrive, Dropbox, Google Drive, iCloud, SharePoint);
4. el formato solicitado no es v2;
5. el puerto de restauración está protegido (5432, 55432–55451) o ya está en uso;
6. el puerto de restauración coincide con el de origen.

---

## 5. Respaldos v1 existentes

- Los respaldos **ficticios** de la Etapa J pueden conservarse como evidencia histórica; no se recifran.
- Un respaldo **real** con formato v1 **no se usa**: se repite la extracción (con autorización vigente) y se cifra en v2. No se “convierte” un paquete real sin autorización explícita.

---

## 6. Retención y rotación

- Salt y nonce nuevos por respaldo: dos paquetes del mismo dump **no** comparten clave derivada ni ciphertext.
- TTL por defecto 72 h; 7 días máximo con justificación escrita.
- Al vencer el TTL: borrar `.fsb2`, el plaintext temporal (si quedara), y **no** conservar la frase.
- La evidencia conserva solo: bytes, SHA-256, parámetros de KDF, conteos agregados y resultado de invariantes. Nunca la frase, el DSN ni muestras de filas.

---

## 7. Verificación exigida antes de declarar el instrumento listo (hecho en L)

| Prueba | Resultado esperado | Evidencia L |
|---|---|---|
| Roundtrip cifrar/descifrar con datos ficticios | hash plano idéntico | `EVIDENCIA_B2_AEAD_5BU_*` |
| Frase incorrecta | rechazo antes de crear la base | idem |
| Ciphertext / tag / cabecera alterados | rechazo antes de crear la base | idem |
| Cabecera intercambiada entre respaldos | rechazo | idem |
| Truncado | rechazo | idem |
| Formato v1 | rechazo explícito sin fallback | idem |
| Restauración ficticia | 0 diferencias en objetos, filas y secuencias | idem |
| Higiene | 0 plaintext residual, 0 `.pass` | idem |
