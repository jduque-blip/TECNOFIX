"""Tarifas, servicios e IVA que configura el administrador."""

from core.middleware import amable
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import Http404
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.models import Configuracion, Portada
from usuarios.permisos import solo_admin

from comunicacion import avisos

from decimal import Decimal, InvalidOperation

from . import carga, presupuestos, views_pagos, views_presupuesto
from .forms import FotoForm, IvaForm, OrdenGestionForm, PrecioForm, ServicioForm, TarifaForm
from .models import Orden, Pago, PresupuestoItem, Servicio, SolicitudEstado, Tarifa


def _primer_error(form):
    for errores in form.errors.values():
        return errores[0]
    return 'Datos no válidos.'


def _servicio_o_general(valor):
    """El formulario manda el número del servicio; vacío significa tarifa general."""
    if valor in ('', None):
        return None
    if not str(valor).isdigit():
        raise Http404
    return get_object_or_404(Servicio, pk=int(valor))


@solo_admin
def tarifas(request):
    """IVA, imagen de la portada, y cada servicio con sus propias tarifas."""
    form_servicio = ServicioForm(auto_id='srv_%s')
    fallida = None  # (id del servicio o '', formulario con errores)
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'tarifa':
            servicio = _servicio_o_general(request.POST.get('servicio'))
            form = TarifaForm(request.POST, servicio=servicio, auto_id=f'tar{servicio.pk if servicio else "g"}_%s')
            if form.is_valid():
                form.save()
                messages.success(request, 'Tarifa añadida')
                return redirect('tarifas')
            fallida = (servicio.pk if servicio else None, form)
        elif accion == 'servicio':
            form_servicio = ServicioForm(request.POST, request.FILES, auto_id='srv_%s')
            if form_servicio.is_valid():
                nuevo = form_servicio.save()
                messages.success(request, f'Servicio «{nuevo.nombre}» añadido. Ahora añade sus tarifas.')
                return redirect(f"{reverse('tarifas')}#servicio-{nuevo.pk}")

    def formulario(servicio):
        clave = servicio.pk if servicio else None
        if fallida and fallida[0] == clave:
            return fallida[1]
        return TarifaForm(servicio=servicio, auto_id=f'tar{clave if clave else "g"}_%s')

    grupos = [
        {'servicio': s, 'tarifas': list(s.tarifas.all()), 'form': formulario(s)}
        for s in Servicio.objects.prefetch_related('tarifas')
    ]
    grupos.append({'servicio': None, 'tarifas': list(Tarifa.objects.filter(servicio__isnull=True)), 'form': formulario(None)})
    return render(request, 'panel/tarifas.html', {
        'tab': 'tar',
        'iva': Configuracion.iva(),
        'datos_transferencia': Configuracion.datos_transferencia(),
        'portada': Portada.actual(),
        'grupos': grupos,
        'form_servicio': form_servicio,
    })


@solo_admin
@require_POST
def iva_guardar(request):
    form = IvaForm(request.POST)
    if form.is_valid():
        Configuracion.objects.update_or_create(
            clave=Configuracion.IVA,
            defaults={'valor': form.cleaned_data['valor'], 'descripcion': 'Porcentaje de IVA que se suma a los presupuestos'},
        )
        messages.success(request, f'IVA actualizado a {form.cleaned_data["valor"].normalize():f}%')
    else:
        messages.error(request, _primer_error(form))
    return redirect('tarifas')


@solo_admin
@require_POST
def transferencia_guardar(request):
    texto = ' '.join(request.POST.get('datos', '').split())
    if len(texto) > 200:
        messages.error(request, 'Los datos de la cuenta son muy largos (máximo 200 letras).')
    else:
        Configuracion.guardar_datos_transferencia(texto)
        messages.success(request, 'Datos de la cuenta guardados' if texto else 'Datos de la cuenta borrados')
    return redirect('tarifas')


@solo_admin
@require_POST
def tarifa_precio(request, pk):
    tarifa = get_object_or_404(Tarifa, pk=pk)
    form = PrecioForm(request.POST)
    if form.is_valid():
        tarifa.precio = form.cleaned_data['precio']
        tarifa.save(update_fields=['precio'])
        messages.success(request, f'Precio de «{tarifa.nombre}» actualizado')
    else:
        messages.error(request, _primer_error(form))
    return redirect('tarifas')


@solo_admin
@require_POST
def tarifa_eliminar(request, pk):
    tarifa = get_object_or_404(Tarifa, pk=pk)
    try:
        with transaction.atomic():
            tarifa.delete()
    except ProtectedError:
        messages.error(request, f'«{tarifa.nombre}» ya se usó en presupuestos y no se puede eliminar.')
    else:
        messages.success(request, 'Tarifa eliminada')
    return redirect('tarifas')


@solo_admin
@require_POST
def servicio_precio(request, pk):
    servicio = get_object_or_404(Servicio, pk=pk)
    form = PrecioForm(request.POST)
    if form.is_valid():
        servicio.precio_base = form.cleaned_data['precio']
        servicio.save(update_fields=['precio_base'])
        messages.success(request, f'Precio de «{servicio.nombre}» actualizado')
    else:
        messages.error(request, _primer_error(form))
    return redirect('tarifas')


@solo_admin
@require_POST
def servicio_eliminar(request, pk):
    servicio = get_object_or_404(Servicio, pk=pk)
    archivo = servicio.foto.name
    try:
        with transaction.atomic():
            servicio.delete()
    except ProtectedError:
        messages.error(request, f'«{servicio.nombre}» ya tiene órdenes o presupuestos registrados y no se puede eliminar.')
    else:
        if archivo:
            servicio.foto.storage.delete(archivo)  # no deja fotos huérfanas en el disco
        messages.success(request, 'Servicio eliminado')
    return redirect('tarifas')


@solo_admin
@require_POST
def servicio_foto(request, pk):
    """Sube o cambia la foto de un servicio."""
    servicio = get_object_or_404(Servicio, pk=pk)
    form = FotoForm(request.POST, request.FILES)
    if form.is_valid():
        anterior = servicio.foto.name
        servicio.foto = form.cleaned_data['foto']
        servicio.save(update_fields=['foto'])
        if anterior:
            servicio.foto.storage.delete(anterior)
        messages.success(request, f'Foto de «{servicio.nombre}» actualizada')
    else:
        messages.error(request, _primer_error(form))
    return redirect('tarifas')


@solo_admin
@require_POST
def servicio_foto_quitar(request, pk):
    """Quita la foto propia; el servicio vuelve a mostrar la de ejemplo o un ícono."""
    servicio = get_object_or_404(Servicio, pk=pk)
    if servicio.foto:
        servicio.foto.delete(save=True)
        messages.success(request, f'Foto de «{servicio.nombre}» quitada')
    return redirect('tarifas')


# ---------------------------------------------------------------- órdenes

POR_PAGINA = 10


@solo_admin
def ordenes(request):
    """Todas las órdenes, con búsqueda y filtro por estado."""
    consulta = Orden.objects.select_related('cliente', 'tecnico', 'servicio')
    estado = request.GET.get('estado', '')
    if estado in (Orden.Estado.CANCELADA, Orden.Estado.RECHAZADA):
        consulta = consulta.filter(Q(estado=estado) | Q(resultado=estado))  # siguen figurando así aunque ya se haya entregado el equipo
    elif estado in Orden.Estado.values:
        consulta = consulta.filter(estado=estado, resultado='')
    else:
        estado = ''
    texto = request.GET.get('q', '').strip()
    if texto:
        filtro = Q(modelo__icontains=texto) | Q(cliente__nombre__icontains=texto) | Q(cliente__email__icontains=texto)
        if texto.lstrip('#').isdigit():
            filtro |= Q(pk=int(texto.lstrip('#')))
        consulta = consulta.filter(filtro)
    pagina = Paginator(consulta, POR_PAGINA).get_page(request.GET.get('pagina'))
    return render(request, 'panel/ordenes.html', {
        'tab': 'ord', 'pagina': pagina, 'q': texto, 'estado': estado,
        'estados': Orden.Estado.choices,
        'n_canceladas': Orden.objects.filter(Q(estado=Orden.Estado.CANCELADA) | Q(resultado=Orden.Estado.CANCELADA)).count(),
        'n_rechazadas': Orden.objects.filter(Q(estado=Orden.Estado.RECHAZADA) | Q(resultado=Orden.Estado.RECHAZADA)).count(),
        'sin_tecnico': Orden.objects.filter(tecnico__isnull=True).exclude(estado__in=Orden.CERRADAS).count(),
        'pagos_por_validar': Pago.objects.filter(estado=Pago.Estado.PENDIENTE).count(),
    })


@solo_admin
@amable
def orden_admin(request, pk):
    """Detalle de una orden: el administrador asigna técnico, estado, prioridad y fecha."""
    orden = get_object_or_404(Orden.objects.select_related('cliente', 'tecnico', 'servicio').prefetch_related('fotos'), pk=pk)
    bloqueada = orden.estado == Orden.Estado.ENTREGADO
    if orden.congelada:
        # Ya no se edita por el formulario: solo se reabre, se termina sin reparar o se deja como está
        bloqueada = True
    if request.method == 'POST':
        if bloqueada:
            messages.error(request, 'Esta orden ya fue entregada y no se puede modificar.')
            return redirect('orden_admin', pk=pk)
        form = OrdenGestionForm(request.POST, orden=orden)
        if form.is_valid():
            form.guardar(request.user)
            messages.success(request, 'Orden actualizada')
            return redirect('orden_admin', pk=pk)
    else:
        form = OrdenGestionForm(orden=orden)
    return render(request, 'panel/orden_detalle.html', {
        'tab': 'ord', 'orden': orden, 'form': form, 'bloqueada': bloqueada,
        'cambios_fecha': orden.cambios_fecha.select_related('usuario'),
        'mano_de_obra': _mano_de_obra(orden),
        'diagnostico': presupuestos.cargo_diagnostico(orden),
        'items_cierre': PresupuestoItem.objects.filter(presupuesto__orden=orden, presupuesto__estado='ACEPTADO', tipo='REPUESTO').select_related('producto'),
        'usos_cobrables': orden.usos_repuesto.select_related('producto').filter(precio_unit__gt=0, pieza__isnull=True),
        'puede_descuento': not orden.congelada and orden.estado not in (Orden.Estado.LISTO, Orden.Estado.ENTREGADO),
        'puede_rechazar': orden.estado in (Orden.Estado.RECIBIDO, Orden.Estado.DIAGNOSTICO, Orden.Estado.REPARACION),
        'notas': orden.notas.select_related('autor'),
        'fotos_avance': orden.fotos_avance.all(),
        'contacto_tecnico': orden.tecnico if orden.tecnico and orden.tecnico.is_active else None,
        'historial': orden.historial.select_related('usuario'),
        'solicitudes': orden.solicitudes_estado.select_related('tecnico'),
        'carga': [(u, carga.detalle(u)) for u in carga.tecnicos_por_carga()],
        'recomendado': form.recomendado if hasattr(form, 'recomendado') else None,
        **views_presupuesto.contexto(orden, request.user),
        **views_pagos.contexto(orden, request.user),
    })


@solo_admin
@require_POST
def solicitud_responder(request, pk, solicitud_pk):
    """El administrador aprueba (la orden vuelve a ese estado) o rechaza la solicitud de retroceso del técnico."""
    with transaction.atomic():
        orden = get_object_or_404(Orden.objects.select_for_update(), pk=pk)
        solicitud = get_object_or_404(SolicitudEstado.objects.select_for_update(), pk=solicitud_pk, orden=orden)
        if solicitud.estado != SolicitudEstado.Estado.PENDIENTE:
            messages.error(request, 'Esta solicitud ya fue respondida.')
            return redirect('orden_admin', pk=pk)
        accion = request.POST.get('accion')
        if accion not in ('aprobar', 'rechazar'):
            messages.error(request, 'Elige aprobar o rechazar.')
            return redirect('orden_admin', pk=pk)
        if accion == 'aprobar':
            if orden.estado == Orden.Estado.ENTREGADO:
                messages.error(request, 'La orden ya fue entregada.')
                return redirect('orden_admin', pk=pk)
            orden.registrar(f'Estado: {orden.get_estado_display()} → {solicitud.get_estado_pedido_display()} (aprobado a pedido del técnico)', request.user)
            orden.estado = solicitud.estado_pedido
            orden.save(update_fields=['estado'])
            solicitud.estado = SolicitudEstado.Estado.APROBADA
        else:
            solicitud.estado = SolicitudEstado.Estado.RECHAZADA
            solicitud.respuesta = ' '.join(request.POST.get('respuesta', '').split())[:300]
            orden.registrar(f'Se rechazó volver a {solicitud.get_estado_pedido_display()}', request.user)
        solicitud.respondida_en = timezone.now()
        solicitud.save(update_fields=['estado', 'respuesta', 'respondida_en'])
        avisos.retroceso_respondido(solicitud)
    messages.success(request, 'Solicitud aprobada: la orden cambió de estado' if accion == 'aprobar' else 'Solicitud rechazada')
    return redirect('orden_admin', pk=pk)


def _rechazado_reciente(orden):
    """Lo último que el cliente rechazó (presupuesto o pieza): lo que cerró la orden. None si la cerró el administrador."""
    p = orden.presupuestos.filter(estado='RECHAZADO').order_by('-respondido_en').first()
    z = orden.piezas_solicitadas.filter(estado='RECHAZADA').order_by('-respondida_en').first()
    if p is None or z is None:
        return p or z
    return p if (p.respondido_en or p.creado_en) >= (z.respondida_en or z.creada_en) else z


def _mano_de_obra(orden):
    """Subtotal (sin IVA) de la mano de obra: la de los presupuestos aceptados; si el cliente no aceptó ninguno,
    la del último presupuesto que rechazó (es lo que se le puede perdonar al reabrir)."""
    items = PresupuestoItem.objects.filter(presupuesto__orden=orden, presupuesto__estado='ACEPTADO', tipo='SERVICIO')
    if not items.exists():
        ultimo = orden.presupuestos.order_by('-creado_en').first()
        items = PresupuestoItem.objects.filter(presupuesto=ultimo, tipo='SERVICIO') if ultimo else PresupuestoItem.objects.none()
    return sum((i.subtotal for i in items), Decimal('0'))


def _base_descuento(orden):
    """Hasta cuánto se puede descontar (sin IVA): lo aceptado y, si no hay nada aceptado, el último presupuesto o pieza rechazada."""
    bruto = sum((l['subtotal'] for l in orden.factura['lineas'] if l['tipo'] != 'Descuento'), Decimal('0'))
    if bruto > 0:
        return bruto
    ultimo = orden.presupuestos.order_by('-creado_en').first()
    base = ultimo.subtotal if ultimo else Decimal('0')
    for z in orden.piezas_solicitadas.filter(estado='RECHAZADA'):
        base += z.subtotal
    return base


def _texto_descuento(monto, sin_mano):
    partes = []
    if sin_mano:
        partes.append('sin cobrar la mano de obra')
    if monto > 0:
        partes.append(f'${monto}')
    return ' y '.join(partes) or '$0'


def _leer_descuento(request, orden):
    """Descuento del formulario: un monto en dólares (sin IVA) y/o «no cobrar la mano de obra». Devuelve (monto, sin_mano_obra, motivo) o lanza ValueError.

    Se puede dejar listo antes de que el técnico mande su presupuesto: se aplica a cada presupuesto que llegue."""
    sin_mano = request.POST.get('sin_mano_obra') == 'on'
    texto = request.POST.get('descuento', '').strip().replace(',', '.')
    try:
        monto = Decimal(texto) if texto else Decimal('0')
    except InvalidOperation:
        raise ValueError('El descuento no es un número válido.')
    monto = monto.quantize(Decimal('0.01'))
    if monto < 0:
        raise ValueError('El descuento no puede ser negativo.')
    bruto = _base_descuento(orden)
    if bruto > 0:
        total = monto + (_mano_de_obra(orden) if sin_mano else Decimal('0'))
        if total > bruto:
            raise ValueError(f'El descuento no puede pasar de ${bruto} (el valor presupuestado sin IVA).')
    elif monto > 5000:
        raise ValueError('El descuento es demasiado grande.')
    motivo = ' '.join(request.POST.get('motivo', '').split())[:200]
    if (monto > 0 or sin_mano) and len(motivo) < 5:
        raise ValueError('Escribe el motivo del descuento (mínimo 5 letras): el cliente lo verá en su presupuesto y en la factura.')
    return monto, sin_mano, motivo


@solo_admin
@require_POST
def orden_descuento(request, pk):
    """Aplica (o quita, con $0) un descuento a una orden en curso: el cliente lo ve en el presupuesto y en la factura."""
    with transaction.atomic():
        orden = get_object_or_404(Orden.objects.select_for_update(of=('self',)).select_related('cliente', 'tecnico'), pk=pk)
        if orden.congelada or orden.estado in (Orden.Estado.LISTO, Orden.Estado.ENTREGADO):
            messages.error(request, 'Solo se puede dar un descuento mientras la orden está en proceso. Si está cancelada o rechazada, reábrela con el descuento.')
            return redirect('orden_admin', pk=pk)
        try:
            descuento, sin_mano, motivo = _leer_descuento(request, orden)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('orden_admin', pk=pk)
        orden.descuento, orden.descuento_sin_mano = descuento, sin_mano
        orden.descuento_motivo = motivo if orden.tiene_descuento else ''
        orden.save(update_fields=['descuento', 'descuento_sin_mano', 'descuento_motivo'])
        detalle = _texto_descuento(descuento, sin_mano)
        orden.registrar(f'Descuento acordado: {detalle}' + (f' ({motivo})' if orden.tiene_descuento else ' (quitado)'), request.user)
        if orden.tiene_descuento:
            avisos.descuento_aplicado(orden, detalle)
    if orden.tiene_descuento:
        messages.success(request, 'Descuento guardado. El cliente lo verá en el presupuesto (y en cada presupuesto que el técnico envíe después).')
    else:
        messages.success(request, 'Descuento quitado.')
    return redirect('orden_admin', pk=pk)


@solo_admin
@require_POST
def orden_rechazar(request, pk):
    """El administrador rechaza la orden (por ejemplo, el equipo no tiene arreglo) y decide qué se le cobra: queda congelada para el técnico."""
    get_object_or_404(Orden, pk=pk)
    try:
        presupuestos.rechazar_orden(pk, request.user, request.POST.get('motivo', ''), request.POST.get('cobro', 'nada'), request.POST.get('cargo', ''))
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, 'Orden rechazada. El técnico ya no puede hacer cambios.')
    return redirect('orden_admin', pk=pk)


@solo_admin
@require_POST
def orden_reabrir(request, pk):
    """Reabre una orden cancelada o rechazada, con un descuento si se acordó uno con el cliente."""
    with transaction.atomic():
        orden = get_object_or_404(Orden.objects.select_for_update(of=('self',)).select_related('cliente', 'tecnico'), pk=pk)
        if not orden.congelada:
            messages.error(request, 'Esta orden no está cancelada ni rechazada.')
            return redirect('orden_admin', pk=pk)
        try:
            descuento, sin_mano, motivo = _leer_descuento(request, orden)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('orden_admin', pk=pk)
        detalle = _texto_descuento(descuento, sin_mano)
        hay = descuento > 0 or sin_mano
        previo = orden.estado_previo if orden.estado_previo in Orden.ORDEN_ESTADOS and orden.estado_previo != Orden.Estado.ENTREGADO else Orden.Estado.DIAGNOSTICO
        orden.registrar(f'Orden reabierta: {orden.get_estado_display()} → {dict(Orden.Estado.choices)[previo]}' + (f' (descuento {detalle}: {motivo})' if hay else ''), request.user)
        rechazado = _rechazado_reciente(orden) if orden.estado == Orden.Estado.CANCELADA else None
        if rechazado is not None:
            # Lo que el cliente rechazó vuelve a quedar pendiente: ya no figura como rechazado y puede responder otra vez (con el descuento).
            if hasattr(rechazado, 'respondido_en'):
                rechazado.estado, rechazado.respondido_en = 'PENDIENTE', None
                rechazado.save(update_fields=['estado', 'respondido_en'])
            else:
                rechazado.estado, rechazado.respondida_en = 'COTIZADA', None
                rechazado.save(update_fields=['estado', 'respondida_en'])
                orden.espera_repuesto = True
                orden.save(update_fields=['espera_repuesto'])
        orden.estado, orden.estado_previo, orden.motivo_cierre, orden.resultado = previo, '', '', ''
        orden.descuento, orden.descuento_sin_mano, orden.descuento_motivo = descuento, sin_mano, motivo if hay else ''
        orden.cargo_cancelacion = Decimal('0')
        orden.save(update_fields=['estado', 'estado_previo', 'motivo_cierre', 'resultado', 'descuento', 'descuento_sin_mano', 'descuento_motivo', 'cargo_cancelacion'])
        avisos.orden_reabierta(orden, detalle if hay else '')
    messages.success(request, 'Orden reabierta: el técnico puede continuar.')
    return redirect('orden_admin', pk=pk)


@solo_admin
@require_POST
def orden_terminar(request, pk):
    """Cierra el caso sin reparar: queda «Listo» para que el cliente pague lo aceptado (o retire sin cobro) y se entregue."""
    with transaction.atomic():
        orden = get_object_or_404(Orden.objects.select_for_update(of=('self',)).select_related('cliente', 'tecnico'), pk=pk)
        if not orden.congelada:
            messages.error(request, 'Esta orden no está cancelada ni rechazada.')
            return redirect('orden_admin', pk=pk)
        opcion = request.POST.get('cobro') or ('otro' if request.POST.get('cargo', '').strip() else 'mantener')
        if opcion != 'mantener':
            # El diagnóstico o la mano de obra se calculan según en qué estado iba la orden antes de cerrarse
            ref = orden.estado_previo or Orden.Estado.DIAGNOSTICO
            actual = orden.estado
            orden.estado = ref if ref in (Orden.Estado.RECIBIDO, Orden.Estado.DIAGNOSTICO, Orden.Estado.REPARACION) else Orden.Estado.DIAGNOSTICO
            try:
                cargo = presupuestos.leer_cobro(orden, opcion, request.POST.get('cargo', ''))
            except ValueError as e:
                orden.estado = actual
                messages.error(request, str(e))
                return redirect('orden_admin', pk=pk)
            orden.estado = actual
            orden.cargo_cancelacion = cargo
        presupuestos.devolver_no_usados(orden, request.user)
        if request.POST.get('usos_listados'):
            marcados = set(request.POST.getlist('cobrar_uso'))
            for u in orden.usos_repuesto.select_related('producto').filter(precio_unit__gt=0, pieza__isnull=True):
                if str(u.pk) not in marcados:
                    u.precio_unit = Decimal('0')
                    u.save(update_fields=['precio_unit'])
                    orden.registrar(f'No se cobra el repuesto usado: {u.cantidad} × {u.producto.nombre}', request.user)
        orden.registrar(f'Cobro del cierre: {"nada" if orden.cargo_cancelacion == 0 else "$" + str(orden.cargo_cancelacion) + " + IVA"}', request.user)
        orden.registrar('Caso cerrado sin reparar: queda listo para pagar lo aceptado y retirar el equipo', request.user)
        orden.estado, orden.estado_previo = Orden.Estado.LISTO, ''
        orden.save(update_fields=['estado', 'estado_previo', 'cargo_cancelacion'])
        avisos.orden_terminada_sin_reparar(orden)
    messages.success(request, 'Listo: el cliente puede pagar lo aceptado y retirar su equipo.')
    return redirect('orden_admin', pk=pk)
