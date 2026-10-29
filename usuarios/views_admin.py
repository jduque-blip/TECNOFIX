"""Gestión de personal (técnicos) y clientes, solo para el administrador."""

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ordenes.models import Orden

from . import claves
from .forms import RestablecerContrasenaForm, TecnicoForm
from .models import Usuario
from .permisos import solo_admin

POR_PAGINA = 10


def _buscar(request, consulta, campos):
    """Filtra por el texto de ?q= en los campos indicados."""
    texto = request.GET.get('q', '').strip()
    if texto:
        filtro = Q()
        for campo in campos:
            filtro |= Q(**{f'{campo}__icontains': texto})
        consulta = consulta.filter(filtro)
    return consulta, texto


def _pagina(request, consulta):
    return Paginator(consulta, POR_PAGINA).get_page(request.GET.get('pagina'))


@solo_admin
def personal(request):
    if request.method == 'POST':
        form = TecnicoForm(request.POST, auto_id='tec_%s')
        if form.is_valid():
            creado = form.guardar()
            if creado:
                tecnico, clave = creado
                return _mostrar_credencial(request, tecnico, clave)
    else:
        form = TecnicoForm(auto_id='tec_%s')
    tecnicos = Usuario.objects.filter(rol=Usuario.Rol.TECNICO).annotate(
        activos=Count('ordenes_como_tecnico', filter=~Q(ordenes_como_tecnico__estado__in=Orden.CERRADAS))
    ).order_by('nombre')
    tecnicos, texto = _buscar(request, tecnicos, ['nombre', 'email'])
    return render(request, 'panel/personal.html', {
        'tab': 'per', 'form': form, 'pagina': _pagina(request, tecnicos), 'q': texto,
    })


@solo_admin
def clientes(request):
    clientes = Usuario.objects.filter(rol=Usuario.Rol.CLIENTE).annotate(servicios=Count('ordenes_como_cliente')).order_by('nombre')
    clientes, texto = _buscar(request, clientes, ['nombre', 'email', 'telefono'])
    return render(request, 'panel/clientes.html', {
        'tab': 'cli', 'pagina': _pagina(request, clientes), 'q': texto,
    })


def _volver(request, usuario):
    """Regresa a la lista de donde se hizo clic, solo si es una dirección de este sitio."""
    destino = request.POST.get('volver', '')
    if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        return redirect(destino)
    return redirect('personal' if usuario.es_tecnico else 'clientes')


def _gestionable(pk):
    # Desde estas pantallas solo se gestionan técnicos y clientes, nunca administradores
    return get_object_or_404(Usuario, pk=pk, rol__in=[Usuario.Rol.TECNICO, Usuario.Rol.CLIENTE])


def _liberar_ordenes(request, tecnico):
    """Un técnico desactivado no puede atender: sus órdenes abiertas quedan sin técnico y se avisa al administrador para reasignarlas."""
    from comunicacion import avisos

    with transaction.atomic():
        abiertas = list(Orden.objects.select_for_update(of=('self',)).filter(tecnico=tecnico).exclude(estado__in=Orden.CERRADAS))
        for o in abiertas:
            o.tecnico, o.asignada_en = None, None
            o.save(update_fields=['tecnico', 'asignada_en'])
            o.registrar(f'Quedó sin técnico: {tecnico.nombre} fue desactivado. Hay que asignar uno nuevo', request.user)
    if abiertas:
        numeros = ', '.join(f'#{o.pk}' for o in abiertas[:8]) + ('…' if len(abiertas) > 8 else '')
        avisos.avisar_admins(f'Desactivaste a {tecnico.nombre}: {len(abiertas)} orden{"es" if len(abiertas) != 1 else ""} quedó sin técnico ({numeros}). Asígnalas a otro técnico.',
                             reverse('ordenes'))
        messages.warning(request, f'{len(abiertas)} orden(es) de {tecnico.nombre} quedaron sin técnico. Reasígnalas desde Órdenes.')


@solo_admin
@require_POST
def usuario_estado(request, pk):
    """Activa o desactiva una cuenta. Una cuenta desactivada no puede ingresar, pero conserva su historial."""
    usuario = _gestionable(pk)
    usuario.is_active = not usuario.is_active
    usuario.save(update_fields=['is_active'])
    messages.success(request, f'{usuario.nombre} fue {"activado" if usuario.is_active else "desactivado"}')
    if usuario.es_tecnico and not usuario.is_active:
        _liberar_ordenes(request, usuario)
    return _volver(request, usuario)


@solo_admin
@require_POST
def usuario_eliminar(request, pk):
    usuario = _gestionable(pk)
    if usuario.es_tecnico:
        activas = usuario.ordenes_como_tecnico.exclude(estado__in=Orden.CERRADAS).count()
        if activas:
            messages.error(request, f'{usuario.nombre} tiene {activas} servicio(s) activo(s). Reasígnalos o desactiva la cuenta.')
            return _volver(request, usuario)
    try:
        with transaction.atomic():
            usuario.delete()
    except ProtectedError:
        # Tiene servicios o registros ligados: se conserva el historial y solo se desactiva
        messages.error(request, f'{usuario.nombre} tiene historial y no se puede eliminar. Puedes desactivar la cuenta.')
    else:
        messages.success(request, 'Usuario eliminado')
    return _volver(request, usuario)


def _mostrar_credencial(request, usuario, clave):
    """La clave temporal se muestra una sola vez: se guarda en la sesión y la pantalla la consume."""
    request.session['credencial'] = {'pk': usuario.pk, 'clave': clave}
    return redirect('usuario_credencial', pk=usuario.pk)


@solo_admin
def credencial(request, pk):
    """Muestra la contraseña temporal recién asignada, con copiar y envío por WhatsApp."""
    usuario = _gestionable(pk)
    guardada = request.session.pop('credencial', None)
    if not guardada or guardada.get('pk') != usuario.pk:
        messages.info(request, 'Esa contraseña ya no se puede mostrar. Genera una nueva si hace falta.')
        return redirect('usuario_contrasena', pk=usuario.pk)
    clave = guardada['clave']
    return render(request, 'panel/credencial.html', {
        'tab': 'per' if usuario.es_tecnico else 'cli', 'persona': usuario, 'clave': clave,
        'whatsapp': claves.enlace_whatsapp(usuario, clave),
    })


@solo_admin
def restablecer_contrasena(request, pk):
    """Asigna una contraseña temporal: la persona debe cambiarla en cuanto ingrese."""
    usuario = _gestionable(pk)
    if request.method == 'POST':
        form = RestablecerContrasenaForm(usuario, request.POST, auto_id='rc_%s')
        if form.is_valid():
            clave = form.guardar()
            return _mostrar_credencial(request, usuario, clave)
    else:
        form = RestablecerContrasenaForm(
            usuario, initial={'password': claves.generar_clave(), 'telefono': usuario.telefono}, auto_id='rc_%s'
        )
    return render(request, 'panel/restablecer.html', {
        'tab': 'per' if usuario.es_tecnico else 'cli', 'form': form, 'persona': usuario,
        'clave_inicial': form['password'].value() if request.method != 'POST' else '',
    })
