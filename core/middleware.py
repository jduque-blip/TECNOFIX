from django.conf import settings
from django.shortcuts import render

# Rutas de equipos y órdenes: si no existen es porque se eliminaron
RUTAS_DE_SOLICITUDES = ('/equipos/', '/panel/ordenes/', '/chat/')


class PaginaAmable:
    """Nunca se muestra la pantalla fría de «página no encontrada»: se explica con una página de TecnoFix y un botón para volver."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        respuesta = self.get_response(request)
        if respuesta.status_code != 404 or request.path.startswith((settings.STATIC_URL, settings.MEDIA_URL)):
            return respuesta
        if 'text/html' not in respuesta.get('Content-Type', 'text/html'):
            return respuesta
        eliminada = request.path.startswith(RUTAS_DE_SOLICITUDES) and any(c.isdigit() for c in request.path)
        return render(request, 'core/no_existe.html', {'eliminada': eliminada}, status=404)


def amable(vista):
    """Para las pantallas de un equipo: si ya no existe (se eliminó) muestra la página amable aunque no esté activado el middleware."""
    from functools import wraps

    from django.http import Http404

    @wraps(vista)
    def envuelta(request, *args, **kwargs):
        try:
            return vista(request, *args, **kwargs)
        except Http404:
            return render(request, 'core/no_existe.html', {'eliminada': True}, status=404)
    return envuelta
