# B3/5W — READ ONLY Windows: ruta propuesta de PGDATA y servicios PG.
# NO ejecuta binarios, NO crea/directorios/archivos/roles, NO abre conexiones.
# NO demuestra identidad de clúster ni aislamiento RLS.
$ErrorActionPreference = 'Stop'

$rutaProyecto = 'C:\SI2_Parcial1\fashionstore-api'
$rutaBinariosPG = 'C:\Program Files\PostgreSQL\18\bin'
$rutaPropuesta = 'C:\SI2_Parcial1\fashionstore-rls-lab-5v-data'
$rutaPadre = Split-Path -Path $rutaPropuesta -Parent

Write-Output '5W PREFLIGHT WIN READ ONLY - NO CREA PGDATA NI INICIA POSTGRES'

# Se identifica el directorio candidato SOLO por metadatos.
# No navegar el contenido de la base historica ni del clúster 5432.
if (Test-Path -LiteralPath $rutaPropuesta) {
    Write-Output '5W PGDATA_PROPUESTO=EXISTE_BLOQUEAR'
} else {
    Write-Output '5W PGDATA_PROPUESTO=NO_EXISTE_NO_CREADO'
}

if (Test-Path -LiteralPath $rutaPadre -PathType Container) {
    Write-Output '5W PADRE_PGDATA=EXISTE'
    # Un junction/symlink en algun ancestro puede redirigir escrituras
    # hacia un PGDATA ajeno en una fase posterior. En tal caso BLOQUEAR.
    $enlace = $false
    $actual = Get-Item -LiteralPath $rutaPadre -Force
    while ($null -ne $actual) {
        if (($actual.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            $enlace = $true
            break
        }
        $actual = $actual.Parent
    }
    if ($enlace) {
        Write-Output '5W ANCESTROS_REPARSE=DETECTADO_BLOQUEAR'
    } else {
        Write-Output '5W ANCESTROS_REPARSE=NINGUNO_OBSERVADO'
    }
} else {
    Write-Output '5W PADRE_PGDATA=NO_EXISTE_BLOQUEAR'
    Write-Output '5W ANCESTROS_REPARSE=NO_COMPROBADO'
}

# Verificar que la propuesta no este dentro del proyecto ni del binario PG18.
# La comprobacion textual NO resuelve junctions ni rutas del clúster fuente.
$pathCandidato = [IO.Path]::GetFullPath($rutaPropuesta).TrimEnd('\')
$pathProyecto = [IO.Path]::GetFullPath($rutaProyecto).TrimEnd('\')
$pathBinarios = [IO.Path]::GetFullPath($rutaBinariosPG).TrimEnd('\')
if (
    $pathCandidato.Equals($pathProyecto, [StringComparison]::OrdinalIgnoreCase) -or
    $pathCandidato.StartsWith(($pathProyecto + '\'), [StringComparison]::OrdinalIgnoreCase) -or
    $pathCandidato.Equals($pathBinarios, [StringComparison]::OrdinalIgnoreCase) -or
    $pathCandidato.StartsWith(($pathBinarios + '\'), [StringComparison]::OrdinalIgnoreCase)
) {
    Write-Output '5W RUTA_PROYECTO_BINARIOS=SOLAPAMIENTO_BLOQUEAR'
} else {
    Write-Output '5W RUTA_PROYECTO_BINARIOS=SIN_SOLAPAMIENTO_TEXTUAL'
}

# Inspeccion de metadatos de servicios; NO imprimir PathName porque
# puede incluir parametros privados. NO asumir que todos los servidores
# PostgreSQL corren como servicio ni que su PGDATA fue descubierto.
$comandoCim = Get-Command -Name 'Get-CimInstance' -ErrorAction SilentlyContinue
if ($null -eq $comandoCim) {
    Write-Output '5W SERVICIOS_POSTGRES=NO_CONSULTADOS'
} else {
    try {
        $servicios = @(
            Get-CimInstance -ClassName Win32_Service -ErrorAction Stop |
            Where-Object {
                $_.Name -match '(?i)^postgresql' -or
                $_.PathName -match '(?i)(?:pg_ctl(?:\.exe)?|postgres(?:\.exe)?)'
            }
        )
        Write-Output ("5W SERVICIOS_POSTGRES=CONTADOS_" + $servicios.Count)
    } catch {
        Write-Output '5W SERVICIOS_POSTGRES=NO_VERIFICABLE'
    }
}

# No existe evidendia suficiente para declarar un destino independiente:
# el PGDATA del clúster fuente y system_identifier siguen sin medirse.
Write-Output '5W PGDATA_CLUSTER_5432=NO_VERIFICADO'
Write-Output '5W SYSTEM_IDENTIFIER_ORIGEN_DESTINO=NO_MEDIDOS'
Write-Output '5W CLUSTER_NATIVO_INDEPENDIENTE=NO_CERTIFICADO'
Write-Output '5W DICTAMEN=BLOQUEADO_SIN_AUTORIZACION_EXPLICITA'
Write-Output '5W SIN CAMBIOS: NO ejecutar initdb/pg_ctl ni crear roles, policies o datos'
