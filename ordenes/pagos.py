"""Pagos: el cliente paga cuando el equipo está listo; el administrador valida y autoriza la entrega."""

from django.db import transaction
from django.utils import timezone

from comunicacion import avisos

from .models import Orden, Pago


class PagoError(Exception):
    """Algo impide la operación; el mensaje se muestra tal cual."""


def _orden_lista(orden_pk, **filtro):
    orden = Orden.objects.select_for_update(of=('self',)).select_related('cliente', 'tecnico').get(pk=orden_pk, **filtro)
    if orden.estado != Orden.Estado.LISTO:
        raise PagoError('El pago se registra cuando el equipo está «Listo».')
    return orden


def _cuenta(orden):
    """Total a pagar y verificación de que no hay otro pago en curso."""
    total = orden.total_a_pagar
    if total <= 0:
        raise PagoError('Esta orden no tiene nada que cobrar.')
    if orden.pagos.filter(estado=Pago.Estado.CONFIRMADO).exists():
        raise PagoError('Esta orden ya está pagada.')
    return total


@transaction.atomic
def enviar_transferencia(orden_pk, cliente, comprobante, referencia):
    """El cliente sube el comprobante de su transferencia; queda pendiente hasta que el administrador lo valide."""
    orden = _orden_lista(orden_pk, cliente=cliente)
    total = _cuenta(orden)
    if orden.pagos.filter(estado=Pago.Estado.PENDIENTE).exists():
        raise PagoError('Ya enviaste un comprobante. Espera a que el administrador lo revise.')
    pago = Pago.objects.create(orden=orden, metodo=Pago.Metodo.TRANSFERENCIA, monto=total, comprobante=comprobante, referencia=referencia)
    orden.registrar(f'El cliente envió el comprobante de una transferencia por ${total}', cliente)
    avisos.pago_enviado(pago)
    return pago


@transaction.atomic
def avisar_efectivo(orden_pk, cliente):
    """El cliente elige pagar en efectivo: debe acercarse al taller; el administrador lo confirma al recibir el dinero."""
    orden = _orden_lista(orden_pk, cliente=cliente)
    total = _cuenta(orden)
    if orden.pagos.filter(estado=Pago.Estado.PENDIENTE).exists():
        raise PagoError('Ya elegiste una forma de pago. Espera a que el administrador la confirme.')
    pago = Pago.objects.create(orden=orden, metodo=Pago.Metodo.EFECTIVO, monto=total)
    orden.registrar(f'El cliente pagará ${total} en efectivo en el taller', cliente)
    avisos.pago_efectivo_elegido(pago)
    return pago


@transaction.atomic
def registrar_efectivo(orden_pk, admin):
    """El administrador recibió el dinero en el taller: queda confirmado y se autoriza la entrega."""
    orden = _orden_lista(orden_pk)
    total = _cuenta(orden)
    if orden.pagos.filter(estado=Pago.Estado.PENDIENTE).exists():
        raise PagoError('Hay un pago por validar. Confírmalo o recházalo primero.')
    ahora = timezone.now()
    pago = Pago.objects.create(
        orden=orden, metodo=Pago.Metodo.EFECTIVO, monto=total, estado=Pago.Estado.CONFIRMADO,
        validado_por=admin, validado_en=ahora,
    )
    orden.entrega_autorizada = True
    orden.save(update_fields=['entrega_autorizada'])
    orden.registrar(f'Pago en efectivo recibido: ${total}. Entrega autorizada', admin)
    avisos.pago_confirmado(pago)
    return pago


@transaction.atomic
def confirmar(pago_pk, admin):
    pago = Pago.objects.select_for_update(of=('self',)).select_related('orden', 'orden__cliente', 'orden__tecnico').get(pk=pago_pk)
    if pago.estado != Pago.Estado.PENDIENTE:
        raise PagoError('Este pago ya fue revisado.')
    orden = Orden.objects.select_for_update(of=('self',)).get(pk=pago.orden_id)
    if pago.monto != orden.total_a_pagar:
        raise PagoError(f'El total de la orden cambió (ahora es ${orden.total_a_pagar}) y el comprobante es por ${pago.monto}. Recházalo y pide uno nuevo.')
    pago.estado, pago.validado_por, pago.validado_en = Pago.Estado.CONFIRMADO, admin, timezone.now()
    pago.save(update_fields=['estado', 'validado_por', 'validado_en'])
    orden.entrega_autorizada = True
    orden.save(update_fields=['entrega_autorizada'])
    orden.registrar(f'Pago {pago.get_metodo_display().lower()} confirmado: ${pago.monto}. Entrega autorizada', admin)
    pago.orden = orden
    avisos.pago_confirmado(pago)
    return pago


@transaction.atomic
def rechazar(pago_pk, admin, motivo):
    motivo = ' '.join((motivo or '').split())
    if len(motivo) < 5:
        raise PagoError('Explica por qué se rechaza el comprobante (por ejemplo: el monto no coincide).')
    pago = Pago.objects.select_for_update(of=('self',)).select_related('orden', 'orden__cliente').get(pk=pago_pk)
    if pago.estado != Pago.Estado.PENDIENTE:
        raise PagoError('Este pago ya fue revisado.')
    pago.estado, pago.motivo_rechazo = Pago.Estado.RECHAZADO, motivo[:300]
    pago.validado_por, pago.validado_en = admin, timezone.now()
    pago.save(update_fields=['estado', 'motivo_rechazo', 'validado_por', 'validado_en'])
    pago.orden.registrar(f'Comprobante rechazado: {pago.motivo_rechazo}', admin)
    avisos.pago_rechazado(pago)
    return pago


@transaction.atomic
def autorizar_sin_cobro(orden_pk, admin):
    """Para una orden sin nada que cobrar (por ejemplo, el equipo no tuvo arreglo y no se cobra)."""
    orden = _orden_lista(orden_pk)
    if orden.total_a_pagar > 0:
        raise PagoError('Esta orden tiene un total a pagar. Registra el pago.')
    orden.entrega_autorizada = True
    orden.save(update_fields=['entrega_autorizada'])
    orden.registrar('Entrega autorizada sin cobro', admin)
    return orden


@transaction.atomic
def entregar(orden_pk, admin):
    orden = Orden.objects.select_for_update(of=('self',)).select_related('cliente', 'tecnico').get(pk=orden_pk)
    if orden.estado != Orden.Estado.LISTO:
        raise PagoError('Solo se entrega un equipo que está «Listo».')
    if not orden.entrega_autorizada:
        raise PagoError('No se puede entregar: falta el pago confirmado.')
    orden.estado, orden.entregada_en = Orden.Estado.ENTREGADO, timezone.now()
    orden.save(update_fields=['estado', 'entregada_en'])
    orden.registrar('Equipo entregado al cliente', admin)
    avisos.entregada(orden)
    return orden
