from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ordenes.models import Orden, Pago
from usuarios.permisos import solo_admin

from . import datos, salida


@solo_admin
def indice(request):
    try:
        desde, hasta = datos.leer_rango(request.GET.get('desde', ''), request.GET.get('hasta', ''))
    except datos.RangoError as e:
        messages.error(request, str(e))
        return redirect('reportes')
    return render(request, 'reportes/index.html', {
        'tab': 'rep', 'desde': desde.isoformat(), 'hasta': hasta.isoformat(), 'hoy': timezone.localdate().isoformat(),
        'reportes': [(k, v[0], v[1], v[3]) for k, v in datos.REPORTES.items()],
    })


@solo_admin
def descargar(request, clave, formato):
    if clave not in datos.REPORTES or formato not in ('pdf', 'csv'):
        raise Http404
    try:
        desde, hasta = datos.leer_rango(request.GET.get('desde', ''), request.GET.get('hasta', ''))
    except datos.RangoError as e:
        messages.error(request, str(e))
        return redirect('reportes')
    reporte = datos.REPORTES[clave][2](desde, hasta)
    nombre = f'tecnofix-{clave}-{desde:%Y%m%d}-{hasta:%Y%m%d}' if datos.REPORTES[clave][3] else f'tecnofix-{clave}-{timezone.localdate():%Y%m%d}'
    return salida.pdf_respuesta(reporte, nombre) if formato == 'pdf' else salida.csv_respuesta(reporte, nombre)


@login_required
def factura(request, pk):
    """Factura general de una orden: el administrador y el cliente dueño. El técnico no la ve."""
    orden = get_object_or_404(Orden.objects.select_related('cliente'), pk=pk)
    if not (request.user.es_admin or (request.user.es_cliente and orden.cliente_id == request.user.pk)):
        raise Http404
    f = orden.factura
    if not f['lineas']:
        messages.error(request, 'Esta orden todavía no tiene nada facturado.')
        return redirect('equipo', pk=pk)
    return salida.factura_pdf(orden, f, orden.pagos.filter(estado=Pago.Estado.CONFIRMADO).exists())
