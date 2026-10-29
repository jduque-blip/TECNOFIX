"""Notificaciones y chat interno."""

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ordenes.models import Orden
from usuarios.models import Usuario

from .forms import MensajeForm
from .models import Mensaje, Notificacion
from .permisos import contactos, puede_chatear

MOSTRAR = 100


# ---------- Notificaciones ----------

@login_required
def notificaciones(request):
    lista = request.user.notificaciones.all()
    pagina = Paginator(lista, 20).get_page(request.GET.get('pagina'))
    return render(request, 'comunicacion/notificaciones.html', {'pagina': pagina})


@login_required
def notificacion_abrir(request, pk):
    """Marca el aviso como leído y lleva a lo que anuncia."""
    n = get_object_or_404(Notificacion, pk=pk, usuario=request.user)
    if not n.leida:
        n.leida = True
        n.save(update_fields=['leida'])
    pedido = re.search(r'/(?:equipos|panel/ordenes)/(\d+)/', n.enlace or '')
    if pedido and not Orden.objects.filter(pk=int(pedido.group(1))).exists():
        messages.info(request, f'Esa solicitud (#{pedido.group(1)}) ya no existe: el cliente la eliminó.')
        return redirect('notificaciones')
    if n.enlace and url_has_allowed_host_and_scheme(n.enlace, allowed_hosts={request.get_host()}):
        return redirect(n.enlace)
    return redirect('notificaciones')


@login_required
@require_POST
def notificaciones_leer(request):
    request.user.notificaciones.filter(leida=False).update(leida=True)
    return redirect('notificaciones')


# ---------- Chat ----------

def _conversaciones(yo):
    """Una fila por persona con quien hay mensajes: último mensaje y cuántos faltan por leer."""
    mios = Mensaje.objects.filter(Q(de=yo) | Q(para=yo)).select_related('de', 'para').order_by('-creado_en')
    filas, vistos = [], set()
    for m in mios:
        otro = m.para if m.de_id == yo.pk else m.de
        if otro.pk in vistos:
            continue
        vistos.add(otro.pk)
        filas.append({'otro': otro, 'ultimo': m})
    sin_leer = dict(
        Mensaje.objects.filter(para=yo, leido=False).values_list('de').annotate(n=Count('pk'))
    )
    for f in filas:
        f['sin_leer'] = sin_leer.get(f['otro'].pk, 0)
    return filas


@login_required
def bandeja(request):
    filas = _conversaciones(request.user)
    con_chat = {f['otro'].pk for f in filas}
    nuevos = [u for u in contactos(request.user) if u.pk not in con_chat]
    return render(request, 'comunicacion/bandeja.html', {'filas': filas, 'nuevos': nuevos})


def _burbuja(m, yo):
    return {
        'id': m.pk, 'mio': m.de_id == yo.pk, 'texto': m.texto,
        'hora': timezone.localtime(m.creado_en).strftime('%d/%m %H:%M'),
        'orden': f'#{m.orden_id} · {m.orden.modelo}' if m.orden_id else '',
    }


@login_required
def chat(request, pk):
    otro = get_object_or_404(Usuario, pk=pk)
    if not puede_chatear(request.user, otro):
        messages.error(request, 'No puedes escribirle a esa persona.')
        return redirect('chat_bandeja')
    orden = None
    orden_id = request.POST.get('orden') or request.GET.get('orden')
    if orden_id and orden_id.isdigit():
        orden = Orden.objects.select_related().filter(pk=orden_id).first()
        # La orden solo se vincula si ambas personas tienen que ver con ella (el administrador siempre)
        if orden:
            involucrados = {orden.cliente_id, orden.tecnico_id}
            if not all(u.es_admin or u.pk in involucrados for u in (request.user, otro)):
                orden = None
    if request.method == 'POST':
        form = MensajeForm(request.POST, auto_id='msg_%s')
        if form.is_valid():
            Mensaje.objects.create(de=request.user, para=otro, orden=orden, texto=form.cleaned_data['texto'])
            return redirect(f"{request.path}{'?orden=%d' % orden.pk if orden else ''}")
    else:
        form = MensajeForm(auto_id='msg_%s')
    hilo = Mensaje.objects.filter(
        Q(de=request.user, para=otro) | Q(de=otro, para=request.user)
    ).select_related('orden').order_by('-creado_en')[:MOSTRAR]
    hilo = list(reversed(hilo))
    Mensaje.objects.filter(de=otro, para=request.user, leido=False).update(leido=True)
    return render(request, 'comunicacion/chat.html', {
        'otro': otro, 'orden': orden, 'form': form,
        'mensajes': [_burbuja(m, request.user) for m in hilo],
        'ultimo': hilo[-1].pk if hilo else 0,
    })


@login_required
def chat_nuevos(request, pk):
    """Mensajes posteriores al id indicado; la pantalla los pide cada pocos segundos."""
    otro = get_object_or_404(Usuario, pk=pk)
    if not puede_chatear(request.user, otro):
        raise Http404
    despues = request.GET.get('despues', '0')
    despues = int(despues) if despues.isdigit() else 0
    nuevos = Mensaje.objects.filter(
        Q(de=request.user, para=otro) | Q(de=otro, para=request.user), pk__gt=despues
    ).select_related('orden').order_by('creado_en')
    lista = list(nuevos)
    Mensaje.objects.filter(de=otro, para=request.user, leido=False).update(leido=True)
    return JsonResponse({'mensajes': [_burbuja(m, request.user) for m in lista]})
