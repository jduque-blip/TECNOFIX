"""Con quién puede hablar cada rol. Nadie escribe a quien no le corresponde."""

from django.db.models import Q

from ordenes.models import Orden
from usuarios.models import Usuario

from .avisos import administradores


def puede_chatear(yo, otro):
    if not otro.is_active or yo.pk == otro.pk:
        return False
    roles = {yo.rol, otro.rol}
    if Usuario.Rol.ADMIN in roles:
        # El administrador habla con técnicos y clientes, y ellos con él; los administradores no se escriben entre sí
        return roles != {Usuario.Rol.ADMIN}
    if roles == {Usuario.Rol.CLIENTE, Usuario.Rol.TECNICO}:
        cliente, tecnico = (yo, otro) if yo.es_cliente else (otro, yo)
        return Orden.objects.filter(cliente=cliente, tecnico=tecnico).exists()
    return False


def contactos(yo):
    """Personas con quienes puede iniciar una conversación."""
    if yo.es_admin:
        return Usuario.objects.filter(rol__in=[Usuario.Rol.CLIENTE, Usuario.Rol.TECNICO], is_active=True).order_by('rol', 'nombre')
    ids = list(administradores().values_list('pk', flat=True))
    if yo.es_cliente:
        ids += list(Orden.objects.filter(cliente=yo, tecnico__isnull=False).values_list('tecnico_id', flat=True))
    elif yo.es_tecnico:
        ids += list(Orden.objects.filter(tecnico=yo).values_list('cliente_id', flat=True))
    return Usuario.objects.filter(pk__in=set(ids), is_active=True).order_by('-rol', 'nombre')


def contacto_para_orden(yo, orden):
    """A quién escribir sobre una orden. El cliente habla con su técnico o, si aún no tiene, con administración."""
    if yo.es_cliente:
        if orden.congelada:
            return administradores().first()  # cancelada o rechazada: negocia con administración
        return orden.tecnico if orden.tecnico and orden.tecnico.is_active else administradores().first()
    if yo.es_tecnico or yo.es_admin:
        return orden.cliente if orden.cliente.is_active else None
    return None
