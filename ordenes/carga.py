"""Cuántos casos tiene cada técnico, para asignar al que esté más libre (asignación rotativa)."""

from django.db.models import Count, F, Max, Q

from usuarios.models import Usuario

from .models import Orden

E = Orden.Estado


def tecnicos_por_carga(incluir=None):
    """Técnicos activos (más el indicado), del que tiene menos casos al que tiene más.

    Cuentan como «activos» los casos que lo ocupan: recibidos, en diagnóstico y en reparación. Los que están
    listos esperan entrega y no lo ocupan. Si empatan, va primero quien lleva más tiempo sin recibir un caso."""
    filtro = Q(rol=Usuario.Rol.TECNICO, is_active=True)
    if incluir:
        filtro |= Q(pk=incluir)
    return (
        Usuario.objects.filter(filtro)
        .annotate(
            n_diagnostico=Count('ordenes_como_tecnico', filter=Q(ordenes_como_tecnico__estado=E.DIAGNOSTICO)),
            n_reparacion=Count('ordenes_como_tecnico', filter=Q(ordenes_como_tecnico__estado=E.REPARACION)),
            n_recibido=Count('ordenes_como_tecnico', filter=Q(ordenes_como_tecnico__estado=E.RECIBIDO)),
            n_listo=Count('ordenes_como_tecnico', filter=Q(ordenes_como_tecnico__estado=E.LISTO)),
            ultima_asignacion=Max('ordenes_como_tecnico__asignada_en'),
        )
        .annotate(activos=F('n_diagnostico') + F('n_reparacion') + F('n_recibido'))
        .order_by('activos', 'n_listo', F('ultima_asignacion').asc(nulls_first=True), 'nombre')
    )


def detalle(u):
    """Texto corto con lo que está haciendo el técnico: «2 casos: 1 en diagnóstico, 1 en reparación»."""
    partes = []
    if u.n_recibido:
        partes.append(f'{u.n_recibido} recibido{"s" if u.n_recibido != 1 else ""}')
    if u.n_diagnostico:
        partes.append(f'{u.n_diagnostico} en diagnóstico')
    if u.n_reparacion:
        partes.append(f'{u.n_reparacion} en reparación')
    if u.n_listo:
        partes.append(f'{u.n_listo} listo{"s" if u.n_listo != 1 else ""} para entrega')
    if not u.activos and not u.n_listo:
        return 'libre'
    cabecera = f'{u.activos} caso{"s" if u.activos != 1 else ""} activo{"s" if u.activos != 1 else ""}' if u.activos else 'sin casos activos'
    return f'{cabecera}: ' + ', '.join(partes)
