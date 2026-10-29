"""Presupuestos: el técnico los arma, el cliente los acepta o rechaza y, al aceptar, salen los repuestos del inventario."""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from comunicacion import avisos
from core.models import Configuracion
from inventario import precios
from inventario import servicios as stock
from inventario.models import PiezaSolicitada, Producto, UsoRepuesto

from . import fechas
from .models import Orden, Presupuesto, PresupuestoItem, RepuestoDanado, Tarifa

MAX_CANTIDAD = 100
CENTAVO = Decimal('0.01')


class PresupuestoError(Exception):
    """Algo impide la operación; el mensaje se muestra tal cual."""


DIAS_INSTALACION = 1  # un día para instalar la pieza y probar el equipo


class SinStock(PresupuestoError):
    """Al aceptar, ya no alcanzaba el stock de un repuesto."""


def _abierta(orden):
    """El técnico no puede tocar una orden entregada, cancelada o rechazada."""
    if orden.estado == Orden.Estado.ENTREGADO:
        raise PresupuestoError('Esta orden ya fue entregada.')
    if orden.congelada:
        raise PresupuestoError('Esta orden fue cancelada o rechazada: solo el administrador puede reabrirla.')


@transaction.atomic
def cerrar(orden, nuevo_estado, motivo, usuario, aviso_cliente=''):
    """Cancelada (la rechazó el cliente) o Rechazada (la rechazó el administrador). La orden queda congelada.

    Guarda el estado en que estaba para poder reabrirla. Quien llama ya tiene la orden bloqueada."""
    orden.estado_previo = orden.estado
    orden.estado = nuevo_estado
    orden.resultado = nuevo_estado
    orden.motivo_cierre = ' '.join((motivo or '').split())[:300]
    orden.save(update_fields=['estado', 'estado_previo', 'motivo_cierre', 'resultado'])
    orden.registrar(f'Orden {orden.get_estado_display().lower()}' + (f': {orden.motivo_cierre}' if orden.motivo_cierre else ''), usuario)
    avisos.orden_cerrada(orden, usuario, aviso_cliente)


MENSAJE_SIN_MAS_REBAJA = ('No es posible hacer una rebaja mayor a la que ya te ofrecimos. Tu orden quedó rechazada y no tiene ningún cobro. '
                          'Muchas gracias por confiar en nosotros.')


def tiene_aceptado(orden):
    """¿El cliente ya aceptó algo (un presupuesto o una pieza) en esta orden?"""
    return (orden.presupuestos.filter(estado=Presupuesto.Estado.ACEPTADO).exists()
            or orden.piezas_solicitadas.filter(estado__in=['ACEPTADA', 'PEDIDA', 'DISPONIBLE']).exists())


def _cerrar_por_rechazo(orden, motivo, cliente):
    """El cliente rechazó un presupuesto o una pieza.

    - Si ya había aceptado algo: la orden NO se cancela. Ese pedido simplemente no se hace y se sigue (y se cobra) con lo aceptado.
    - Si no había aceptado nada y ya tenía un descuento, ese era el último ofrecimiento: se anula y la orden queda «Rechazada».
    - Si no había aceptado nada y sin descuento: queda «Cancelada» y la administración puede negociar."""
    if tiene_aceptado(orden):
        orden.registrar(f'El cliente no aceptó este pedido. Se sigue trabajando solo con lo que ya aceptó: {motivo}', cliente)
        avisos.pedido_no_aceptado(orden, motivo)
    elif orden.tiene_descuento:
        orden.descuento, orden.descuento_sin_mano, orden.descuento_motivo = Decimal('0'), False, ''
        orden.save(update_fields=['descuento', 'descuento_sin_mano', 'descuento_motivo'])
        cerrar(orden, Orden.Estado.RECHAZADA, 'El cliente no aceptó el descuento ofrecido: no hay más rebaja posible.', cliente, MENSAJE_SIN_MAS_REBAJA)
    else:
        cerrar(orden, Orden.Estado.CANCELADA, motivo, cliente)


def cargo_diagnostico(orden):
    """Lo que vale el diagnóstico (sin IVA): la tarifa «Diagnóstico» del servicio; si no hay, la tarifa más barata disponible."""
    tarifas = list(tarifas_disponibles(orden))
    diag = [t.precio for t in tarifas if 'diagn' in t.nombre.lower()]
    if diag:
        return min(diag)
    return min((t.precio for t in tarifas), default=Decimal('0'))


def cargo_mano_de_obra(orden):
    """Mano de obra (sin IVA) del último presupuesto enviado; 0 si no hubo ninguno."""
    ultimo = orden.presupuestos.order_by('-creado_en').first()
    if ultimo is None:
        return Decimal('0')
    return sum((i.subtotal for i in ultimo.items.filter(tipo=PresupuestoItem.Tipo.SERVICIO)), Decimal('0'))


def cargo_cancelacion(orden):
    """Lo que paga el cliente si cancela, sin IVA. Si ya aceptó un presupuesto, su mano de obra ya está en la factura y no se cobra dos veces.

    - Cancela en recibido o diagnóstico: solo el diagnóstico (es lo más barato).
    - Cancela en reparación: la mano de obra del presupuesto que se le envió."""
    if orden.presupuestos.filter(estado=Presupuesto.Estado.ACEPTADO).exists():
        return Decimal('0')
    if orden.estado in (Orden.Estado.RECIBIDO, Orden.Estado.DIAGNOSTICO):
        return cargo_diagnostico(orden)
    return cargo_mano_de_obra(orden)


OPCIONES_COBRO = [('nada', 'No cobrar nada'), ('diagnostico', 'Cobrar solo el diagnóstico'),
                  ('mano', 'Cobrar la mano de obra del presupuesto'), ('otro', 'Cobrar otro valor')]


def leer_cobro(orden, opcion, texto=''):
    """Traduce lo que el administrador eligió a un valor en dólares (sin IVA). ValueError si no es válido."""
    if opcion == 'nada':
        return Decimal('0')
    if opcion == 'diagnostico':
        return cargo_diagnostico(orden)
    if opcion == 'mano':
        return cargo_mano_de_obra(orden)
    if opcion == 'otro':
        try:
            valor = Decimal((texto or '').strip().replace(',', '.')).quantize(CENTAVO)
        except Exception:
            raise ValueError('Escribe un valor válido para el cobro.')
        if valor < 0 or valor > 5000:
            raise ValueError('El cobro debe estar entre $0 y $5000.')
        return valor
    raise ValueError('Elige qué se le cobra al cliente.')


@transaction.atomic
def rechazar_orden(orden_pk, admin, motivo, cobro='nada', cargo=''):
    """El administrador rechaza la orden y decide qué se le cobra al cliente (nada, el diagnóstico, la mano de obra u otro valor)."""
    orden = Orden.objects.select_for_update(of=('self',)).select_related('cliente', 'tecnico').get(pk=orden_pk)
    motivo = ' '.join((motivo or '').split())
    if orden.estado not in (Orden.Estado.RECIBIDO, Orden.Estado.DIAGNOSTICO, Orden.Estado.REPARACION):
        raise PresupuestoError('Solo se puede rechazar una orden que todavía no está lista ni cerrada.')
    if len(motivo) < 5:
        raise PresupuestoError('Escribe el motivo del rechazo (mínimo 5 letras): el cliente lo verá.')
    try:
        valor = leer_cobro(orden, cobro, cargo)
    except ValueError as e:
        raise PresupuestoError(str(e))
    orden.cargo_cancelacion = valor
    orden.save(update_fields=['cargo_cancelacion'])
    cerrar(orden, Orden.Estado.RECHAZADA, motivo, admin)
    orden.registrar(f'Cobro por el rechazo: {"nada" if valor == 0 else "$" + str(valor) + " + IVA"}', admin)
    return orden


@transaction.atomic
def cancelar_por_cliente(orden_pk, cliente, motivo=''):
    """El cliente cancela su equipo con el trabajo ya en proceso: paga la mano de obra (si no la había pagado ya) y el equipo se le devuelve."""
    orden = Orden.objects.select_for_update().get(pk=orden_pk, cliente=cliente)
    if orden.congelada or orden.estado not in (Orden.Estado.RECIBIDO, Orden.Estado.DIAGNOSTICO, Orden.Estado.REPARACION) or orden.tecnico_id is None:
        raise PresupuestoError('Solo puedes cancelar mientras tu equipo está en proceso (con técnico asignado y sin estar listo).')
    cargo = cargo_cancelacion(orden)
    orden.cargo_cancelacion = cargo
    orden.save(update_fields=['cargo_cancelacion'])
    motivo = ' '.join((motivo or '').split())
    usados = [f'{u.cantidad} × {u.producto.nombre}' for u in orden.usos_repuesto.select_related('producto').filter(precio_unit__gt=0, pieza__isnull=True)]
    if usados:
        motivo = (motivo + ' ' if motivo else '') + 'Repuestos ya usados: ' + ', '.join(usados) + '.'
    extra = (f' Se cobrará {"solo el diagnóstico" if orden.estado in (Orden.Estado.RECIBIDO, Orden.Estado.DIAGNOSTICO) else "la mano de obra"} (${cargo} más IVA).' if cargo > 0 else '')
    cerrar(orden, Orden.Estado.CANCELADA, f'El cliente canceló su equipo.{extra}' + (f' Motivo: {motivo}' if motivo else ''), cliente)
    return cargo


def siguiente_paso(orden):
    """Frase corta para el técnico: qué toca hacer ahora en esta orden (None si no hay nada que hacer)."""
    if orden.estado == Orden.Estado.ENTREGADO:
        return None
    if orden.congelada:
        return 'La orden está cerrada. Espera las indicaciones del administrador.'
    if orden.estado == Orden.Estado.LISTO:
        return 'El equipo está listo. Falta que el cliente pague y el administrador lo entregue.'
    if orden.presupuestos.filter(estado=Presupuesto.Estado.PENDIENTE).exists():
        return 'Esperando que el cliente responda tu presupuesto.'
    if orden.piezas_sin_usar:
        return 'Llegó la pieza: instálala y regístrala en «Repuestos usados».'
    if orden.espera_repuesto:
        return 'Hay una pieza en trámite. Mientras tanto puedes seguir con lo que no dependa de ella.'
    if not orden.presupuestos.filter(estado=Presupuesto.Estado.ACEPTADO).exists():
        return 'Revisa el equipo y arma el presupuesto (o pide la pieza que falte).'
    return 'Repara el equipo, registra los repuestos que uses y avanza a «Listo» cuando termines.'


def tarifas_disponibles(orden):
    """Tarifas del servicio de la orden y las generales."""
    return Tarifa.objects.filter(Q(servicio=orden.servicio) | Q(servicio__isnull=True)).select_related('servicio').order_by('servicio_id', 'id')


def repuestos_disponibles():
    return Producto.objects.filter(activo=True, stock__gt=0).select_related('categoria').order_by('nombre')


def calcular(subtotal, iva_porcentaje):
    iva = (subtotal * iva_porcentaje / 100).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    return iva, subtotal + iva


def _cantidad(valor, etiqueta):
    try:
        n = int(valor)
    except (TypeError, ValueError):
        raise PresupuestoError(f'Cantidad no válida en {etiqueta}.')
    if n < 1 or n > MAX_CANTIDAD:
        raise PresupuestoError(f'La cantidad de {etiqueta} debe estar entre 1 y {MAX_CANTIDAD}.')
    return n


@transaction.atomic
def crear(orden_pk, tecnico, tarifas, repuestos, observacion='', proforma=False):
    """tarifas: {tarifa_pk: cantidad}; repuestos: [(producto_pk, cantidad)]. Devuelve el presupuesto pendiente.

    Con proforma=True es la proforma de diagnóstico: falta alguna pieza y hay que esperarla. Puede llevar los repuestos que sí hay
    en stock; las piezas que faltan se piden aparte (el administrador las cotiza)."""
    orden = Orden.objects.select_for_update().get(pk=orden_pk, tecnico=tecnico)
    _abierta(orden)
    if orden.presupuestos.filter(estado=Presupuesto.Estado.PENDIENTE).exists():
        raise PresupuestoError('Esta orden ya tiene un presupuesto esperando la respuesta del cliente.')
    adicional = orden.presupuestos.filter(estado=Presupuesto.Estado.ACEPTADO).exists()
    if adicional and proforma:
        raise PresupuestoError('La proforma de diagnóstico es solo la primera. Para otro problema arma un presupuesto adicional.')
    observacion = ' '.join((observacion or '').split())
    if proforma:
        if len(observacion) < 10:
            raise PresupuestoError('Escribe la nota para el cliente: explícale que debe esperar la pieza.')
    lineas = []
    permitidas = {t.pk: t for t in tarifas_disponibles(orden)}
    for pk, cant in tarifas.items():
        if pk not in permitidas:
            raise PresupuestoError('Una de las tarifas no corresponde a este servicio.')
        lineas.append((PresupuestoItem.Tipo.SERVICIO, permitidas[pk], None, _cantidad(cant, permitidas[pk].nombre)))
    juntos = {}
    for pk, cant in repuestos:
        juntos[pk] = juntos.get(pk, 0) + _cantidad(cant, 'un repuesto')  # el mismo repuesto repetido se suma
    productos = {p.pk: p for p in Producto.objects.filter(pk__in=juntos, activo=True)}
    for pk, cant in juntos.items():
        p = productos.get(pk)
        if p is None:
            raise PresupuestoError('Uno de los repuestos ya no está disponible.')
        if cant > p.stock:
            raise PresupuestoError(f'Solo hay {p.stock} unidad{"es" if p.stock != 1 else ""} de {p.nombre}. Pide la pieza al administrador.')
        lineas.append((PresupuestoItem.Tipo.REPUESTO, None, p, cant))
    if not lineas:
        raise PresupuestoError('Agrega al menos un servicio o un repuesto.')
    iva_pct = orden.iva_efectivo
    presupuesto = Presupuesto.objects.create(orden=orden, iva_porcentaje=iva_pct, observacion=observacion[:500], espera_pieza=proforma)
    subtotal = Decimal('0')
    for tipo, tarifa, producto, cant in lineas:
        item = PresupuestoItem.objects.create(presupuesto=presupuesto, tipo=tipo, tarifa=tarifa, producto=producto, cantidad=cant)
        subtotal += item.subtotal
    presupuesto.subtotal = subtotal
    presupuesto.iva, presupuesto.total = calcular(subtotal, iva_pct)
    presupuesto.save(update_fields=['subtotal', 'iva', 'total'])
    orden.registrar(f'{"Proforma de diagnóstico" if proforma else "Presupuesto adicional" if adicional else "Presupuesto"} #{presupuesto.pk} enviada al cliente por ${presupuesto.total}', tecnico)
    avisos.presupuesto_enviado(presupuesto)
    return presupuesto


@transaction.atomic
def retirar(presupuesto_pk, tecnico):
    """El técnico retira un presupuesto pendiente (por ejemplo, para corregirlo y enviar otro)."""
    p = Presupuesto.objects.select_for_update(of=('self',)).select_related('orden').get(pk=presupuesto_pk, orden__tecnico=tecnico)
    if p.estado != Presupuesto.Estado.PENDIENTE:
        raise PresupuestoError('Solo se puede retirar un presupuesto pendiente.')
    p.orden.registrar(f'Presupuesto #{p.pk} retirado por el técnico', tecnico)
    p.delete()


@transaction.atomic
def responder(presupuesto_pk, cliente, acepta, motivo=''):
    """El cliente acepta o rechaza. Al aceptar se descuentan los repuestos; si falta alguno no se acepta nada."""
    p = Presupuesto.objects.select_for_update(of=('self',)).select_related('orden', 'orden__tecnico').get(pk=presupuesto_pk, orden__cliente=cliente)
    if p.estado != Presupuesto.Estado.PENDIENTE:
        raise PresupuestoError('Este presupuesto ya fue respondido.')
    _abierta(p.orden)
    salidas = []
    if acepta:
        # Productos en orden de id para que dos aceptaciones simultáneas no se bloqueen entre sí
        for item in p.items.filter(tipo=PresupuestoItem.Tipo.REPUESTO).select_related('producto').order_by('producto_id'):
            try:
                producto = stock.descontar(item.producto_id, item.cantidad, f'Presupuesto #{p.pk} · orden #{p.orden_id}', cliente, presupuesto=p)
            except stock.StockError as e:
                raise SinStock(f'No se pudo aceptar: {e} El técnico revisará el presupuesto.')
            salidas.append((producto, item.cantidad))
    p.estado = Presupuesto.Estado.ACEPTADO if acepta else Presupuesto.Estado.RECHAZADO
    p.respondido_en = timezone.now()
    p.save(update_fields=['estado', 'respondido_en'])
    p.orden.registrar(f'Presupuesto #{p.pk} {"aceptado" if acepta else "rechazado"} por el cliente', cliente)
    avisos.presupuesto_respondido(p)
    if not acepta:
        _cerrar_por_rechazo(p.orden, motivo or f'El cliente rechazó el presupuesto #{p.pk} (${p.total})', cliente)
    for producto, cantidad in salidas:
        avisos.salida_inventario(producto, cantidad, p.orden, 'repuesto del presupuesto aceptado')
    return p


def avisar_falta_de_stock(presupuesto_pk):
    """Si aceptar falló por falta de stock, se avisa al técnico y a los administradores (fuera de la transacción revertida)."""
    p = Presupuesto.objects.select_related('orden', 'orden__tecnico').get(pk=presupuesto_pk)
    avisos.presupuesto_sin_stock(p)


def _sin_piezas_abiertas(orden):
    if not orden.piezas_solicitadas.filter(estado__in=PiezaSolicitada.ABIERTOS).exists() and orden.espera_repuesto:
        orden.espera_repuesto = False
        orden.save(update_fields=['espera_repuesto'])


def fecha_con_pieza(orden, dias_entrega, desde=None):
    """Fecha estimada de entrega contando la pieza: llega en N días y se da un día para instalarla.

    Solo puede atrasar la fecha, nunca adelantarla. Ejemplo: estimada el 23, hoy 22 y la pieza tarda 1 día → 24."""
    hoy = desde or timezone.localdate()
    minima = hoy + timedelta(days=dias_entrega + DIAS_INSTALACION)
    return max(orden.fecha_estimada, minima) if orden.fecha_estimada else minima


def total_orden(orden):
    """Lo que el cliente debe pagar: presupuestos aceptados más piezas aceptadas (con IVA)."""
    return orden.total_a_pagar


@transaction.atomic
def solicitar_pieza(orden_pk, tecnico, nombre, cantidad, observacion, producto=None, categoria=None, agotada=False):
    orden = Orden.objects.select_for_update().get(pk=orden_pk, tecnico=tecnico)
    _abierta(orden)
    if orden.estado not in (Orden.Estado.RECIBIDO, Orden.Estado.DIAGNOSTICO, Orden.Estado.REPARACION):
        raise PresupuestoError('Solo se pide una pieza mientras la orden está en diagnóstico o reparación.')
    cant = _cantidad(cantidad, 'la pieza')
    extra = {}
    if agotada:
        if producto is None or not producto.activo:
            raise PresupuestoError('Elige la pieza del inventario que se agotó.')
        if producto.stock >= cant:
            raise PresupuestoError(f'Del inventario aún hay {producto.stock} unidad(es) de {producto.nombre}: úsala directamente en «Repuestos usados».')
        nombre, categoria = producto.nombre, producto.categoria
        # Se cobra al precio de venta del inventario (sin cotizar); cuenta en la factura cuando el administrador la repone
        extra = {'precio': producto.precio_venta, 'iva_porcentaje': orden.iva_efectivo, 'agotada': True}
    nombre = ' '.join((nombre or '').split())
    if len(nombre) < 3:
        raise PresupuestoError('Escribe el nombre de la pieza.')
    pieza = PiezaSolicitada.objects.create(
        orden=orden, tecnico=tecnico, producto=producto, categoria=categoria, nombre_pieza=nombre[:120], cantidad=cant,
        observacion=(observacion or '').strip()[:300], **extra,
    )
    if not orden.espera_repuesto:
        orden.espera_repuesto = True
        orden.save(update_fields=['espera_repuesto'])
    orden.registrar(f'Pieza {"agotada en el inventario" if agotada else "solicitada"}: {cant} × {pieza.nombre_pieza}', tecnico)
    avisos.pieza_solicitada(pieza)
    return pieza


def _pieza(pk, *estados):
    pieza = PiezaSolicitada.objects.select_for_update(of=('self',)).select_related('orden', 'orden__cliente', 'tecnico').get(pk=pk)
    if pieza.estado not in estados:
        raise PresupuestoError(f'Esta pieza ya está en «{pieza.get_estado_display()}».')
    return pieza


@transaction.atomic
def cotizar_pieza(pieza_pk, admin, proveedor, costo, precio, dias, nota='', categoria=None):
    """El administrador pone precio y días de entrega; el cliente recibe la cotización."""
    pieza = _pieza(pieza_pk, PiezaSolicitada.Estado.PENDIENTE)
    if pieza.agotada:
        raise PresupuestoError('Esta pieza ya está en el inventario y solo se agotó: usa «Reponer» en lugar de cotizarla.')
    if categoria is not None:
        pieza.categoria = categoria
    if proveedor is None:
        raise PresupuestoError('Elige el proveedor al que le vas a pedir la pieza.')
    if costo is None or costo <= 0:
        raise PresupuestoError('Escribe cuánto te cobra el proveedor por cada unidad.')
    if precio is None:
        precio = precios.sugerido(costo, Configuracion.margen_defecto())
    if precio is None or precio <= 0:
        raise PresupuestoError('Escribe el precio que pagará el cliente por cada unidad.')
    if precio < costo:
        raise PresupuestoError('El precio al cliente no puede ser menor que lo que cuesta la pieza.')
    if dias is None and proveedor is not None:
        dias = proveedor.dias_entrega
    pieza.proveedor, pieza.costo, pieza.precio, pieza.dias_entrega = proveedor, costo, precio, dias
    pieza.nota_admin = ' '.join((nota or '').split())[:300]
    pieza.iva_porcentaje = pieza.orden.iva_efectivo
    pieza.estado = PiezaSolicitada.Estado.COTIZADA
    pieza.cotizada_en = timezone.now()
    pieza.save()
    pieza.orden.registrar(f'Pieza cotizada: {pieza.cantidad} × {pieza.nombre_pieza} por ${pieza.total}', admin)
    avisos.pieza_cotizada(pieza)
    return pieza


@transaction.atomic
def descartar_pieza(pieza_pk, admin, nota='', avisar_cliente=False, cerrar_orden=False, cobro='nada', cargo=''):
    """No se va a pedir la pieza (por ejemplo, no se consigue). Siempre se avisa al técnico; opcionalmente al cliente.

    Con cerrar_orden=True la orden se rechaza por esto y el administrador elige qué cobrar (nada, diagnóstico, mano de obra u otro)."""
    pieza = _pieza(pieza_pk, PiezaSolicitada.Estado.PENDIENTE)
    pieza.estado = PiezaSolicitada.Estado.DESCARTADA
    pieza.nota_admin = ' '.join((nota or '').split())[:300]
    pieza.save(update_fields=['estado', 'nota_admin'])
    pieza.orden.registrar(f'No se pedirá la pieza: {pieza.nombre_pieza}', admin)
    _sin_piezas_abiertas(pieza.orden)
    avisos.pieza_descartada(pieza)
    if avisar_cliente or cerrar_orden:
        avisos.pieza_descartada_cliente(pieza)
    if cerrar_orden:
        motivo = f'No se consiguió la pieza {pieza.nombre_pieza}.' + (f' {pieza.nota_admin}' if pieza.nota_admin else '')
        rechazar_orden(pieza.orden_id, admin, motivo, cobro, cargo)
    return pieza


@transaction.atomic
def responder_pieza(pieza_pk, cliente, acepta, motivo=''):
    pieza = _pieza(pieza_pk, PiezaSolicitada.Estado.COTIZADA)
    if pieza.orden.cliente_id != cliente.pk:
        raise PresupuestoError('Esta pieza no es de tu equipo.')
    _abierta(pieza.orden)
    pieza.estado = PiezaSolicitada.Estado.ACEPTADA if acepta else PiezaSolicitada.Estado.RECHAZADA
    pieza.respondida_en = timezone.now()
    pieza.save(update_fields=['estado', 'respondida_en'])
    pieza.orden.registrar(f'Pieza {pieza.nombre_pieza} {"aceptada" if acepta else "rechazada"} por el cliente (${pieza.total})', cliente)
    if not acepta:
        _sin_piezas_abiertas(pieza.orden)
    avisos.pieza_respondida(pieza)
    if not acepta:
        orden = Orden.objects.select_for_update().get(pk=pieza.orden_id)
        _cerrar_por_rechazo(orden, motivo or f'El cliente rechazó la pieza {pieza.nombre_pieza} (${pieza.total})', cliente)
    return pieza


@transaction.atomic
def pedir_pieza(pieza_pk, admin):
    """El administrador hizo el pedido al proveedor: queda la fecha estimada de llegada."""
    pieza = _pieza(pieza_pk, PiezaSolicitada.Estado.ACEPTADA)
    hoy = timezone.localdate()
    pieza.estado = PiezaSolicitada.Estado.PEDIDA
    pieza.pedida_en = timezone.now()
    if pieza.dias_entrega is not None:
        pieza.llegada_estimada = hoy + timedelta(days=pieza.dias_entrega)
    pieza.save(update_fields=['estado', 'pedida_en', 'llegada_estimada'])
    orden = Orden.objects.select_for_update().get(pk=pieza.orden_id)
    orden.registrar(f'Pieza pedida al proveedor: {pieza.nombre_pieza}' + (f' (llega aprox. el {pieza.llegada_estimada:%d/%m/%Y})' if pieza.llegada_estimada else ''), admin)
    avisos.pieza_pedida(pieza)
    _mover_fecha(orden, pieza, admin, hoy)
    return pieza


def _mover_fecha(orden, pieza, admin, hoy):
    """Si la pieza tarda, la fecha de entrega se corre (nunca se adelanta) y se avisa al cliente."""
    if pieza.dias_entrega is not None:
        nueva, anterior = fecha_con_pieza(orden, pieza.dias_entrega, hoy), orden.fecha_estimada
        if nueva != anterior:
            motivo = f'La pieza {pieza.nombre_pieza} tarda {pieza.dias_entrega} día{"s" if pieza.dias_entrega != 1 else ""} en llegar'
            fechas.cambiar(orden, nueva, motivo, admin)
            orden.save(update_fields=['fecha_estimada', 'fecha_prometida'])
            orden.registrar(f'Fecha estimada de entrega: {nueva:%d/%m/%Y} (por la pieza)', admin)
            avisos.fecha_actualizada(orden, anterior)


@transaction.atomic
def reponer_agotada(pieza_pk, admin, dias):
    """La pieza se agotó en el inventario: el administrador repone el stock y dice en cuántos días llegará.

    Se avisa al técnico y al cliente, y la fecha de entrega se corre si hace falta. No hay cotización ni aceptación: la pieza ya tiene precio."""
    pieza = _pieza(pieza_pk, PiezaSolicitada.Estado.PENDIENTE)
    if not pieza.agotada:
        raise PresupuestoError('Esta pieza no está en el inventario: cotízala con un proveedor.')
    if dias is None or dias < 0 or dias > 365:
        raise PresupuestoError('Escribe en cuántos días llegará el stock (de 0 a 365).')
    hoy = timezone.localdate()
    pieza.estado, pieza.dias_entrega = PiezaSolicitada.Estado.PEDIDA, dias
    pieza.pedida_en, pieza.llegada_estimada = timezone.now(), hoy + timedelta(days=dias)
    pieza.save(update_fields=['estado', 'dias_entrega', 'pedida_en', 'llegada_estimada'])
    orden = Orden.objects.select_for_update().get(pk=pieza.orden_id)
    orden.registrar(f'Se repondrá el stock de {pieza.nombre_pieza} (llega aprox. el {pieza.llegada_estimada:%d/%m/%Y})', admin)
    avisos.pieza_agotada_en_camino(pieza)
    _mover_fecha(orden, pieza, admin, hoy)
    return pieza


def atender_agotadas(producto, usuario):
    """Se repuso el stock de un producto (la llama el inventario): las piezas agotadas que lo esperaban pasan a «Disponible» y se avisa al técnico."""
    disponible = producto.stock
    espera = PiezaSolicitada.objects.select_for_update(of=('self',)).filter(
        producto=producto, agotada=True, estado__in=[PiezaSolicitada.Estado.PENDIENTE, PiezaSolicitada.Estado.PEDIDA],
    ).select_related('tecnico', 'orden', 'orden__cliente').order_by('creada_en')
    for pieza in espera:
        if pieza.cantidad > disponible:
            continue
        disponible -= pieza.cantidad
        orden = Orden.objects.select_for_update().get(pk=pieza.orden_id)
        pieza.estado = PiezaSolicitada.Estado.DISPONIBLE
        pieza.save(update_fields=['estado'])
        orden.registrar(f'Llegó al inventario: {pieza.cantidad} × {pieza.nombre_pieza}', usuario)
        _sin_piezas_abiertas(orden)
        cambio = False
        if orden.estado in (Orden.Estado.RECIBIDO, Orden.Estado.DIAGNOSTICO) and not orden.espera_repuesto and not orden.congelada:
            orden.registrar(f'Estado: {orden.get_estado_display()} → {Orden.Estado.REPARACION.label} (automático, llegó la pieza)', usuario)
            orden.estado = Orden.Estado.REPARACION
            orden.save(update_fields=['estado'])
            cambio = True
        pieza.orden = orden
        avisos.pieza_disponible(pieza, cambio)




@transaction.atomic
def pieza_disponible(pieza_pk, admin):
    """(Se llama al registrar la pieza en el inventario.) Llegó la pieza: entra al inventario y la orden pasa a reparación. El técnico la registra como usada y ahí sale del stock."""
    pieza = _pieza(pieza_pk, PiezaSolicitada.Estado.ACEPTADA, PiezaSolicitada.Estado.PEDIDA)
    if pieza.producto_id is None:
        raise PresupuestoError('Primero registra la pieza en el inventario con el botón «Registrar en el inventario».')
    if pieza.estado == PiezaSolicitada.Estado.ACEPTADA:
        pedir_pieza(pieza_pk, admin)  # si no marcó «Ya la pedí», se da por pedida
        pieza = _pieza(pieza_pk, PiezaSolicitada.Estado.PEDIDA)
    orden = Orden.objects.select_for_update().get(pk=pieza.orden_id)
    stock.entrada_de_pieza(
        pieza.producto_id, pieza.cantidad, pieza.proveedor, pieza.costo,
        f'Llegada de la pieza para la orden #{orden.pk}', admin,
    )
    pieza.estado = PiezaSolicitada.Estado.DISPONIBLE
    pieza.save(update_fields=['estado'])
    orden.registrar(f'Llegó la pieza: {pieza.cantidad} × {pieza.nombre_pieza}', admin)
    _sin_piezas_abiertas(orden)  # libera la espera si era la última pieza
    cambio_estado = False
    if orden.estado in (Orden.Estado.RECIBIDO, Orden.Estado.DIAGNOSTICO) and not orden.espera_repuesto:
        orden.registrar(f'Estado: {orden.get_estado_display()} → {Orden.Estado.REPARACION.label} (automático, llegó la pieza)', admin)
        orden.estado = Orden.Estado.REPARACION
        orden.save(update_fields=['estado'])
        cambio_estado = True
    pieza.orden = orden  # con el estado ya actualizado, para el aviso
    avisos.pieza_disponible(pieza, cambio_estado)
    return pieza


@transaction.atomic
def usar_repuesto(orden_pk, tecnico, producto_pk=None, cantidad=1, pieza_pk=None, reemplazo=False):
    """El técnico registra un repuesto del inventario usado en la reparación: sale del stock.

    Con pieza_pk usa la pieza que se pidió para esta orden (y no puede usarla dos veces).
    Con reemplazo=True sustituye un repuesto dañado: sale del stock pero no se cobra al cliente.
    Un repuesto extra normal se suma a la factura del cliente y él recibe un aviso."""
    orden = Orden.objects.select_for_update().get(pk=orden_pk, tecnico=tecnico)
    _abierta(orden)
    if orden.estado != Orden.Estado.REPARACION:
        raise PresupuestoError('Los repuestos se registran cuando la orden está «En reparación».')
    pieza = None
    if pieza_pk is not None:
        pieza = PiezaSolicitada.objects.select_for_update(of=('self',)).filter(pk=pieza_pk, orden=orden).first()
        if pieza is None or pieza.estado != PiezaSolicitada.Estado.DISPONIBLE or pieza.producto_id is None:
            raise PresupuestoError('Esa pieza todavía no está disponible en el inventario.')
        if pieza.usos.exists():
            raise PresupuestoError('Esa pieza ya fue registrada como usada.')
        producto_pk, cantidad = pieza.producto_id, pieza.cantidad
    cantidad = _cantidad(cantidad, 'el repuesto')
    producto = Producto.objects.filter(pk=producto_pk, activo=True).first()
    if producto is None:
        raise PresupuestoError('Elige un repuesto del inventario.')
    try:
        producto = stock.descontar(producto.pk, cantidad, f'Usado en la orden #{orden.pk}', tecnico, avisar=pieza is None)
    except stock.StockError as e:
        raise PresupuestoError(str(e))
    uso = UsoRepuesto.objects.create(
        orden=orden, producto=producto, cantidad=cantidad, tecnico=tecnico, pieza=pieza,
        precio_unit=0 if (pieza or reemplazo) else producto.precio_venta,  # la pieza pedida ya está cobrada; el reemplazo lo paga el taller
    )
    orden.registrar(f'Repuesto usado{" (reemplazo, no se cobra)" if reemplazo else ""}: {cantidad} × {producto.nombre}', tecnico)
    avisos.salida_inventario(producto, cantidad, orden, 'repuesto de reemplazo' if reemplazo else 'repuesto usado en la reparación')
    if not pieza and not reemplazo:
        avisos.repuesto_extra_cliente(orden, producto, cantidad, uso.precio_unit)
    return uso


def cantidades_en_uso(orden):
    """{producto_id: unidades} que el técnico tiene en uso en esta orden (registradas o marcadas «en uso»), menos las ya reportadas como dañadas."""
    from collections import Counter
    total = Counter()
    for pid, cant in orden.usos_repuesto.values_list('producto_id', 'cantidad'):
        total[pid] += cant
    for pid, cant in PresupuestoItem.objects.filter(
        presupuesto__orden=orden, presupuesto__estado=Presupuesto.Estado.ACEPTADO,
        tipo=PresupuestoItem.Tipo.REPUESTO, en_uso_en__isnull=False,
    ).values_list('producto_id', 'cantidad'):
        total[pid] += cant
    for pid, cant in orden.repuestos_danados.values_list('producto_id', 'cantidad'):
        total[pid] -= cant
    return {pid: c for pid, c in total.items() if c > 0}


def productos_en_uso(orden):
    """Repuestos que el técnico está usando en esta orden y aún pueden reportarse como dañados.

    Cada producto trae `en_uso` (unidades). Un repuesto ya salió del inventario cuando se registró o se aceptó,
    por eso se puede reportar aunque el stock esté en 0."""
    cant = cantidades_en_uso(orden)
    productos = list(Producto.objects.filter(pk__in=cant).order_by('nombre'))
    for p in productos:
        p.en_uso = cant[p.pk]
    return productos


@transaction.atomic
def reportar_danado(orden_pk, tecnico, producto_pk, cantidad, motivo):
    """Un repuesto que ya estaba en uso se dañó: queda como pérdida del negocio (no se cobra al cliente).

    La unidad ya había salido del inventario al registrarse o aceptarse, así que NO se descuenta otra vez:
    se puede reportar aunque el stock esté en 0."""
    from finanzas.models import Gasto
    orden = Orden.objects.select_for_update().get(pk=orden_pk, tecnico=tecnico)
    _abierta(orden)
    if orden.estado not in (Orden.Estado.DIAGNOSTICO, Orden.Estado.REPARACION):
        raise PresupuestoError('Solo se reporta un daño mientras la orden está en diagnóstico o reparación.')
    motivo = ' '.join((motivo or '').split())
    if len(motivo) < 5:
        raise PresupuestoError('Cuenta qué pasó (por ejemplo: se rompió al instalarla).')
    cantidad = _cantidad(cantidad, 'el repuesto dañado')
    disponibles = cantidades_en_uso(orden).get(producto_pk, 0)
    if disponibles == 0:
        raise PresupuestoError('Elige un repuesto que estés usando en esta orden. Si aún no lo has marcado, márcalo primero como «en uso».')
    if cantidad > disponibles:
        raise PresupuestoError(f'En esta orden solo tienes {disponibles} unidad(es) de ese repuesto en uso.')
    producto = Producto.objects.get(pk=producto_pk)
    unitario = producto.costo_pedido or producto.precio_venta  # sin costo de compra conocido, se valora a precio de venta
    perdida = max((unitario * cantidad).quantize(CENTAVO), CENTAVO)
    Gasto.objects.create(
        tipo=Gasto.Tipo.PERDIDA, concepto=f'Repuesto dañado en orden #{orden.pk}: {cantidad} × {producto.nombre}'[:150],
        monto=perdida, usuario=tecnico, automatico=True,
    )
    RepuestoDanado.objects.create(orden=orden, producto=producto, cantidad=cantidad, motivo=motivo[:200], perdida=perdida, tecnico=tecnico)
    orden.registrar(f'Repuesto dañado: {cantidad} × {producto.nombre} ({motivo})', tecnico)
    avisos.repuesto_danado(orden, producto, cantidad, motivo, perdida)
    return perdida


@transaction.atomic
def marcar_en_uso(item_pk, tecnico):
    """El técnico marca que está usando un repuesto del presupuesto aceptado (ya salió del inventario al aceptarse)."""
    orden_pk = PresupuestoItem.objects.filter(pk=item_pk, presupuesto__orden__tecnico=tecnico).values_list('presupuesto__orden_id', flat=True).first()
    if orden_pk is None:
        raise PresupuestoError('Ese repuesto no es de tus órdenes.')
    orden = Orden.objects.select_for_update().get(pk=orden_pk)
    item = PresupuestoItem.objects.select_for_update(of=('self',)).select_related('producto', 'presupuesto').get(pk=item_pk)
    _abierta(orden)
    if orden.estado != Orden.Estado.REPARACION:
        raise PresupuestoError('Los repuestos se marcan cuando la orden está «En reparación».')
    if item.tipo != PresupuestoItem.Tipo.REPUESTO or item.presupuesto.estado != Presupuesto.Estado.ACEPTADO:
        raise PresupuestoError('Ese repuesto no está en un presupuesto aceptado.')
    if item.en_uso_en:
        raise PresupuestoError('Ese repuesto ya está marcado como en uso.')
    item.en_uso_en = timezone.now()
    item.save(update_fields=['en_uso_en'])
    orden.registrar(f'Repuesto en uso: {item.cantidad} × {item.producto.nombre}', tecnico)
    avisos.repuesto_en_uso(orden, item.producto, item.cantidad)
    return item


def devolver_no_usados(orden, usuario):
    """Orden cancelada o rechazada: los repuestos de presupuestos aceptados que el técnico no llegó a usar no se cobran y vuelven al inventario."""
    items = PresupuestoItem.objects.select_for_update(of=('self',)).select_related('producto').filter(
        presupuesto__orden=orden, presupuesto__estado=Presupuesto.Estado.ACEPTADO, tipo=PresupuestoItem.Tipo.REPUESTO,
        en_uso_en__isnull=True, devuelto_en__isnull=True)
    for i in items:
        stock.entrada_de_pieza(i.producto_id, i.cantidad, None, None, f'No se usó: orden #{orden.pk} cerrada sin reparar', usuario)
        i.devuelto_en = timezone.now()
        i.save(update_fields=['devuelto_en'])
        orden.registrar(f'Repuesto no usado, vuelve al inventario y no se cobra: {i.cantidad} × {i.producto.nombre}', usuario)


@transaction.atomic
def quitar_uso(uso_pk, tecnico):
    """Corrige un error: el repuesto vuelve al inventario."""
    uso = UsoRepuesto.objects.select_for_update(of=('self',)).select_related('orden', 'producto').get(pk=uso_pk, orden__tecnico=tecnico)
    _abierta(uso.orden)
    stock.entrada_de_pieza(uso.producto_id, uso.cantidad, None, None, f'Devuelto al inventario: se quitó de la orden #{uso.orden_id}', tecnico)
    uso.orden.registrar(f'Repuesto devuelto al inventario: {uso.cantidad} × {uso.producto.nombre}', tecnico)
    uso.delete()
