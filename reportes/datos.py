"""Datos de cada reporte. Todas las funciones devuelven un `Reporte`: título, columnas, filas y un resumen."""

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db.models import F, Q, Sum
from django.utils import timezone

from core.models import Configuracion
from finanzas.models import Gasto
from inventario.models import MovimientoInventario, Producto
from ordenes.models import CambioFecha, Orden, Pago, RepuestoDanado

MAX_FILAS = 5000
MAX_DIAS = 366 * 5


class RangoError(ValueError):
    """Las fechas elegidas no sirven; el mensaje se muestra tal cual."""


@dataclass
class Reporte:
    clave: str
    titulo: str
    columnas: list
    filas: list
    resumen: list = field(default_factory=list)   # [(etiqueta, valor)]
    derecha: tuple = ()                           # índices de columnas numéricas (alineadas a la derecha)
    periodo: str = ''
    truncado: bool = False


def dinero(v):
    return f'{Decimal(v or 0):.2f}'


def fecha_hora(v):
    return timezone.localtime(v).strftime('%d/%m/%Y %H:%M') if v else ''


def leer_rango(desde, hasta):
    """Texto ISO -> (desde, hasta). Sin datos: el mes actual."""
    hoy = timezone.localdate()
    try:
        d = timezone.datetime.fromisoformat(desde).date() if desde else hoy.replace(day=1)
        h = timezone.datetime.fromisoformat(hasta).date() if hasta else hoy
    except ValueError:
        raise RangoError('Fecha no válida. Usa el calendario.')
    if d > h:
        raise RangoError('La fecha «desde» no puede ser posterior a «hasta».')
    if (h - d).days > MAX_DIAS:
        raise RangoError('El período es demasiado largo (máximo 5 años).')
    return d, h


def _periodo(d, h):
    return f'Del {d:%d/%m/%Y} al {h:%d/%m/%Y}'


def _cortar(qs):
    filas = list(qs[:MAX_FILAS + 1])
    return filas[:MAX_FILAS], len(filas) > MAX_FILAS


def ordenes(d, h):
    qs = Orden.objects.filter(creada_en__date__range=(d, h)).select_related('cliente', 'tecnico', 'servicio').order_by('creada_en')
    lista, corto = _cortar(qs)
    filas, total, descontado = [], Decimal('0'), Decimal('0')
    for o in lista:
        f = o.factura
        a_pagar = f['total']
        total += a_pagar
        descontado += f['descuento']
        filas.append([o.pk, fecha_hora(o.creada_en), o.cliente.nombre, o.modelo, o.servicio.nombre,
                      o.tecnico.nombre if o.tecnico else 'Sin asignar', o.nombre_estado(), dinero(f['descuento']) if f['descuento'] else '—', dinero(a_pagar)])
    por_estado = [(f'Órdenes {e.lower()}', sum(1 for o in lista if (o.cierre_visible or o.estado) == v)) for v, e in Orden.Estado.choices]
    return Reporte('ordenes', 'Reporte de órdenes', ['N.º', 'Recibida', 'Cliente', 'Equipo', 'Servicio', 'Técnico', 'Estado', 'Descuento $', 'Total $'],
                   filas, [('Órdenes en el período', len(lista))] + por_estado + [('Descuentos acordados (sin IVA)', f'${dinero(descontado)}'), ('Total facturado', f'${dinero(total)}')],
                   derecha=(0, 7, 8), periodo=_periodo(d, h), truncado=corto)


def ingresos(d, h):
    qs = Pago.objects.filter(estado=Pago.Estado.CONFIRMADO, validado_en__date__range=(d, h)).select_related('orden__cliente').order_by('validado_en')
    lista, corto = _cortar(qs)
    filas = [[fecha_hora(p.validado_en), p.orden_id, p.orden.cliente.nombre, p.get_metodo_display(), p.referencia, dinero(p.monto)] for p in lista]
    suma = lambda m: sum((p.monto for p in lista if p.metodo == m), Decimal('0'))
    return Reporte('ingresos', 'Reporte de ingresos (pagos confirmados)', ['Fecha', 'Orden', 'Cliente', 'Método', 'Comprobante', 'Monto $'], filas,
                   [('Pagos confirmados', len(lista)), ('En efectivo', f'${dinero(suma("EFECTIVO"))}'),
                    ('Por transferencia', f'${dinero(suma("TRANSFERENCIA"))}'), ('Total cobrado', f'${dinero(sum((p.monto for p in lista), Decimal("0")))}')],
                   derecha=(1, 5), periodo=_periodo(d, h), truncado=corto)


def gastos(d, h):
    qs = Gasto.objects.filter(fecha__range=(d, h)).select_related('usuario').order_by('fecha', 'id')
    lista, corto = _cortar(qs)
    filas = [[f'{g.fecha:%d/%m/%Y}', g.get_tipo_display(), g.concepto, 'Automático' if g.automatico else 'Manual', dinero(g.monto)] for g in lista]
    suma = lambda tp, auto=None: sum((g.monto for g in lista if g.tipo == tp and (auto is None or g.automatico == auto)), Decimal('0'))
    return Reporte('gastos', 'Reporte de gastos y pérdidas', ['Fecha', 'Tipo', 'Concepto', 'Origen', 'Monto $'], filas,
                   [('Gastos (incluye reposiciones)', f'${dinero(suma("GASTO"))}'), ('Pérdidas registradas', f'${dinero(suma("PERDIDA", False))}'),
                    ('Repuestos perdidos o dañados (ya pagados al comprar)', f'${dinero(suma("PERDIDA", True))}')],
                   derecha=(4,), periodo=_periodo(d, h), truncado=corto)


def estado_financiero(d, h):
    pagos = Pago.objects.filter(estado=Pago.Estado.CONFIRMADO)
    suma = lambda q: q.aggregate(t=Sum('monto'))['t'] or Decimal('0')
    salen = lambda q: suma(q.filter(Q(tipo='GASTO') | Q(automatico=False)))
    ing = suma(pagos.filter(validado_en__date__range=(d, h)))
    gas = suma(Gasto.objects.filter(fecha__range=(d, h), tipo='GASTO'))
    per = suma(Gasto.objects.filter(fecha__range=(d, h), tipo='PERDIDA', automatico=False))
    per_inv = suma(Gasto.objects.filter(fecha__range=(d, h), tipo='PERDIDA', automatico=True))
    capital = Configuracion.capital_inicial()
    caja = capital + suma(pagos) - salen(Gasto.objects.all())
    inventario = Producto.objects.filter(activo=True).aggregate(t=Sum(F('stock') * F('costo_pedido')))['t'] or Decimal('0')
    filas = [
        ['Ingresos cobrados en el período', dinero(ing)],
        ['(-) Gastos, incluidas reposiciones a proveedores', dinero(gas)],
        ['(-) Pérdidas registradas', dinero(per)],
        ['(=) Utilidad del período', dinero(ing - gas - per)],
        ['Repuestos perdidos o dañados (informativo: ya pagados al comprar)', dinero(per_inv)],
        ['Capital inicial', dinero(capital)],
        ['Dinero en caja hoy (capital + cobrado - gastado, todo el historial)', dinero(caja)],
        ['Valor del inventario hoy (a costo de compra)', dinero(inventario)],
    ]
    return Reporte('estado', 'Estado financiero', ['Concepto', 'Valor $'], filas, [], derecha=(1,), periodo=_periodo(d, h))


def inventario(d=None, h=None):
    lista, corto = _cortar(Producto.objects.select_related('categoria').order_by('nombre'))
    filas, total = [], Decimal('0')
    for p in lista:
        valor = p.stock * p.costo_pedido
        total += valor if p.activo else 0
        estado = 'Desactivado' if not p.activo else ('Stock bajo' if p.stock <= p.stock_minimo else 'Normal')
        filas.append([p.codigo, p.nombre, p.categoria.nombre if p.categoria else '', p.stock, p.stock_minimo, dinero(p.costo_pedido), dinero(p.precio_venta), dinero(valor), estado])
    return Reporte('inventario', 'Reporte de inventario', ['Código', 'Producto', 'Categoría', 'Stock', 'Mínimo', 'Costo $', 'Venta $', 'Valor $', 'Estado'], filas,
                   [('Productos', len(lista)), ('Con stock bajo', sum(1 for r in filas if r[8] == 'Stock bajo')), ('Valor del inventario activo', f'${dinero(total)}')],
                   derecha=(3, 4, 5, 6, 7), periodo=f'Situación al {timezone.localdate():%d/%m/%Y}', truncado=corto)


def movimientos(d, h):
    qs = MovimientoInventario.objects.filter(fecha__date__range=(d, h)).select_related('producto', 'usuario').order_by('fecha', 'id')
    lista, corto = _cortar(qs)
    filas = [[fecha_hora(m.fecha), m.producto.nombre, m.get_tipo_display(), m.cantidad, m.stock_resultante, m.motivo, m.usuario.nombre if m.usuario else ''] for m in lista]
    return Reporte('movimientos', 'Movimientos de inventario (kardex)', ['Fecha', 'Producto', 'Tipo', 'Cantidad', 'Stock', 'Motivo', 'Usuario'], filas,
                   [('Movimientos', len(lista))], derecha=(3, 4), periodo=_periodo(d, h), truncado=corto)


def danados(d, h):
    qs = RepuestoDanado.objects.filter(fecha__date__range=(d, h)).select_related('producto', 'orden', 'tecnico').order_by('fecha', 'id')
    lista, corto = _cortar(qs)
    filas = [[fecha_hora(x.fecha), x.orden_id, x.producto.nombre, x.cantidad, x.motivo, x.tecnico.nombre if x.tecnico else '', dinero(x.perdida)] for x in lista]
    return Reporte('danados', 'Informe de repuestos dañados', ['Fecha', 'Orden', 'Repuesto', 'Cant.', 'Motivo', 'Técnico', 'Pérdida $'], filas,
                   [('Reportes de daño', len(lista)), ('Unidades dañadas', sum(x.cantidad for x in lista)),
                    ('Valor perdido (ya pagado al comprar)', f'${dinero(sum((x.perdida for x in lista), Decimal("0")))}')],
                   derecha=(1, 3, 6), periodo=_periodo(d, h), truncado=corto)


def fechas(d, h):
    qs = (Orden.objects.filter(creada_en__date__range=(d, h)).exclude(estado__in=['CANCELADA', 'RECHAZADA']).filter(resultado='')
          .select_related('cliente').prefetch_related('cambios_fecha').order_by('creada_en'))
    lista, corto = _cortar(qs)
    filas = []
    for o in lista:
        cambios = list(o.cambios_fecha.all())
        filas.append([
            o.pk, o.cliente.nombre, o.modelo, f'{o.fecha_prometida:%d/%m/%Y}' if o.fecha_prometida else '',
            f'{o.fecha_estimada:%d/%m/%Y}' if o.fecha_estimada else '', len(cambios), cambios[-1].motivo if cambios else '',
            f'{timezone.localtime(o.entregada_en):%d/%m/%Y}' if o.entregada_en else '', o.cumplimiento,
        ])
    entregadas = [o for o in lista if o.entrega_a_tiempo is not None]
    a_tiempo = sum(1 for o in entregadas if o.entrega_a_tiempo)
    return Reporte('fechas', 'Fechas de entrega y cumplimiento',
                   ['Orden', 'Cliente', 'Equipo', 'Prometida', 'Vigente', 'Cambios', 'Último motivo', 'Entregada', 'Cumplimiento'], filas,
                   [('Órdenes del período', len(lista)), ('Con la fecha cambiada', sum(1 for f in filas if f[5])),
                    ('Entregadas a tiempo', f'{a_tiempo} de {len(entregadas)}')],
                   derecha=(0, 5), periodo=_periodo(d, h), truncado=corto)


# clave -> (título, descripción, función, ¿usa fechas?)
REPORTES = {
    'estado': ('Estado financiero', 'Ingresos, gastos, utilidad, caja y valor del inventario.', estado_financiero, True),
    'ingresos': ('Ingresos', 'Cada pago confirmado: efectivo y transferencias.', ingresos, True),
    'gastos': ('Gastos y pérdidas', 'Reposiciones, gastos del taller y pérdidas.', gastos, True),
    'ordenes': ('Órdenes', 'Equipos recibidos, su estado y lo que se facturó.', ordenes, True),
    'danados': ('Repuestos dañados', 'Qué se dañó, cuántos, el motivo, quién lo reportó y cuándo.', danados, True),
    'fechas': ('Fechas de entrega', 'Fecha prometida, cambios con su motivo y si se cumplió.', fechas, True),
    'inventario': ('Inventario', 'Stock actual, costo, precio y valor de cada producto.', inventario, False),
    'movimientos': ('Movimientos de inventario', 'Entradas, salidas y ajustes (kardex).', movimientos, True),
}
