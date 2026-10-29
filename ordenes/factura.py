"""Factura general de una orden: todo lo que el cliente aceptó (servicios, repuestos y piezas) en una sola cuenta."""

from decimal import ROUND_HALF_UP, Decimal

CENTAVO = Decimal('0.01')


def calcular(orden):
    """Líneas, subtotal, IVA y total de lo que el cliente debe pagar.

    Suma los presupuestos aceptados, las piezas aceptadas y los repuestos adicionales que el técnico usó
    (a su precio de venta). Los repuestos de un presupuesto o de una pieza no se cuentan dos veces."""
    from .models import Presupuesto

    lineas, subtotal, iva = [], Decimal('0'), Decimal('0')
    cerrada = bool(orden.cierre_visible)
    for p in orden.presupuestos.filter(estado=Presupuesto.Estado.ACEPTADO).prefetch_related('items__tarifa', 'items__producto'):
        parcial = Decimal('0')
        for i in p.items.all():
            if cerrada and i.tipo != 'SERVICIO' and not i.en_uso_en:
                continue  # orden cancelada o rechazada: el repuesto que no se usó no se cobra
            lineas.append({
                'detalle': i.tarifa.nombre if i.tipo == 'SERVICIO' else i.producto.nombre,
                'tipo': 'Servicio' if i.tipo == 'SERVICIO' else 'Repuesto',
                'cantidad': i.cantidad, 'precio_unit': i.precio_unit, 'subtotal': i.subtotal,
            })
            parcial += i.subtotal
        if cerrada:
            subtotal += parcial
            iva += (parcial * p.iva_porcentaje / 100).quantize(CENTAVO, rounding=ROUND_HALF_UP)
        else:
            subtotal += p.subtotal
            iva += p.iva
    mano = sum((l['subtotal'] for l in lineas if l['tipo'] == 'Servicio'), Decimal('0')) if orden.descuento_sin_mano else Decimal('0')
    for z in orden.piezas_solicitadas.filter(estado__in=['ACEPTADA', 'PEDIDA', 'DISPONIBLE']):
        if z.precio is None:
            continue
        lineas.append({'detalle': z.nombre_pieza, 'tipo': 'Pieza', 'cantidad': z.cantidad, 'precio_unit': z.precio, 'subtotal': z.subtotal})
        subtotal += z.subtotal
        iva += z.iva
    extra = Decimal('0')
    for u in orden.usos_repuesto.select_related('producto').filter(pieza__isnull=True, precio_unit__gt=0):
        sub = u.precio_unit * u.cantidad
        lineas.append({'detalle': u.producto.nombre, 'tipo': 'Repuesto adicional', 'cantidad': u.cantidad, 'precio_unit': u.precio_unit, 'subtotal': sub})
        extra += sub
    if extra:
        subtotal += extra
        iva += (extra * orden.iva_efectivo / 100).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    cargo = orden.cargo_cancelacion or Decimal('0')
    if cargo > 0:
        lineas.append({'detalle': 'Mano de obra por cancelación', 'tipo': 'Servicio', 'cantidad': 1, 'precio_unit': cargo, 'subtotal': cargo})
        subtotal += cargo
        iva += (cargo * orden.iva_efectivo / 100).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    desc = min((orden.descuento or Decimal('0')) + mano, subtotal)
    if desc > 0:
        iva = (iva * (subtotal - desc) / subtotal).quantize(CENTAVO, rounding=ROUND_HALF_UP)  # el IVA baja en proporción
        lineas.append({
            'detalle': f'Descuento acordado{" · " + orden.descuento_motivo if orden.descuento_motivo else ""}',
            'tipo': 'Descuento', 'cantidad': 1, 'precio_unit': -desc, 'subtotal': -desc,
        })
        subtotal -= desc
    return {'lineas': lineas, 'subtotal': subtotal, 'iva': iva, 'total': subtotal + iva, 'descuento': desc}
