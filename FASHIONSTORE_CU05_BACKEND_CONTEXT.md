# FashionStore — Contexto Backend CU05
## Consultar Bitácora del Sistema

## Objetivo
Implementar CU05 como funcionalidad **solo lectura** sobre la tabla `bitacora`.

Actor funcional: Administrador.
Autorización técnica: usuario autenticado con `CONSULTAR_BITACORA + CONSULTAR` mediante `require_permission` de CU04.

No crear endpoints de escritura, no editar ni eliminar bitácora.

## Stack y arquitectura
FastAPI + SQLAlchemy 2 + Psycopg 3 + PostgreSQL/Supabase.

Arquitectura obligatoria:
`Router → Service → Repository → SQLAlchemy → PostgreSQL`

No usar `Base.metadata.create_all()`. No DDL. No migraciones. No tocar `.env`, Supabase ni git.

## Estado previo
CU03 Usuarios y CU04 Roles/Permisos ya están completos.
Ya existen:
- `get_current_user`
- `require_permission(funcion, accion)`
- modelo ORM `Bitacora`
- auditoría con `set_config('app.usuario_id', ..., true)`
- triggers de auditoría

No duplicar modelos ni infraestructura.

## Tabla real `bitacora`
Columnas confirmadas:
- `id BIGINT NOT NULL`
- `usuario_id BIGINT NULL`
- `fecha_hora TIMESTAMPTZ NOT NULL`
- `ip VARCHAR NULL`
- `accion VARCHAR NOT NULL`
- `entidad_afectada VARCHAR NULL`
- `descripcion TEXT NULL`

`usuario_id` puede ser NULL; la API debe permitirlo y el frontend mostrará “Sistema”.
`ip` puede ser NULL; no intentar reconstruirla.

## Datos observados
Actualmente existen eventos sobre entidades como:
- cliente
- empleado
- inventario
- producto
- rol
- sucursal
- usuario
- variante_producto

Acciones observadas:
- CREAR
- MODIFICAR
- ELIMINAR

No hardcodear estos valores como única fuente; obtener catálogos desde BD.

## Reglas de negocio
1. Bitácora es solo lectura.
2. Todos los endpoints requieren `CONSULTAR_BITACORA + CONSULTAR`.
3. Orden por defecto: `fecha_hora DESC, id DESC`.
4. Debe existir paginación.
5. Los filtros deben combinarse.
6. La API debe devolver fechas ISO 8601.
7. No exponer `password_hash` ni datos sensibles de Usuario.
8. `usuario_id = NULL` debe manejarse sin error.
9. Consultar bitácora NO debe generar una nueva fila de bitácora.
10. No crear POST/PATCH/DELETE para bitácora.
11. Si `fecha_desde > fecha_hasta` devolver 400.

## API propuesta
### Listado
`GET /bitacora`

Filtros opcionales:
- `buscar`
- `usuario_id`
- `accion`
- `entidad_afectada`
- `fecha_desde`
- `fecha_hasta`
- `limit` (default 50, max 100)
- `offset` (default 0)

`buscar` debe poder buscar case-insensitive en descripción, acción, entidad y correo del usuario cuando exista.

Respuesta sugerida:
```json
{
  "items": [
    {
      "id": 162,
      "fecha_hora": "2026-09-05T12:00:00-04:00",
      "ip": null,
      "accion": "MODIFICAR",
      "entidad_afectada": "usuario",
      "descripcion": "Usuario actualizado",
      "usuario": {
        "id": 1,
        "correo": "admin@fashionstore.test",
        "rol": "ADMINISTRADOR"
      }
    }
  ],
  "total": 162,
  "limit": 50,
  "offset": 0
}
```

### Detalle
`GET /bitacora/{bitacora_id}`

404 si no existe.

### Catálogos de filtros
`GET /bitacora/catalogos`

Debe devolver valores reales de BD:
```json
{
  "acciones": ["CREAR", "MODIFICAR", "ELIMINAR"],
  "entidades": ["usuario", "empleado", "rol"],
  "usuarios": [
    {"id": 1, "correo": "admin@fashionstore.test"}
  ]
}
```

Declarar `/bitacora/catalogos` antes de `/{bitacora_id}` si la estructura de router puede generar colisión.

## Estructura sugerida
Crear si no existe:
```text
app/modules/bitacora/
├── api/
├── repositories/
├── schemas/
└── services/
```

Schemas sugeridos:
- BitacoraUsuarioResumen
- BitacoraResponse
- BitacoraDetalleResponse
- BitacoraListResponse
- BitacoraCatalogosResponse
- BitacoraUsuarioFiltroResponse

No crear schemas de escritura.

## Repository
Responsabilidades:
- listado paginado
- total con los mismos filtros
- detalle por id
- distinct acciones
- distinct entidades
- usuarios para filtro
- join/outerjoin con Usuario y Rol
- evitar N+1

Usar SQLAlchemy seguro, sin interpolación manual.

## Service
Responsabilidades:
- normalizar filtros
- validar rango de fechas
- preparar respuesta
- manejar semántica de fechas

No modificar BD.

## Seguridad
Usar:
`require_permission("CONSULTAR_BITACORA", "CONSULTAR")`

Pruebas:
- sin token → 401
- autenticado sin permiso → 403
- con permiso → 200

## Rendimiento
La bitácora crecerá. Obligatorio:
- paginación
- no cargar toda la tabla
- query de total coherente
- evitar N+1

No crear índices en CU05; si se detecta necesidad, solo reportarla.

## Pruebas backend
Imports:
- `python -c "import app.models; print('MODELOS OK')"`
- `python -c "from app.main import app; print('APP OK')"`

Probar:
1. GET /bitacora
2. paginación
3. buscar
4. filtro usuario
5. filtro acción
6. filtro entidad
7. rango fechas
8. combinación de filtros
9. rango inválido → 400
10. detalle existente
11. detalle inexistente → 404
12. catálogos
13. sin token → 401
14. sin permiso → 403
15. con permiso → 200
16. usuario NULL si existe
17. verificar que consultar no crea filas nuevas

Regresión:
- CU03 Usuarios
- CU04 Roles/Permisos
- auth/login
- auth/me
- productos
- categorías
- sucursales

## Criterio de terminado
CU05 backend completo cuando exista consulta paginada, filtros, detalle, catálogos, autorización granular, nulls manejados, cero escritura y regresión verde.
