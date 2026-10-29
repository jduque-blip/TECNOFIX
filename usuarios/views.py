from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render, resolve_url
from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme

from core.views import contexto_inicio

from django.core.cache import cache

from comunicacion import avisos

from .forms import CambioContrasenaForm, IngresoForm, OlvideContrasenaForm, RegistroForm
from .models import Usuario


def _destino(request):
    """A dónde ir tras ingresar. Solo se acepta una dirección de este mismo sitio."""
    siguiente = request.POST.get('next') or request.GET.get('next') or ''
    seguro = url_has_allowed_host_and_scheme(
        siguiente, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return siguiente if siguiente and seguro else resolve_url(settings.LOGIN_REDIRECT_URL)


def ingresar(request):
    if request.user.is_authenticated:
        return redirect('panel')
    if request.method == 'POST':
        form = IngresoForm(request.POST, request=request, auto_id='ing_%s')
        if form.is_valid():
            login(request, form.usuario)
            if form.usuario.debe_cambiar_contrasena:
                return redirect('cambiar_contrasena')
            return redirect(_destino(request))
    else:
        form = IngresoForm(request=request, auto_id='ing_%s')
    contexto = contexto_inicio(
        form_ingreso=form,
        modal_abierto='modal-ingresar',
        siguiente=request.POST.get('next') or request.GET.get('next') or '',
    )
    return render(request, 'core/inicio.html', contexto)


def registro(request):
    if request.user.is_authenticated:
        return redirect('panel')
    if request.method == 'POST':
        form = RegistroForm(request.POST, auto_id='reg_%s')
        if form.is_valid():
            usuario = form.guardar()
            if usuario:
                login(request, usuario)
                messages.success(request, '¡Registro exitoso!')
                return redirect('panel')
    else:
        form = RegistroForm(auto_id='reg_%s')
    contexto = contexto_inicio(form_registro=form, modal_abierto='modal-registro')
    return render(request, 'core/inicio.html', contexto)


MAX_PEDIDOS_EQUIPO = 10       # solicitudes por hora desde un mismo equipo
ESPERA_AVISO = 30 * 60        # no se repite el aviso del mismo usuario durante 30 minutos


def olvide_contrasena(request):
    """El usuario no puede entrar: se avisa al administrador, que genera una clave nueva.

    La respuesta es la misma exista o no el correo, para no revelar quién tiene cuenta.
    """
    if request.user.is_authenticated:
        return redirect('panel')
    if request.method == 'POST':
        form = OlvideContrasenaForm(request.POST, auto_id='olv_%s')
        if form.is_valid():
            equipo = f"olvide:equipo:{request.META.get('REMOTE_ADDR', 'desconocida')}"
            cache.add(equipo, 0, 3600)
            if cache.incr(equipo) <= MAX_PEDIDOS_EQUIPO:
                usuario = Usuario.objects.filter(
                    email__iexact=form.cleaned_data['email'], is_active=True,
                ).exclude(rol=Usuario.Rol.ADMIN).first()
                if usuario and cache.add(f'olvide:usuario:{usuario.pk}', 1, ESPERA_AVISO):
                    avisos.contrasena_olvidada(usuario)
            messages.success(
                request,
                'Listo. Si el correo está registrado, avisamos al administrador para que te dé una contraseña nueva. '
                'Te la entregará por WhatsApp.',
            )
            return redirect('inicio')
    else:
        form = OlvideContrasenaForm(auto_id='olv_%s')
    return render(request, 'core/inicio.html', contexto_inicio(form_olvide=form, modal_abierto='modal-olvide'))


@login_required
def cambiar_contrasena(request):
    if request.method == 'POST':
        form = CambioContrasenaForm(request.user, request, request.POST, auto_id='cc_%s')
        if form.is_valid():
            usuario = form.guardar()
            # Sin esto, cambiar la contraseña cerraría la sesión actual
            update_session_auth_hash(request, usuario)
            messages.success(request, 'Contraseña actualizada')
            return redirect('panel')
    else:
        form = CambioContrasenaForm(request.user, request, auto_id='cc_%s')
    return render(request, 'cuenta/contrasena.html', {'form': form})
