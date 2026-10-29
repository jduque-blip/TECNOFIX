from django.urls import path

from . import views

urlpatterns = [
    path('notificaciones/', views.notificaciones, name='notificaciones'),
    path('notificaciones/leer/', views.notificaciones_leer, name='notificaciones_leer'),
    path('notificaciones/<int:pk>/', views.notificacion_abrir, name='notificacion_abrir'),
    path('mensajes/', views.bandeja, name='chat_bandeja'),
    path('mensajes/<int:pk>/', views.chat, name='chat'),
    path('mensajes/<int:pk>/nuevos/', views.chat_nuevos, name='chat_nuevos'),
]
