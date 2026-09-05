from app.models.seguridad import Rol, Usuario
from app.models.personas import Cliente, Empleado
from app.models.catalogo import Categoria, Producto, VarianteProducto
from app.models.inventario import Inventario

__all__ = [
    "Rol",
    "Usuario",
    "Cliente",
    "Empleado",
    "Categoria",
    "Producto",
    "VarianteProducto",
    "Inventario",
]