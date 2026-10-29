from .models import Mensaje


def contadores(request):
    """Cuántas notificaciones y mensajes sin leer tiene quien está dentro; se muestran en la barra."""
    u = request.user
    if not u.is_authenticated:
        return {}
    return {
        'n_notif': u.notificaciones.filter(leida=False).count(),
        'n_chat': Mensaje.objects.filter(para=u, leido=False).count(),
    }
