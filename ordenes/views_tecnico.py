"""Acciones del técnico sobre las órdenes que tiene asignadas."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .forms import EstadoTecnicoForm, FotosAvanceForm, NotaForm, SolicitudEstadoForm
from .models import Orden


def _orden_del_tecnico(request, pk):
    """La orden asignada a este técnico y todavía abierta. Devuelve (orden, None) o (None, respuesta)."""
    if not request.user.es_tecnico:
        raise Http404
    orden = get_object_or_404(Orden.objects.select_for_update().select_related('cliente', 'tecnico'), pk=pk, tecnico=request.user)
    if orden.estado == Orden.Estado.ENTREGADO:
        messages.error(request, 'Esta orden ya fue entregada y no se puede modificar.')
        return None, redirect('equipo', pk=pk)
    if orden.congelada:
        messages.error(request, 'Esta orden fue rechazada o cancelada. Solo el administrador puede reabrirla.')
        return None, redirect('equipo', pk=pk)
    return orden, None


@login_required
@require_POST
def cambiar_estado(request, pk):
    with transaction.atomic():
        orden, rechazo = _orden_del_tecnico(request, pk)
        if rechazo:
            return rechazo
        form = EstadoTecnicoForm(request.POST, orden=orden)
        if form.is_valid():
            orden = form.guardar(request.user)
            messages.success(request, f'Estado actualizado: {orden.get_estado_display()}')
        else:
            messages.error(request, form.errors['estado'][0])
    return redirect('equipo', pk=pk)


@login_required
@require_POST
def agregar_nota(request, pk):
    with transaction.atomic():
        orden, rechazo = _orden_del_tecnico(request, pk)
        if rechazo:
            return rechazo
        form = NotaForm(request.POST)
        if form.is_valid():
            form.guardar(orden, request.user)
            messages.success(request, 'Nota guardada')
        else:
            messages.error(request, form.errors['texto'][0])
    return redirect('equipo', pk=pk)


@login_required
@require_POST
def subir_fotos(request, pk):
    with transaction.atomic():
        orden, rechazo = _orden_del_tecnico(request, pk)
        if rechazo:
            return rechazo
        form = FotosAvanceForm(request.POST, request.FILES, orden=orden)
        if form.is_valid():
            fotos = form.guardar(request.user)
            messages.success(request, f'{len(fotos)} foto{"s" if len(fotos) != 1 else ""} subida{"s" if len(fotos) != 1 else ""}')
        else:
            messages.error(request, form.errors['fotos'][0])
    return redirect('equipo', pk=pk)


@login_required
@require_POST
def quitar_foto(request, pk, foto_pk):
    with transaction.atomic():
        orden, rechazo = _orden_del_tecnico(request, pk)
        if rechazo:
            return rechazo
        foto = get_object_or_404(orden.fotos_avance, pk=foto_pk)
        foto.imagen.delete(save=False)
        foto.delete()
        orden.registrar('El técnico quitó una foto del avance', request.user)
        messages.success(request, 'Foto quitada')
    return redirect('equipo', pk=pk)


@login_required
@require_POST
def pedir_retroceso(request, pk):
    with transaction.atomic():
        orden, rechazo = _orden_del_tecnico(request, pk)
        if rechazo:
            return rechazo
        form = SolicitudEstadoForm(request.POST, orden=orden)
        if form.is_valid():
            form.guardar(request.user)
            messages.success(request, 'Solicitud enviada. El administrador la revisará.')
        else:
            errores = form.non_field_errors() or next(iter(form.errors.values()))
            messages.error(request, errores[0])
    return redirect('equipo', pk=pk)
