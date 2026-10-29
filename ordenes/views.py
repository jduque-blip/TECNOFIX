"""Pantallas del cliente y del técnico para sus equipos (órdenes de servicio)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Q
from django.http import Http404

from core.middleware import amable
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from comunicacion import avisos
from comunicacion.permisos import contacto_para_orden

from . import presupuestos, views_pagos, views_presupuesto
from .forms import EdicionOrdenForm, EstadoTecnicoForm, NotaForm, SolicitudEstadoForm, SolicitudForm
from .models import Orden


def _propias(usuario):
    """Órdenes que le pertenecen a este usuario según su rol."""
    consulta = Orden.objects.select_related('servicio', 'tecnico', 'cliente').prefetch_related('fotos', 'fotos_avance')
    if usuario.es_cliente:
        return consulta.filter(cliente=usuario)
    if usuario.es_tecnico:
        return consulta.filter(tecnico=usuario)
    return consulta.none()


def _orden_de_atencion(o):
    """Primero lo atrasado, luego lo que vence antes; lo que no tiene fecha va al final."""
    return (not o.atrasada, o.fecha_estimada is None, o.fecha_estimada or o.creada_en.date(), o.pk)


def contexto_panel(usuario, q='', estado='', **extra):
    """Lo que necesita el panel del cliente o del técnico: equipos en proceso, entregados y, para el técnico, su resumen."""
    propias = _propias(usuario)
    en_proceso = propias.exclude(estado=Orden.Estado.ENTREGADO)
    estados_validos = {e for e, _ in Orden.Estado.choices if e != Orden.Estado.ENTREGADO}
    if estado in (Orden.Estado.CANCELADA, Orden.Estado.RECHAZADA):
        # Siguen figurando así aunque el equipo ya se haya entregado
        en_proceso = propias.filter(Q(estado=estado) | Q(resultado=estado))
    elif estado in estados_validos:
        en_proceso = en_proceso.filter(estado=estado)
    else:
        estado = ''
    q = (q or '').strip()[:100]
    if q:
        filtro = Q(modelo__icontains=q) | Q(servicio__nombre__icontains=q)
        if q.lstrip('#').isdigit():
            filtro |= Q(pk=int(q.lstrip('#')))
        if usuario.es_tecnico:
            filtro |= Q(cliente__nombre__icontains=q)
        en_proceso = en_proceso.filter(filtro)
    en_proceso = sorted(en_proceso, key=_orden_de_atencion)
    entregados = propias.filter(estado=Orden.Estado.ENTREGADO).order_by('-entregada_en')
    contexto = {
        'en_proceso': en_proceso,
        'entregados': entregados,
        'form_solicitud': SolicitudForm(auto_id='sol_%s'),
        'modal_abierto': None,
        'q': q, 'filtro_estado': estado,
        'estados_filtro': [(e, n) for e, n in Orden.Estado.choices if e in estados_validos],
    }
    if usuario.es_tecnico:
        contexto['resumen'] = resumen_tecnico(usuario)
    contexto.update(extra)
    return contexto


def resumen_tecnico(tecnico):
    """Cifras del técnico: lo que tiene abierto, lo atrasado y cómo ha cumplido las entregas."""
    propias = Orden.objects.filter(tecnico=tecnico)
    abiertas = list(propias.exclude(estado__in=Orden.CERRADAS))
    entregadas = list(propias.filter(estado=Orden.Estado.ENTREGADO))
    con_fecha = [o for o in entregadas if o.fecha_estimada]
    a_tiempo = sum(1 for o in con_fecha if timezone.localtime(o.entregada_en).date() <= o.fecha_estimada)
    return {
        'abiertas': len(abiertas),
        'atrasadas': sum(1 for o in abiertas if o.atrasada),
        'listas': sum(1 for o in abiertas if o.estado == Orden.Estado.LISTO),
        'entregadas': len(entregadas),
        'cumplimiento': round(100 * a_tiempo / len(con_fecha)) if con_fecha else None,
    }


@login_required
@require_POST
def solicitar(request):
    """El cliente pide un servicio nuevo."""
    if not request.user.es_cliente:
        messages.error(request, 'Solo los clientes pueden solicitar servicios.')
        return redirect('panel')
    form = SolicitudForm(request.POST, request.FILES, auto_id='sol_%s')
    if form.is_valid():
        orden = form.guardar(request.user)
        messages.success(request, f'Solicitud #{orden.pk} enviada. Te avisaremos cuando avance.')
        return redirect('panel')
    # Con errores se vuelve al panel con la ventana abierta y lo que escribió
    contexto = contexto_panel(request.user, form_solicitud=form, modal_abierto='modal-solicitud')
    return render(request, 'core/panel.html', contexto)


@login_required
@amable
def equipo(request, pk):
    """Detalle de un equipo: estado, historial y fotos. Cada quien ve solo lo suyo."""
    if request.user.es_admin:
        return redirect('orden_admin', pk=pk)
    orden = get_object_or_404(_propias(request.user), pk=pk)
    puede_cancelar = request.user.es_cliente and not orden.congelada and orden.tecnico_id is not None and orden.estado in ('RECIBIDO', 'DIAGNOSTICO', 'REPARACION')
    return render(request, 'ordenes/detalle.html', {
        'puede_cancelar': puede_cancelar, 'cargo_cancelar': presupuestos.cargo_cancelacion(orden) if puede_cancelar else 0,
        'siguiente_paso': presupuestos.siguiente_paso(orden) if request.user.es_tecnico else None,
        'orden': orden, 'historial': orden.historial.select_related('usuario'),
        'contacto': contacto_para_orden(request.user, orden),
        'cambios_fecha': orden.cambios_fecha.select_related('usuario'),
        'fotos_avance': orden.fotos_avance.all(),
        'notas': orden.notas.select_related('autor') if request.user.es_tecnico else orden.notas.filter(visible_cliente=True).select_related('autor'),
        'form_estado': EstadoTecnicoForm(orden=orden, initial={'estado': orden.estado}) if request.user.es_tecnico else None,
        'form_nota': NotaForm(auto_id='nota_%s') if request.user.es_tecnico else None,
        'form_retroceso': SolicitudEstadoForm(orden=orden, auto_id='retro_%s') if request.user.es_tecnico else None,
        'solicitudes': orden.solicitudes_estado.select_related('tecnico')[:5] if request.user.es_tecnico else [],
        **views_presupuesto.contexto(orden, request.user),
        **views_pagos.contexto(orden, request.user),
    })


@login_required
@require_POST
def equipo_cancelar(request, pk):
    """El cliente cancela su equipo cuando ya está en proceso: paga la mano de obra y se le devuelve."""
    if not request.user.es_cliente:
        raise Http404
    get_object_or_404(Orden, pk=pk, cliente=request.user)
    try:
        cargo = presupuestos.cancelar_por_cliente(pk, request.user, request.POST.get('motivo', ''))
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, 'Cancelaste el servicio de tu equipo.' + (f' Te cobraremos ${cargo} más IVA por el trabajo ya hecho y podrás retirar tu equipo sin reparar.' if cargo > 0 else ' No tiene ningún cobro: podrás retirar tu equipo.'))
    return redirect('equipo', pk=pk)


def _orden_modificable(request, pk):
    """La orden del cliente que sigue sin técnico. Devuelve (orden, None) o (None, respuesta) si ya no se puede."""
    if not request.user.es_cliente:
        raise Http404
    orden = get_object_or_404(Orden.objects.select_for_update(), pk=pk, cliente=request.user)
    if not orden.cliente_puede_modificar:
        messages.error(request, 'Esta solicitud ya tiene técnico asignado y no se puede modificar. Escríbenos si necesitas un cambio.')
        return None, redirect('equipo', pk=pk)
    return orden, None


@login_required
def equipo_editar(request, pk):
    """El cliente corrige su solicitud mientras no tenga técnico."""
    with transaction.atomic():
        orden, rechazo = _orden_modificable(request, pk)
        if rechazo:
            return rechazo
        if request.method == 'POST':
            form = EdicionOrdenForm(request.POST, request.FILES, orden=orden, auto_id='ed_%s')
            if form.is_valid():
                form.guardar_cambios(request.user)
                messages.success(request, 'Solicitud actualizada')
                return redirect('equipo', pk=pk)
        else:
            form = EdicionOrdenForm(
                orden=orden, auto_id='ed_%s',
                initial={'servicio': orden.servicio_id, 'modelo': orden.modelo, 'problema': orden.problema},
            )
        return render(request, 'ordenes/editar.html', {
            'orden': orden, 'form': form, 'fotos': orden.fotos.all(),
            'cupo': max(0, 4 - orden.fotos.count()),
        })


@login_required
@require_POST
def equipo_eliminar(request, pk):
    """El cliente elimina su solicitud mientras no tenga técnico."""
    with transaction.atomic():
        orden, rechazo = _orden_modificable(request, pk)
        if rechazo:
            return rechazo
        try:
            for foto in orden.fotos.all():
                foto.imagen.delete(save=False)
            numero, modelo = orden.pk, orden.modelo
            orden.delete()
            avisos.solicitud_eliminada(numero, modelo, request.user)
        except ProtectedError:
            messages.error(request, 'Esta solicitud tiene registros ligados y no se puede eliminar.')
            return redirect('equipo', pk=pk)
    messages.success(request, 'Solicitud eliminada')
    return redirect('panel')
