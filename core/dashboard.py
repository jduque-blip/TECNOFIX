"""Datos del dashboard del administrador: indicadores y los once gráficos."""

from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from finanzas.models import Gasto
from ordenes import carga
from inventario.models import MovimientoInventario, Producto
from ordenes.models import Orden, Pago

from .models import Configuracion

MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
N_MESES = 6

# (id del gráfico, título, explicación corta) en el orden en que se muestran
TARJETAS = [
    ('ingresos_gastos', 'Ingresos y gastos por mes', 'Lo cobrado frente a lo que salió: gastos, reposiciones y pérdidas registradas.'),
    ('utilidad', 'Utilidad por mes', 'Ingresos menos lo que salió de caja.'),
    ('recibidas', 'Equipos recibidos por mes', 'Cuántas órdenes entraron al taller.'),
    ('estados', 'Órdenes por estado', 'Dónde están hoy todos los equipos.'),
    ('servicios', 'Servicios más pedidos', 'Los seis servicios con más órdenes.'),
    ('carga', 'Casos activos por técnico', 'Quién tiene más trabajo ahora.'),
    ('composicion', 'En qué se fue el dinero este mes', 'Gastos y pérdidas del mes actual.'),
    ('mas_usados', 'Repuestos más usados', 'Salidas del inventario en los últimos 90 días.'),
    ('stock_bajo', 'Productos con stock bajo', 'Stock actual frente al mínimo que definiste.'),
    ('metodos', 'Cómo pagan los clientes', 'Pagos confirmados: efectivo o transferencia.'),
    ('cumplimiento', 'Entregas a tiempo', 'Equipos entregados en o antes de la última fecha acordada con el cliente.'),
]


def _f(valor):
    return float(valor or 0)


def _ultimos_meses(hoy):
    """[(año, mes)] de los últimos seis meses, el actual al final."""
    año, mes = hoy.year, hoy.month
    salida = []
    for _ in range(N_MESES):
        salida.append((año, mes))
        mes -= 1
        if mes == 0:
            año, mes = año - 1, 12
    return salida[::-1]


def _por_mes(meses, filas):
    """filas: [(fecha, monto)] -> lista de totales alineada con `meses`."""
    acumulado = defaultdict(Decimal)
    for fecha, monto in filas:
        acumulado[(fecha.year, fecha.month)] += Decimal(monto)
    return [_f(acumulado[m]) for m in meses]


def datos():
    hoy = timezone.localdate()
    meses = _ultimos_meses(hoy)
    desde = hoy.replace(day=1)
    for _ in range(N_MESES - 1):
        desde = (desde - timedelta(days=1)).replace(day=1)
    etiquetas = [f'{MESES[m - 1]} {str(a)[2:]}' for a, m in meses]

    pagos = Pago.objects.filter(estado=Pago.Estado.CONFIRMADO)
    ingresos = _por_mes(meses, [(timezone.localtime(v).date(), m) for v, m in pagos.filter(validado_en__date__gte=desde).values_list('validado_en', 'monto')])
    # Dinero que sale: gastos y pérdidas manuales (las pérdidas de inventario ya se pagaron al comprar)
    salen = Gasto.objects.filter(fecha__gte=desde).filter(Q(tipo=Gasto.Tipo.GASTO) | Q(automatico=False))
    gastos = _por_mes(meses, salen.values_list('fecha', 'monto'))
    utilidad = [round(i - g, 2) for i, g in zip(ingresos, gastos)]
    recibidas = Counter()
    for fecha in Orden.objects.filter(creada_en__date__gte=desde).values_list('creada_en', flat=True):
        d = timezone.localtime(fecha)
        recibidas[(d.year, d.month)] += 1

    por_estado = dict(Orden.objects.values_list('estado').annotate(n=Count('id')))
    estados = [(v, n) for v, n in Orden.Estado.choices]
    por_servicio = list(Orden.objects.values_list('servicio__nombre').annotate(n=Count('id')).order_by('-n')[:6])
    tecnicos = [(u.nombre, u.activos) for u in carga.tecnicos_por_carga()][:8]
    mes_g = Gasto.objects.filter(fecha__year=hoy.year, fecha__month=hoy.month)
    suma = lambda q: _f(q.aggregate(t=Sum('monto'))['t'])
    composicion = [
        ('Reposiciones a proveedores', suma(mes_g.filter(tipo='GASTO', automatico=True))),
        ('Gastos del taller', suma(mes_g.filter(tipo='GASTO', automatico=False))),
        ('Pérdidas registradas', suma(mes_g.filter(tipo='PERDIDA', automatico=False))),
        ('Repuestos perdidos o dañados', suma(mes_g.filter(tipo='PERDIDA', automatico=True))),
    ]
    mas_usados = list(MovimientoInventario.objects.filter(
        tipo=MovimientoInventario.Tipo.SALIDA, fecha__date__gte=hoy - timedelta(days=90),
    ).values_list('producto__nombre').annotate(n=Sum('cantidad')).order_by('-n')[:5])
    bajos = list(Producto.objects.filter(activo=True, stock__lte=F('stock_minimo')).order_by('stock', 'nombre').values_list('nombre', 'stock', 'stock_minimo')[:8])
    metodos = dict(pagos.values_list('metodo').annotate(n=Count('id')))

    entregadas = list(Orden.objects.filter(estado=Orden.Estado.ENTREGADO, fecha_estimada__isnull=False, entregada_en__isnull=False))
    a_tiempo = sum(1 for o in entregadas if o.entrega_a_tiempo)
    mes_ingresos = ingresos[-1]
    capital = Configuracion.capital_inicial()
    todo_in = suma(pagos)
    todo_out = suma(Gasto.objects.filter(Q(tipo='GASTO') | Q(automatico=False)))
    kpis = {
        'activas': Orden.objects.exclude(estado__in=Orden.CERRADAS).count(),
        'por_cobrar': Orden.objects.filter(estado=Orden.Estado.LISTO).exclude(pagos__estado=Pago.Estado.CONFIRMADO).count(),
        'pagos_por_validar': Pago.objects.filter(estado=Pago.Estado.PENDIENTE).count(),
        'ingresos_mes': mes_ingresos,
        'utilidad_mes': utilidad[-1],
        'caja': round(_f(capital) + todo_in - todo_out, 2),
        'stock_bajo': Producto.objects.filter(activo=True, stock__lte=F('stock_minimo')).count(),
        'canceladas': Orden.objects.filter(Q(estado=Orden.Estado.CANCELADA) | Q(resultado=Orden.Estado.CANCELADA)).count(),
        'rechazadas': Orden.objects.filter(Q(estado=Orden.Estado.RECHAZADA) | Q(resultado=Orden.Estado.RECHAZADA)).count(),
        'sin_tecnico': Orden.objects.filter(tecnico__isnull=True).exclude(estado__in=Orden.CERRADAS).count(),
        'fecha_cambiada': Orden.objects.exclude(estado__in=Orden.CERRADAS).filter(cambios_fecha__isnull=False).distinct().count(),
    }
    graficos = {
        'ingresos_gastos': {'etiquetas': etiquetas, 'ingresos': ingresos, 'gastos': gastos},
        'utilidad': {'etiquetas': etiquetas, 'valores': utilidad},
        'recibidas': {'etiquetas': etiquetas, 'valores': [recibidas[m] for m in meses]},
        'estados': {'etiquetas': [n for _, n in estados], 'valores': [por_estado.get(v, 0) for v, _ in estados]},
        'servicios': {'etiquetas': [n for n, _ in por_servicio], 'valores': [c for _, c in por_servicio]},
        'composicion': {'etiquetas': [n for n, _ in composicion], 'valores': [v for _, v in composicion]},
        'carga': {'etiquetas': [n for n, _ in tecnicos], 'valores': [c for _, c in tecnicos]},
        'mas_usados': {'etiquetas': [n for n, _ in mas_usados], 'valores': [c for _, c in mas_usados]},
        'stock_bajo': {'etiquetas': [n for n, _, _ in bajos], 'stock': [s for _, s, _ in bajos], 'minimo': [m for _, _, m in bajos]},
        'metodos': {'etiquetas': ['Efectivo', 'Transferencia'], 'valores': [metodos.get('EFECTIVO', 0), metodos.get('TRANSFERENCIA', 0)]},
        'cumplimiento': {'etiquetas': ['A tiempo', 'Con retraso'], 'valores': [a_tiempo, len(entregadas) - a_tiempo]},
    }
    return kpis, graficos

