from django.urls import path

from . import views

urlpatterns = [
    path('panel/finanzas/', views.gastos, name='gastos'),
    path('panel/finanzas/capital/', views.capital_guardar, name='capital_guardar'),
    path('panel/finanzas/nuevo/', views.gasto_nuevo, name='gasto_nuevo'),
    path('panel/finanzas/<int:pk>/editar/', views.gasto_editar, name='gasto_editar'),
    path('panel/finanzas/<int:pk>/eliminar/', views.gasto_eliminar, name='gasto_eliminar'),
]
