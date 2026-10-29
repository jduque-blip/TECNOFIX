"""Fecha de entrega: la primera que se le promete al cliente y cada vez que se mueve, con su motivo."""

from .models import CambioFecha


def cambiar(orden, nueva, motivo, usuario):
    """Pone la nueva fecha estimada. Si ya había una, deja el cambio anotado con el motivo.

    Quien llama guarda la orden con update_fields=['fecha_estimada', 'fecha_prometida']. Devuelve True si cambió."""
    anterior = orden.fecha_estimada
    if nueva == anterior:
        return False
    if anterior is not None:
        CambioFecha.objects.create(
            orden=orden, fecha_anterior=anterior, fecha_nueva=nueva, usuario=usuario,
            motivo=' '.join((motivo or 'Sin motivo indicado').split())[:200],
        )
    elif orden.fecha_prometida is None:
        orden.fecha_prometida = nueva  # la primera fecha que se le dijo al cliente
    orden.fecha_estimada = nueva
    return True
