import re
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import F, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Configuracion
from inventario.models import Producto
from ordenes.models import Pago
from usuarios.permisos import solo_admin

from .forms import GastoForm
from .models import Gasto

POR_PAGINA = 15


def _mes(texto):
    """'2026-10' -> (2026, 10); 'todos' -> None; vacío o inválido -> el mes actual."""
    hoy = timezone.localdate()
    if texto == 'todos':
        return None
    m = re.fullmatch(r'(\d{4})-(\d{2})', texto or '')
    if m and 1 <= int(m.group(2)) <= 12 and 2000 <= int(m.group(1)) <= 2100:
        return int(m.group(1)), int(m.group(2))
    return hoy.year, hoy.month


def _error(form):
    for errores in form.errors.values():
        return errores[0]
    return 'Datos no válidos.'


@solo_admin
def gastos(request):
    mes = _mes(request.GET.get('mes', ''))
    tipo = request.GET.get('tipo', '')
    if tipo not in Gasto.Tipo.values:
        tipo = ''
    base = Gasto.objects.select_related('usuario', 'pedido')
    pagos = Pago.objects.filter(estado=Pago.Estado.CONFIRMADO)
    if mes:
        base = base.filter(fecha__year=mes[0], fecha__month=mes[1])
        pagos = pagos.filter(validado_en__year=mes[0], validado_en__month=mes[1])
    # Dinero que realmente sale: gastos (incluye las reposiciones) y pérdidas manuales.
    # Las pérdidas automáticas (faltantes, repuestos dañados) son mercadería que ya se pagó al comprarla: solo informan.
    suma = lambda q: q.aggregate(t=Sum('monto'))['t'] or 0
    total_gastos = suma(base.filter(tipo=Gasto.Tipo.GASTO))
    perdidas_manuales = suma(base.filter(tipo=Gasto.Tipo.PERDIDA, automatico=False))
    perdidas_inventario = suma(base.filter(tipo=Gasto.Tipo.PERDIDA, automatico=True))
    ingresos = suma(pagos)
    salidas = total_gastos + perdidas_manuales
    # Caja: todo el historial, no solo el mes elegido
    capital = Configuracion.capital_inicial()
    ingresos_total = suma(Pago.objects.filter(estado=Pago.Estado.CONFIRMADO))
    salidas_total = suma(Gasto.objects.filter(tipo=Gasto.Tipo.GASTO)) + suma(Gasto.objects.filter(tipo=Gasto.Tipo.PERDIDA, automatico=False))
    caja = capital + ingresos_total - salidas_total
    valor_inventario = Producto.objects.filter(activo=True).aggregate(t=Sum(F('stock') * F('costo_pedido')))['t'] or 0
    lista = base.filter(tipo=tipo) if tipo else base
    pagina = Paginator(lista, POR_PAGINA).get_page(request.GET.get('pagina'))
    mes_texto = f'{mes[0]}-{mes[1]:02d}' if mes else 'todos'
    return render(request, 'finanzas/gastos.html', {
        'tab': 'fin', 'pagina': pagina, 'mes': mes_texto, 'tipo': tipo, 'tipos': Gasto.Tipo.choices,
        'total_gastos': total_gastos, 'perdidas_manuales': perdidas_manuales, 'perdidas_inventario': perdidas_inventario,
        'ingresos': ingresos, 'utilidad': ingresos - salidas,
        'capital': capital, 'caja': caja, 'valor_inventario': valor_inventario,
        'form': GastoForm(initial={'fecha': timezone.localdate()}, auto_id='gasto_%s'),
        'extra': f'mes={mes_texto}' + (f'&tipo={tipo}' if tipo else ''),
    })


@solo_admin
@require_POST
def gasto_nuevo(request):
    form = GastoForm(request.POST)
    if form.is_valid():
        g = form.save(commit=False)
        g.usuario = request.user
        g.save()
        messages.success(request, f'{g.get_tipo_display()} registrado: {g.concepto} (${g.monto})')
    else:
        messages.error(request, _error(form))
    return redirect('gastos')


@solo_admin
def gasto_editar(request, pk):
    gasto = get_object_or_404(Gasto, pk=pk, automatico=False)
    form = GastoForm(request.POST or None, instance=gasto, auto_id='gasto_%s')
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Registro actualizado')
        return redirect('gastos')
    return render(request, 'finanzas/gasto_form.html', {'tab': 'fin', 'form': form, 'gasto': gasto})


@solo_admin
@require_POST
def gasto_eliminar(request, pk):
    gasto = get_object_or_404(Gasto, pk=pk)
    if gasto.automatico:
        messages.error(request, 'Este registro lo creó el sistema (reposición o ajuste de inventario) y no se borra.')
    else:
        gasto.delete()
        messages.success(request, 'Registro eliminado')
    return redirect('gastos')


@solo_admin
@require_POST
def capital_guardar(request):
    texto = request.POST.get('capital', '').replace(',', '.').strip()
    try:
        valor = Decimal(texto)
        if not valor.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        messages.error(request, 'Escribe el capital como un número, por ejemplo 3000.')
        return redirect('gastos')
    if valor < 0 or valor > Decimal('99999999'):
        messages.error(request, 'El capital debe estar entre 0 y 99 999 999.')
    else:
        Configuracion.guardar_capital(valor.quantize(Decimal('0.01')))
        messages.success(request, f'Capital inicial guardado: ${valor.quantize(Decimal("0.01"))}')
    return redirect('gastos')
