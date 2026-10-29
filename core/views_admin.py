"""Ajustes del sitio que cambia el administrador."""

from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from ordenes.forms import FotoForm
from usuarios.permisos import solo_admin

from .models import Portada


@solo_admin
@require_POST
def portada_foto(request):
    """Sube o cambia la imagen grande de la página principal."""
    form = FotoForm(request.POST, request.FILES)
    if form.is_valid():
        portada = Portada.actual()
        anterior = portada.foto.name
        portada.foto = form.cleaned_data['foto']
        portada.save()
        if anterior:
            portada.foto.storage.delete(anterior)
        messages.success(request, 'Imagen de la portada actualizada')
    else:
        messages.error(request, form.errors['foto'][0])
    return redirect('tarifas')


@solo_admin
@require_POST
def portada_quitar(request):
    portada = Portada.actual()
    if portada.foto:
        portada.foto.delete(save=True)
        messages.success(request, 'Imagen de la portada quitada; se muestra la de ejemplo')
    return redirect('tarifas')
