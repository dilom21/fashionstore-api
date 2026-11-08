# Contratos backend para Angular: autenticacion y CU05 SaaS

## Selector publico

`GET /auth/tenant/empresas` (sin JWT)

```json
[{"slug":"vanter-men","nombre_comercial":"Vanter Men"}]
```

Solo incluye empresas activas con plan y suscripcion activa y vigente. El
orden es `nombre_comercial`, `slug`. No expone IDs ni datos de contacto.

## Registro publico de cliente

`POST /auth/tenant/clientes/registro` (sin JWT)

```json
{
  "empresa_slug":"vanter-men",
  "correo":"cliente@ejemplo.com",
  "password":"...",
  "password_confirmacion":"...",
  "nombre":"Ana",
  "apellido":"Perez",
  "ci":"123456",
  "telefono":"70000000",
  "sexo":"FEMENINO",
  "fecha_nacimiento":"1995-01-01"
}
```

Respuesta `201` (no contiene JWT):

```json
{
  "mensaje":"Cuenta creada correctamente. Inicie sesion para continuar.",
  "usuario_id":10,
  "cliente_id":20,
  "empresa_slug":"vanter-men",
  "correo":"cliente@ejemplo.com",
  "nombre":"Ana",
  "apellido":"Perez"
}
```

Un correo global existente se rechaza con `409`; no se vincula a otra empresa
sin una futura comprobacion autenticada de propiedad. Empresa no habilitada:
`404`; contrato bootstrap no instalado: `503`; validacion: `400/422`.

## Login e identidad existentes

`POST /auth/tenant/login`: recibe `empresa_slug`, `correo`, `password` y
`contexto` (`cliente` o `personal`); responde con bearer JWT y el contexto de
empresa. `GET /auth/tenant/me` exige ese JWT y conserva su contrato actual.

## CU05 Bitacora SaaS

- `GET /tenant/bitacora`: filtros `buscar`, `usuario_id`, `accion`,
  `entidad_afectada`, `fecha_desde`, `fecha_hasta`, `limit`, `offset`.
- `GET /tenant/bitacora/catalogos`: `acciones`, `entidades`, `usuarios`.
- `GET /tenant/bitacora/{bitacora_id}`: detalle de un evento de la empresa.

Las tres rutas exigen JWT de contexto `personal` y el permiso empresarial
`CONSULTAR_BITACORA / CONSULTAR`. La respuesta paginada mantiene
`items`, `total`, `limit`, `offset`. Un evento ajeno se presenta como `404`.

Después de un registro exitoso, Angular debe volver al login conservando solo
`empresa_slug` y `correo`; nunca debe transportar ni persistir la contrasena.
