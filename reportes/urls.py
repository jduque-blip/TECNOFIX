from django.urls import path

from . import views

urlpatterns = [
    path('panel/reportes/', views.indice, name='reportes'),
    path('panel/reportes/<slug:clave>.<slug:formato>', views.descargar, name='reporte_descargar'),
    path('equipos/<int:pk>/factura.pdf', views.factura, name='factura_pdf'),
]
