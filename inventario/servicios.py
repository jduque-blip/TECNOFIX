"""Operaciones que cambian el stock. Todas bloquean la fila del producto para que dos personas
no la modifiquen a la vez, y dejan un movimiento (kardex) con el stock resultante."""

from django.db import transaction
from django.db.models.deletion import ProtectedError

from comunicacion import avisos

from django.utils import timezone

from .models import HistorialPrecio, MovimientoInventario, Pedido, Producto, ProductoProveedor

MAX_UNIDADES = 100000


class StockError(Exception):
    """Operación de stock no permitida (se muestra tal cual al administrador)."""


def _movimiento(producto, tipo, cantidad, motivo, usuario, pedido=None, presupuesto=None):
    return MovimientoInventario.objects.create(
        producto=producto, tipo=tipo, cantidad=cantidad, stock_resultante=producto.stock,
        motivo=motivo, usuario=usuario, pedido=pedido, presupuesto=presupuesto,
    )


def _avisar_si_bajo(producto, stock_antes):
    """Avisa a los administradores una sola vez: cuando el stock cruza hacia el mínimo (o se agota), no en cada cambio."""
    if producto.activo and stock_antes > producto.stock_minimo and producto.stock <= producto.stock_minimo:
        avisos.stock_bajo(producto)


@transaction.atomic
def crear_producto(form, usuario):
    """Guarda el producto nuevo con sus proveedores y, si hay stock inicial, su primera entrada."""
    producto = form.save(commit=False)
    producto.stock = 0
    producto.costo_pedido = form.costo_final()
    producto.save()
    _guardar_cotizaciones(producto, form.cotizaciones())
    inicial = form.cleaned_data.get('stock_inicial') or 0
    if inicial:
        producto.stock = inicial
        producto.save(update_fields=['stock'])
        _movimiento(producto, MovimientoInventario.Tipo.ENTRADA, inicial, 'Stock inicial', usuario)
    return producto


@transaction.atomic
def actualizar_producto(form):
    producto = form.save(commit=False)
    producto.costo_pedido = form.costo_final()
    producto.save()
    _guardar_cotizaciones(producto, form.cotizaciones())
    return producto


def _registrar_precio(producto, proveedor, precio, origen):
    """Guarda en el historial el precio de un proveedor, solo si cambió respecto al último conocido."""
    ultimo = HistorialPrecio.objects.filter(producto=producto, proveedor=proveedor).first()
    if ultimo is None or ultimo.precio != precio:
        HistorialPrecio.objects.create(producto=producto, proveedor=proveedor, precio=precio, origen=origen)


def _guardar_cotizaciones(producto, cotizaciones):
    """Deja al producto con exactamente estos proveedores. Los que ya estaban conservan su fecha si el precio no cambió."""
    nuevos = {proveedor.pk: (proveedor, precio) for proveedor, precio in cotizaciones}
    actuales = {c.proveedor_id: c for c in producto.cotizaciones.all()}
    for proveedor_id, cotizacion in actuales.items():
        if proveedor_id not in nuevos:
            cotizacion.delete()  # primero se quitan los que sobran: un producto admite máximo 2
    for proveedor_id, (proveedor, precio) in nuevos.items():
        cotizacion = actuales.get(proveedor_id)
        if cotizacion is None:
            ProductoProveedor.objects.create(producto=producto, proveedor=proveedor, precio=precio)
        elif cotizacion.precio != precio:
            cotizacion.precio = precio
            cotizacion.actualizado_en = timezone.now()
            cotizacion.save()
        _registrar_precio(producto, proveedor, precio, HistorialPrecio.Origen.COTIZACION)


@transaction.atomic
def reponer(producto_pk, proveedor, cantidad, precio_unit, observacion, usuario):
    """Pedido a un proveedor: sube el stock y el costo de pedido pasa a ser el último pagado."""
    producto = Producto.objects.select_for_update().get(pk=producto_pk)
    if producto.stock + cantidad > MAX_UNIDADES * 10:
        raise StockError('Esa cantidad deja el stock demasiado alto.')
    costo_anterior = producto.costo_pedido
    pedido = Pedido.objects.create(
        producto=producto, proveedor=proveedor, cantidad=cantidad, precio_unit=precio_unit,
        observacion=observacion, usuario=usuario,
    )
    producto.stock += cantidad
    producto.costo_pedido = precio_unit
    producto.save(update_fields=['stock', 'costo_pedido'])
    _movimiento(
        producto, MovimientoInventario.Tipo.ENTRADA, cantidad,
        f'Reposición a {proveedor.nombre} ({cantidad} × ${precio_unit:.2f})', usuario, pedido,
    )
    # Si el proveedor cobró distinto a lo que teníamos anotado, su precio queda actualizado
    cotizacion = ProductoProveedor.objects.filter(producto=producto, proveedor=proveedor).first()
    if cotizacion and cotizacion.precio != precio_unit:
        cotizacion.precio = precio_unit
        cotizacion.actualizado_en = timezone.now()
        cotizacion.save()
    _registrar_precio(producto, proveedor, precio_unit, HistorialPrecio.Origen.PEDIDO)
    from ordenes.presupuestos import atender_agotadas
    atender_agotadas(producto, usuario)  # avisa a los técnicos que esperaban esta pieza agotada
    from finanzas.models import Gasto
    Gasto.objects.create(
        tipo=Gasto.Tipo.GASTO, concepto=f'Reposición: {cantidad} × {producto.nombre} ({proveedor.nombre})'[:150],
        monto=pedido.cantidad * precio_unit, pedido=pedido, usuario=usuario, automatico=True,
    )
    return pedido, costo_anterior


@transaction.atomic
def usar_sugerido(producto_pk):
    """Pone como costo de venta el precio sugerido (costo de pedido + margen)."""
    producto = Producto.objects.select_for_update().get(pk=producto_pk)
    sugerido = producto.precio_sugerido
    if sugerido is None:
        raise StockError('Este producto no tiene costo de pedido, no hay precio que sugerir.')
    producto.precio_venta = sugerido
    producto.save(update_fields=['precio_venta'])
    return producto


@transaction.atomic
def ajustar(producto_pk, stock_real, motivo, usuario):
    """Corrige el stock a lo que realmente hay en estantería (conteo físico, daño, pérdida)."""
    producto = Producto.objects.select_for_update().get(pk=producto_pk)
    diferencia = stock_real - producto.stock
    if diferencia == 0:
        raise StockError('El stock real es igual al que ya tiene el sistema.')
    antes = producto.stock
    producto.stock = stock_real
    producto.save(update_fields=['stock'])
    _movimiento(producto, MovimientoInventario.Tipo.AJUSTE, diferencia, motivo, usuario)
    if diferencia < 0 and producto.costo_pedido:
        from finanzas.models import Gasto
        Gasto.objects.create(
            tipo=Gasto.Tipo.PERDIDA, concepto=f'Faltante de inventario: {-diferencia} × {producto.nombre}'[:150],
            monto=-diferencia * producto.costo_pedido, usuario=usuario, automatico=True,
        )
    _avisar_si_bajo(producto, antes)
    return producto


@transaction.atomic
def descontar(producto_pk, cantidad, motivo, usuario, presupuesto=None, avisar=True):
    """Salida de stock (por ejemplo, repuestos usados en una reparación). No permite dejar el stock en negativo."""
    producto = Producto.objects.select_for_update().get(pk=producto_pk)
    if cantidad < 1:
        raise StockError('La cantidad debe ser al menos 1.')
    if producto.stock < cantidad:
        raise StockError(f'Solo hay {producto.stock} unidad{"es" if producto.stock != 1 else ""} de {producto.nombre}.')
    antes = producto.stock
    producto.stock -= cantidad
    producto.save(update_fields=['stock'])
    _movimiento(producto, MovimientoInventario.Tipo.SALIDA, cantidad, motivo, usuario, presupuesto=presupuesto)
    if avisar:
        _avisar_si_bajo(producto, antes)
    return producto


def por_que_no_se_elimina(producto):
    """None si el producto se puede borrar (se creó por error); si no, la razón."""
    if producto.pedido_set.exists():
        return 'Ya tiene pedidos a proveedores registrados.'
    if producto.movimientos.exclude(tipo=MovimientoInventario.Tipo.ENTRADA, motivo='Stock inicial').exists():
        return 'Ya tiene movimientos de stock (entradas, salidas o ajustes).'
    return None


@transaction.atomic
def eliminar_producto(producto_pk):
    """Borra un producto creado por error. Solo si no tiene historial más allá de su stock inicial."""
    producto = Producto.objects.select_for_update().get(pk=producto_pk)
    razon = por_que_no_se_elimina(producto)
    if razon:
        raise StockError(f'No se puede eliminar: {razon} Desactívalo en su lugar.')
    try:
        producto.movimientos.all().delete()
        producto.delete()
    except ProtectedError:
        raise StockError('No se puede eliminar: ya se usó en un presupuesto. Desactívalo en su lugar.')


@transaction.atomic
def entrada_de_pieza(producto_pk, cantidad, proveedor, costo, motivo, usuario):
    """Entra al inventario una pieza que llegó. Con proveedor y costo queda como pedido (historial de precios)."""
    if proveedor is not None and costo is not None:
        pedido, _ = reponer(producto_pk, proveedor, cantidad, costo, motivo, usuario)
        return pedido
    producto = Producto.objects.select_for_update().get(pk=producto_pk)
    producto.stock += cantidad
    producto.save(update_fields=['stock'])
    _movimiento(producto, MovimientoInventario.Tipo.ENTRADA, cantidad, motivo, usuario)
    return None
