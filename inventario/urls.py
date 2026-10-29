from django.urls import path

from . import views

urlpatterns = [
    path('inventario/', views.lista, name='inventario'),
    path('inventario/nuevo/', views.producto_nuevo, name='producto_nuevo'),
    path('inventario/margen/', views.margen_guardar, name='margen_guardar'),
    path('inventario/movimientos/', views.movimientos, name='movimientos'),
    path('inventario/proveedores/', views.proveedores, name='proveedores'),
    path('inventario/proveedores/<int:pk>/contacto/', views.proveedor_contacto, name='proveedor_contacto'),
    path('inventario/categorias/', views.categoria_crear, name='categoria_crear'),
    path('inventario/categorias/<int:pk>/eliminar/', views.categoria_eliminar, name='categoria_eliminar'),
    path('inventario/proveedores/<int:pk>/', views.proveedor_editar, name='proveedor_editar'),
    path('inventario/proveedores/<int:pk>/eliminar/', views.proveedor_eliminar, name='proveedor_eliminar'),
    path('inventario/<int:pk>/', views.producto_detalle, name='producto_detalle'),
    path('inventario/<int:pk>/editar/', views.producto_editar, name='producto_editar'),
    path('inventario/<int:pk>/eliminar/', views.producto_eliminar, name='producto_eliminar'),
    path('inventario/<int:pk>/activar/', views.producto_activar, name='producto_activar'),
]
