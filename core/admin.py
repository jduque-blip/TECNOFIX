from django.contrib import admin

from .models import Configuracion, Portada


@admin.register(Configuracion)
class ConfiguracionAdmin(admin.ModelAdmin):
    list_display = ('clave', 'valor', 'descripcion')


admin.site.register(Portada)
