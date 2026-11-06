# 5BO / Etapa F - Extraccion AUTORIZADA del esquema real (NO ejecutable por defecto)

Procedimiento para que **el estudiante** (o quien tenga autorizacion escrita)
obtenga el esquema historico completo del entorno protegido y lo entregue para
la fase 5BP. OpenCode no se conecta a Supabase/Render/produccion: este script
solo se ejecuta si el estudiante lo lanza a mano con `-Autorizado`.

## Reglas

1. Ejecutar **solo** con autorizacion concreta de lectura y credenciales
   vigentes del dueno del entorno.
2. La salida va a una carpeta **NO versionada** (por defecto
   `%USERPROFILE%\Documents\fashionstore-esquema-5bp\`), nunca al repositorio.
3. El dump `--schema-only` **no incluye datos**, pero puede contener nombres de
   usuarios/roles y comentarios. Revisar y redactar antes de compartir.
4. No usar `--no-owner` ni `--no-privileges`: en este caso el objetivo es
   justamente conservar propietarios y ACL para auditarlos.
5. Prohibido usarlo contra `127.0.0.1` (los laboratorios locales ya tienen su
   propio runner) o sin el switch `-Autorizado`.

## Uso

```powershell
powershell -ExecutionPolicy Bypass -File database/saas/80_extraccion_esquema_autorizada_5bo.ps1 `
  -Servidor "db.<proyecto>.supabase.co" `
  -Puerto 5432 `
  -BaseDatos "postgres" `
  -Usuario "postgres" `
  -CarpetaSalida "$env:USERPROFILE\Documents\fashionstore-esquema-5bp" `
  -Autorizado
```

La contrasena se pide de forma interactiva (`Read-Host -AsSecureString`) y se
pasa por `PGPASSWORD` **solo al proceso hijo**; nunca se imprime ni se escribe
en disco.

## Salidas

| Archivo | Contenido |
|---|---|
| `esquema_<fecha>.sql` | `pg_dump --schema-only --no-publications --no-subscriptions` (sin datos) |
| `catalogo_<fecha>.txt` | Propietarios, `SECURITY DEFINER`, ACL, triggers, `search_path`, politicas RLS, extensiones y version |
| `inventario_<fecha>.json` | Resumen legible para comparar con la reconstruccion 5BL |

## Que se compara despues (5BP)

- 41 tablas historicas vs `database/saas/75_esquema_historico_reconstruido_5bl.sql`.
- 27 triggers vs los 14 reconstruidos.
- 24 rutinas vs las 13 reconstruidas.
- Propietario y ACL de cada funcion `SECURITY DEFINER`.
- Politicas RLS efectivas y `FORCE ROW LEVEL SECURITY`.

**Sin este artefacto la activacion productiva sigue BLOQUEADA.** Un dump no
prueba por si solo que los privilegios sean correctos: la revision de ACL y
propietarios es obligatoria.
