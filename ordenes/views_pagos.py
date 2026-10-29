"""Vistas de pagos: el cliente sube su comprobante; el administrador valida, cobra en efectivo y entrega."""

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from core.models import Configuracion
from usuarios.permisos import solo_admin

from . import pagos
from .forms import ComprobanteForm
from .models import Orden, Pago


def contexto(orden, usuario):
    """Lo que necesita la tarjeta de pago en el detalle del equipo."""
    historial = list(orden.pagos.select_related('validado_por'))
    total = orden.total_a_pagar
    pagado = next((p for p in historial if p.estado == Pago.Estado.CONFIRMADO), None)
    pendiente = next((p for p in historial if p.estado == Pago.Estado.PENDIENTE), None)
    return {
        'pagos': historial, 'pago_pagado': pagado, 'pago_pendiente': pendiente,
        'pago_total': total,
        'pago_visible': orden.estado in (Orden.Estado.LISTO, Orden.Estado.ENTREGADO) or bool(historial),
        'pago_puede_pagar': orden.estado == Orden.Estado.LISTO and total > 0 and not pagado and not pendiente,
        'datos_transferencia': Configuracion.datos_transferencia(),
        'form_comprobante': ComprobanteForm(auto_id='comp_%s') if usuario.es_cliente else None,
    }


def _ejecutar(request, pk, funcion, *args, exito=''):
    try:
        funcion(*args)
        if exito:
            messages.success(request, exito)
    except Orden.DoesNotExist:
        raise Http404
    except pagos.PagoError as e:
        messages.error(request, str(e))
    return redirect('orden_admin', pk=pk)


@login_required
@require_POST
def pago_enviar(request, pk):
    if not request.user.es_cliente:
        raise Http404
    orden = get_object_or_404(Orden, pk=pk, cliente=request.user)
    form = ComprobanteForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, next(iter(sum(form.errors.values(), []))))
        return redirect('equipo', pk=pk)
    try:
        pagos.enviar_transferencia(orden.pk, request.user, form.cleaned_data['comprobante'], form.cleaned_data['referencia'])
        messages.success(request, 'Recibimos tu comprobante. Te avisaremos cuando lo validemos.')
    except pagos.PagoError as e:
        messages.error(request, str(e))
    return redirect('equipo', pk=pk)


@login_required
@require_POST
def pago_efectivo_cliente(request, pk):
    if not request.user.es_cliente:
        raise Http404
    orden = get_object_or_404(Orden, pk=pk, cliente=request.user)
    try:
        pagos.avisar_efectivo(orden.pk, request.user)
        messages.success(request, 'Anotado. Acércate al taller para pagar en efectivo y retirar tu equipo.')
    except pagos.PagoError as e:
        messages.error(request, str(e))
    return redirect('equipo', pk=pk)


@solo_admin
@require_POST
def pago_efectivo(request, pk):
    return _ejecutar(request, pk, pagos.registrar_efectivo, pk, request.user, exito='Pago en efectivo registrado. Ya se puede entregar.')


@solo_admin
@require_POST
def pago_confirmar(request, pk, pago_pk):
    get_object_or_404(Pago, pk=pago_pk, orden_id=pk)
    return _ejecutar(request, pk, pagos.confirmar, pago_pk, request.user, exito='Pago confirmado. Ya se puede entregar.')


@solo_admin
@require_POST
def pago_rechazar(request, pk, pago_pk):
    get_object_or_404(Pago, pk=pago_pk, orden_id=pk)
    return _ejecutar(request, pk, pagos.rechazar, pago_pk, request.user, request.POST.get('motivo', ''), exito='Comprobante rechazado. El cliente fue avisado.')


@solo_admin
@require_POST
def entrega_sin_cobro(request, pk):
    return _ejecutar(request, pk, pagos.autorizar_sin_cobro, pk, request.user, exito='Entrega autorizada.')


@solo_admin
@require_POST
def entregar(request, pk):
    return _ejecutar(request, pk, pagos.entregar, pk, request.user, exito='Equipo entregado.')


@login_required
def comprobante(request, pk, pago_pk):
    """El comprobante solo lo ven el administrador y el cliente dueño de la orden."""
    pago = get_object_or_404(Pago.objects.select_related('orden'), pk=pago_pk, orden_id=pk)
    if not (request.user.es_admin or pago.orden.cliente_id == request.user.pk) or not pago.comprobante:
        raise Http404
    tipo = mimetypes.guess_type(pago.comprobante.name)[0] or 'application/octet-stream'
    respuesta = FileResponse(pago.comprobante.open('rb'), content_type=tipo)
    respuesta['Content-Disposition'] = 'inline'
    respuesta['X-Content-Type-Options'] = 'nosniff'
    return respuesta
