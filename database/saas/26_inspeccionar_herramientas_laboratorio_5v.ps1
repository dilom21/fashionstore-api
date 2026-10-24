# B3 / 5V — SOLO INSPECCION DE HERRAMIENTAS LOCALES EN WINDOWS.
# No invoca ejecutables, no abre conexiones, no crea archivos/directorios,
# clústeres, contenedores, roles, policies ni datos. No lee secretos/.env.
$ErrorActionPreference = 'Stop'

# Este nombre/puerto son una PROPUESTA NO APROBADA, no un servidor existente.
$puertoPropuesto = 55432
$rutaDatosPropuesta = 'C:\SI2_Parcial1\fashionstore-rls-lab-5v-data'
$rutaPostgres18 = 'C:\Program Files\PostgreSQL\18\bin'

Write-Output '5V INSPECCION WINDOWS SOLO LECTURA - SIN SERVIDORES NI DDL'

# Get-Command consulta el PATH, NO ejecuta docker.exe.
$docker = Get-Command -Name 'docker.exe' -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $docker) {
    Write-Output '5V DOCKER_CLI=NO_ENCONTRADO'
} else {
    Write-Output '5V DOCKER_CLI=ENCONTRADO_NO_EJECUTADO'
}

# Test-Path no arranca PostgreSQL ni enumera datos de sus bases.
$initdb = Test-Path -LiteralPath (Join-Path $rutaPostgres18 'initdb.exe') -PathType Leaf
$pgCtl  = Test-Path -LiteralPath (Join-Path $rutaPostgres18 'pg_ctl.exe') -PathType Leaf
$pgConfig = Test-Path -LiteralPath (Join-Path $rutaPostgres18 'pg_config.exe') -PathType Leaf

if ($initdb) { Write-Output '5V PG18_INITDB=ENCONTRADO_NO_EJECUTADO' }
else { Write-Output '5V PG18_INITDB=NO_ENCONTRADO' }
if ($pgCtl) { Write-Output '5V PG18_PG_CTL=ENCONTRADO_NO_EJECUTADO' }
else { Write-Output '5V PG18_PG_CTL=NO_ENCONTRADO' }
if ($pgConfig) { Write-Output '5V PG18_PG_CONFIG=ENCONTRADO_NO_EJECUTADO' }
else { Write-Output '5V PG18_PG_CONFIG=NO_ENCONTRADO' }

if (Test-Path -LiteralPath $rutaDatosPropuesta) {
    # No se modifica ni examina el contenido de la carpeta existente.
    Write-Output '5V RUTA_DATOS_PROPUESTA=YA_EXISTE_NO_USAR'
} else {
    Write-Output '5V RUTA_DATOS_PROPUESTA=NO_EXISTE_NO_CREADA'
}

# Se inspecciona una TABLA DE SOCKETS del sistema, no se contacta PostgreSQL.
# No ver un listener no prueba que un puerto pueda reservarse mas adelante.
$puertoEstado = 'DESCONOCIDO'
$comandoPuertos = Get-Command -Name 'Get-NetTCPConnection' -ErrorAction SilentlyContinue
if ($null -ne $comandoPuertos) {
    try {
        $listeners = @(Get-NetTCPConnection -LocalPort $puertoPropuesto -State Listen -ErrorAction Stop)
        if ($listeners.Count -gt 0) {
            $puertoEstado = 'OCUPADO'
        } else {
            $puertoEstado = 'SIN_LISTENER_OBSERVADO'
        }
    } catch {
        # Algunos Windows devuelven error "No matching MSFT_NetTCPConnection
        # objects found" cuando el puerto no tiene listeners. No se infiere
        # DISPONIBLE si la consulta falla; se conserva DESCONOCIDO.
        $puertoEstado = 'DESCONOCIDO'
    }
}
Write-Output "5V PUERTO_55432=$puertoEstado"

# Solo clasifica la disponibilidad de binarios; NO comprueba daemon Docker,
# permisos, system_identifier, seguridad ni independencia del clúster.
if ($null -ne $docker) {
    Write-Output '5V OPCION_POSIBLE=DOCKER_CLI_REQUIERE_VERIFICACION'
}
if ($initdb -and $pgCtl) {
    Write-Output '5V OPCION_POSIBLE=PG18_INSTANCIA_SEPARADA_REQUIERE_AUTORIZACION'
}
if ($null -eq $docker -and -not ($initdb -and $pgCtl)) {
    Write-Output '5V OPCION_POSIBLE=HERRAMIENTAS_NO_CONFIRMADAS'
}

Write-Output '5V CLUSTER_INDEPENDIENTE=NO_VERIFICADO'
Write-Output '5V SYSTEM_IDENTIFIER=NO_MEDIDO'
Write-Output '5V AISLAMIENTO_RLS_A_B=NO_CERTIFICADO'
Write-Output '5V SIN CAMBIOS: NO iniciar Docker/initdb/pg_ctl ni crear roles sin autorizacion'
