from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views, views_admin

urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('panel/', views.panel, name='panel'),
    path('panel/portada/foto/', views_admin.portada_foto, name='portada_foto'),
    path('panel/portada/quitar/', views_admin.portada_quitar, name='portada_quitar'),
    path('salir/', LogoutView.as_view(next_page='inicio'), name='salir'),
]
