"""Quién recibe qué aviso. Las pantallas llaman a estas funciones y no crean notificaciones por su cuenta."""

from decimal import Decimal

from django.urls import reverse

from usuarios.models import Usuario

from .models import Notificacion


def administradores():
    return Usuario.objects.filter(rol=Usuario.Rol.ADMIN, is_active=True).order_by('pk')


def avisar_admins(texto, enlace=''):
    for admin in administradores():
        Notificacion.avisar(admin, texto, enlace)


def nueva_solicitud(orden):
    avisar_admins(
        f'Nueva solicitud #{orden.pk} de {orden.cliente.nombre}: {orden.modelo}',
        reverse('orden_admin', args=[orden.pk]),
    )


def solicitud_corregida(orden):
    avisar_admins(f'{orden.cliente.nombre} corrigió su solicitud #{orden.pk} ({orden.modelo})', reverse('orden_admin', args=[orden.pk]))


def solicitud_eliminada(orden_pk, modelo, cliente):
    avisar_admins(f'{cliente.nombre} eliminó su solicitud #{orden_pk} ({modelo})', reverse('ordenes'))


def orden_gestionada(orden, tecnico_anterior, estado_cambio, fecha_cambio):
    """El administrador cambió algo de la orden: se avisa a quien le afecta."""
    enlace = reverse('equipo', args=[orden.pk])
    if orden.tecnico_id != (tecnico_anterior.pk if tecnico_anterior else None):
        if tecnico_anterior:
            Notificacion.avisar(tecnico_anterior, f'Ya no tienes asignada la orden #{orden.pk} ({orden.modelo})')
        if orden.tecnico:
            Notificacion.avisar(orden.tecnico, f'Se te asignó la orden #{orden.pk}: {orden.modelo}', enlace)
            Notificacion.avisar(orden.cliente, f'Tu equipo #{orden.pk} ya tiene técnico: {orden.tecnico.nombre}', enlace)
    if estado_cambio:
        Notificacion.avisar(orden.cliente, f'Tu equipo #{orden.pk} ahora está: {orden.get_estado_display()}', enlace)
        if orden.tecnico:
            Notificacion.avisar(orden.tecnico, f'La orden #{orden.pk} cambió a: {orden.get_estado_display()}', enlace)
    if fecha_cambio and orden.fecha_estimada:
        Notificacion.avisar(orden.cliente, f'Entrega estimada de tu equipo #{orden.pk}: {orden.fecha_estimada:%d/%m/%Y}', enlace)


def pedido_no_aceptado(orden, motivo):
    """El cliente rechazó un pedido adicional pero la orden sigue con lo que ya aceptó."""
    enlace_admin = reverse('orden_admin', args=[orden.pk])
    avisar_admins(f'El cliente no aceptó un pedido de la orden #{orden.pk} ({orden.modelo}): {motivo}. La orden sigue con lo ya aceptado.', enlace_admin)
    if orden.tecnico:
        Notificacion.avisar(orden.tecnico, f'El cliente no aceptó un pedido de la orden #{orden.pk}. Trabaja solo con lo que ya aceptó.', reverse('equipo', args=[orden.pk]))


def servicio_corregido(orden, anterior):
    """El administrador corrigió el servicio que el cliente había elegido."""
    texto = f'El servicio de tu equipo #{orden.pk} se corrigió: de «{anterior.nombre}» a «{orden.servicio.nombre}».'
    Notificacion.avisar(orden.cliente, texto, reverse('equipo', args=[orden.pk]))
    if orden.tecnico:
        Notificacion.avisar(orden.tecnico, f'En la orden #{orden.pk} el servicio cambió de «{anterior.nombre}» a «{orden.servicio.nombre}».', reverse('equipo', args=[orden.pk]))


def estado_por_tecnico(orden):
    enlace = reverse('equipo', args=[orden.pk])
    texto = f'Tu equipo #{orden.pk} ahora está: {orden.get_estado_display()}'
    if orden.estado == orden.Estado.LISTO:
        total = orden.total_a_pagar
        texto = f'¡Tu equipo #{orden.pk} está listo!' + (f' Total a pagar: ${total}.' if total else '') + ' Coordina el pago y el retiro con el taller.'
    Notificacion.avisar(orden.cliente, texto, enlace)
    if orden.estado == orden.Estado.LISTO:
        avisar_admins(f'La orden #{orden.pk} ({orden.modelo}) está lista para entrega', reverse('orden_admin', args=[orden.pk]))
    else:
        avisar_admins(f'{orden.tecnico.nombre} pasó la orden #{orden.pk} a: {orden.get_estado_display()}', reverse('orden_admin', args=[orden.pk]))


def nota_para_cliente(orden):
    Notificacion.avisar(orden.cliente, f'Hay una nota nueva sobre tu equipo #{orden.pk}', reverse('equipo', args=[orden.pk]))


def fotos_de_avance(orden):
    Notificacion.avisar(orden.cliente, f'El técnico subió fotos de tu equipo #{orden.pk}', reverse('equipo', args=[orden.pk]))


def contrasena_olvidada(usuario):
    """Un cliente o técnico dice que olvidó su contraseña: el administrador la restablece."""
    avisar_admins(
        f'{usuario.nombre} ({usuario.get_rol_display()}) olvidó su contraseña. Genera una nueva.',
        reverse('usuario_contrasena', args=[usuario.pk]),
    )


def stock_bajo(producto):
    """Un producto llegó a su stock mínimo o se agotó: el administrador debe reponerlo."""
    if producto.stock == 0:
        texto = f'Agotado: {producto.nombre} ya no tiene unidades (mínimo {producto.stock_minimo})'
    else:
        texto = f'Stock bajo: {producto.nombre} ({producto.stock} en existencia, mínimo {producto.stock_minimo})'
    avisar_admins(texto, reverse('producto_detalle', args=[producto.pk]))


def presupuesto_enviado(presupuesto):
    o = presupuesto.orden
    Notificacion.avisar(o.cliente, f'Tienes un presupuesto por ${presupuesto.total} para tu equipo #{o.pk}. Revísalo y respóndelo.', reverse('equipo', args=[o.pk]))


def presupuesto_respondido(presupuesto):
    o = presupuesto.orden
    resultado = 'aceptó' if presupuesto.estado == presupuesto.Estado.ACEPTADO else 'rechazó'
    texto = f'{o.cliente.nombre} {resultado} el presupuesto #{presupuesto.pk} de la orden #{o.pk} (${presupuesto.total})'
    if o.tecnico:
        Notificacion.avisar(o.tecnico, texto, reverse('equipo', args=[o.pk]))
    avisar_admins(texto, reverse('orden_admin', args=[o.pk]))


def presupuesto_sin_stock(presupuesto):
    o = presupuesto.orden
    texto = f'{o.cliente.nombre} quiso aceptar el presupuesto #{presupuesto.pk} (orden #{o.pk}) pero ya no hay stock de un repuesto'
    if o.tecnico:
        Notificacion.avisar(o.tecnico, texto, reverse('equipo', args=[o.pk]))
    avisar_admins(texto, reverse('orden_admin', args=[o.pk]))


def pieza_solicitada(pieza):
    o = pieza.orden
    if pieza.agotada:
        avisar_admins(f'Se AGOTÓ en el inventario: {pieza.cantidad} × {pieza.nombre_pieza}. {pieza.tecnico.nombre} la necesita para la orden #{o.pk}. Repón el stock y dile al técnico y al cliente en cuántos días llega.', reverse('orden_admin', args=[o.pk]))
        return
    avisar_admins(f'{pieza.tecnico.nombre} necesita {pieza.cantidad} × {pieza.nombre_pieza} (no está en el inventario) para la orden #{o.pk}', reverse('orden_admin', args=[o.pk]))


def pieza_agotada_en_camino(pieza):
    o = pieza.orden
    enlace = reverse('equipo', args=[o.pk])
    dias = f'{pieza.dias_entrega} día{"s" if pieza.dias_entrega != 1 else ""}'
    Notificacion.avisar(pieza.tecnico, f'El administrador repondrá {pieza.nombre_pieza} (orden #{o.pk}): llega en {dias}. Te avisaremos cuando esté en el inventario.', enlace)
    Notificacion.avisar(o.cliente, f'Se agotó una pieza de tu equipo #{o.pk} ({pieza.nombre_pieza}) y se repondrá en {dias}. La entrega puede tardar un poco más; te avisaremos la nueva fecha.', enlace)


def pieza_disponible(pieza, cambio_estado=False):
    o = pieza.orden
    enlace = reverse('equipo', args=[o.pk])
    Notificacion.avisar(pieza.tecnico, f'Llegó la pieza {pieza.nombre_pieza} (orden #{o.pk}) y ya está en el inventario. Continúa la reparación y regístrala como usada.', enlace)
    Notificacion.avisar(o.cliente, f'Ya llegó la pieza de tu equipo #{o.pk}. Continuamos con la reparación.', enlace)
    if cambio_estado:
        Notificacion.avisar(o.cliente, f'Tu equipo #{o.pk} ahora está: {o.get_estado_display()}', enlace)


def pieza_cotizada(pieza):
    o = pieza.orden
    enlace = reverse('equipo', args=[o.pk])
    dias = f' Tarda {pieza.dias_entrega} día{"s" if pieza.dias_entrega != 1 else ""} en llegar.' if pieza.dias_entrega is not None else ''
    Notificacion.avisar(o.cliente, f'La pieza de tu equipo #{o.pk} cuesta ${pieza.total} con IVA.{dias} Revisa y responde.', enlace)
    Notificacion.avisar(pieza.tecnico, f'El administrador cotizó la pieza {pieza.nombre_pieza} (orden #{o.pk}): ${pieza.total}. Se envió al cliente.', enlace)


def pieza_descartada(pieza):
    o = pieza.orden
    extra = f' Motivo: {pieza.nota_admin}' if pieza.nota_admin else ''
    Notificacion.avisar(pieza.tecnico, f'No se pedirá la pieza {pieza.nombre_pieza} (orden #{o.pk}).{extra}', reverse('equipo', args=[o.pk]))


def pieza_descartada_cliente(pieza):
    o = pieza.orden
    extra = f' {pieza.nota_admin}' if pieza.nota_admin else ''
    Notificacion.avisar(o.cliente, f'No fue posible conseguir la pieza {pieza.nombre_pieza} para tu equipo #{o.pk}.{extra} La administración te contactará.', reverse('equipo', args=[o.pk]))


def pieza_respondida(pieza):
    o = pieza.orden
    resultado = 'aceptó' if pieza.estado == pieza.Estado.ACEPTADA else 'rechazó'
    texto = f'{o.cliente.nombre} {resultado} la pieza {pieza.nombre_pieza} (orden #{o.pk}, ${pieza.total})'
    Notificacion.avisar(pieza.tecnico, texto, reverse('equipo', args=[o.pk]))
    avisar_admins(texto + (' Pídela al proveedor.' if pieza.estado == pieza.Estado.ACEPTADA else ''), reverse('orden_admin', args=[o.pk]))


def pieza_pedida(pieza):
    o = pieza.orden
    enlace = reverse('equipo', args=[o.pk])
    llega = f' Llega aprox. el {pieza.llegada_estimada:%d/%m/%Y}.' if pieza.llegada_estimada else ''
    Notificacion.avisar(pieza.tecnico, f'Se pidió al proveedor la pieza {pieza.nombre_pieza} (orden #{o.pk}).{llega}', enlace)
    Notificacion.avisar(o.cliente, f'Pedimos la pieza de tu equipo #{o.pk}.{llega}', enlace)


def retroceso_solicitado(solicitud):
    o = solicitud.orden
    avisar_admins(
        f'{solicitud.tecnico.nombre} pide volver la orden #{o.pk} de {solicitud.get_estado_actual_display()} a {solicitud.get_estado_pedido_display()}: {solicitud.motivo}',
        reverse('orden_admin', args=[o.pk]),
    )


def retroceso_respondido(solicitud):
    o = solicitud.orden
    enlace = reverse('equipo', args=[o.pk])
    if solicitud.estado == solicitud.Estado.APROBADA:
        Notificacion.avisar(solicitud.tecnico, f'Se aprobó tu solicitud: la orden #{o.pk} volvió a {solicitud.get_estado_pedido_display()}', enlace)
        Notificacion.avisar(o.cliente, f'Tu equipo #{o.pk} ahora está: {o.get_estado_display()}', enlace)
    else:
        extra = f' Motivo: {solicitud.respuesta}' if solicitud.respuesta else ''
        Notificacion.avisar(solicitud.tecnico, f'Se rechazó tu solicitud de volver la orden #{o.pk} a {solicitud.get_estado_pedido_display()}.{extra}', enlace)


def fecha_actualizada(orden, anterior):
    """La fecha estimada de entrega cambió porque hay que esperar una pieza."""
    enlace = reverse('equipo', args=[orden.pk])
    antes = f' (antes {anterior:%d/%m/%Y})' if anterior else ''
    texto = f'Actualización de fecha: la entrega estimada de tu equipo #{orden.pk} ahora es el {orden.fecha_estimada:%d/%m/%Y}{antes}, por la espera de la pieza.'
    Notificacion.avisar(orden.cliente, texto, enlace)
    if orden.tecnico:
        Notificacion.avisar(orden.tecnico, f'La entrega estimada de la orden #{orden.pk} ahora es el {orden.fecha_estimada:%d/%m/%Y}{antes}, por la pieza.', enlace)


def pago_enviado(pago):
    o = pago.orden
    avisar_admins(f'{o.cliente.nombre} envió el comprobante de la orden #{o.pk} (${pago.monto}). Revísalo.', reverse('orden_admin', args=[o.pk]))


def pago_efectivo_elegido(pago):
    o = pago.orden
    avisar_admins(f'{o.cliente.nombre} pagará en efectivo la orden #{o.pk} (${pago.monto}). Confirma el pago cuando lo reciba.', reverse('orden_admin', args=[o.pk]))
    Notificacion.avisar(o.cliente, f'Pagarás ${pago.monto} en efectivo (equipo #{o.pk}). Acércate al taller para pagar y retirar tu equipo.', reverse('equipo', args=[o.pk]))


def pago_confirmado(pago):
    o = pago.orden
    if o.tecnico:
        Notificacion.avisar(o.tecnico, f'El pago de la orden #{o.pk} fue confirmado: el equipo se entregará al cliente.', reverse('equipo', args=[o.pk]))
    Notificacion.avisar(o.cliente, f'Recibimos tu pago de ${pago.monto} (equipo #{o.pk}). Ya puedes retirar tu equipo.', reverse('equipo', args=[o.pk]))


def pago_rechazado(pago):
    o = pago.orden
    Notificacion.avisar(o.cliente, f'No pudimos validar tu comprobante (equipo #{o.pk}): {pago.motivo_rechazo}. Envía uno nuevo.', reverse('equipo', args=[o.pk]))


def entregada(orden):
    enlace = reverse('equipo', args=[orden.pk])
    Notificacion.avisar(orden.cliente, f'Tu equipo #{orden.pk} fue entregado. ¡Gracias por confiar en nosotros!', enlace)
    if orden.tecnico:
        Notificacion.avisar(orden.tecnico, f'La orden #{orden.pk} ({orden.modelo}) fue entregada al cliente.', enlace)


def salida_inventario(producto, cantidad, orden, donde):
    """Avisa a los administradores que algo salió del inventario (queda también en el kardex)."""
    avisar_admins(
        f'Salió del inventario: {cantidad} × {producto.nombre} ({donde}, orden #{orden.pk}). Quedan {producto.stock}.',
        reverse('orden_admin', args=[orden.pk]),
    )


def repuesto_en_uso(orden, producto, cantidad):
    avisar_admins(f'El técnico está usando {cantidad} × {producto.nombre} en la orden #{orden.pk}.', reverse('orden_admin', args=[orden.pk]))


def repuesto_extra_cliente(orden, producto, cantidad, precio_unit):
    """El cliente ve que se usó un repuesto adicional y que se suma a su cuenta."""
    total = (precio_unit * cantidad).quantize(Decimal('0.01'))
    Notificacion.avisar(
        orden.cliente,
        f'En tu equipo #{orden.pk} se usó {cantidad} × {producto.nombre} (${total} más IVA). Se suma al total a pagar; lo ves en la factura de tu equipo.',
        reverse('equipo', args=[orden.pk]),
    )


def repuesto_danado(orden, producto, cantidad, motivo, perdida):
    avisar_admins(
        f'Repuesto dañado en la orden #{orden.pk}: {cantidad} × {producto.nombre} ({motivo}). Pérdida registrada: ${perdida}.',
        reverse('orden_admin', args=[orden.pk]),
    )


def orden_cerrada(orden, por, aviso_cliente=''):
    """La orden quedó cancelada (por el cliente) o rechazada (por el administrador)."""
    enlace_admin = reverse('orden_admin', args=[orden.pk])
    enlace = reverse('equipo', args=[orden.pk])
    motivo = f' Motivo: {orden.motivo_cierre}' if orden.motivo_cierre else ''
    if orden.estado == orden.Estado.CANCELADA:
        avisar_admins(f'El cliente canceló la orden #{orden.pk} ({orden.modelo}).{motivo} Habla con él por el chat si quieres negociar.', enlace_admin)
        Notificacion.avisar(orden.cliente, f'Cancelaste el servicio de tu equipo #{orden.pk}. Si lo hiciste por el precio, puedes hablar con la administración desde el botón de contacto: tal vez se llegue a un acuerdo.', enlace)
    elif aviso_cliente:
        avisar_admins(f'La orden #{orden.pk} ({orden.modelo}) quedó rechazada: el cliente no aceptó el descuento ofrecido.', enlace_admin)
        Notificacion.avisar(orden.cliente, f'Orden #{orden.pk}: {aviso_cliente}', enlace)
    else:
        Notificacion.avisar(orden.cliente, f'La administración rechazó la orden #{orden.pk}.{motivo} Puedes hablar con la administración por el chat.', enlace)
    if orden.tecnico:
        Notificacion.avisar(orden.tecnico, f'La orden #{orden.pk} fue rechazada.{motivo} No hagas más cambios: el administrador te avisará si se reabre.', enlace)


def descuento_aplicado(orden, detalle):
    Notificacion.avisar(orden.cliente, f'La administración te aplicó un descuento ({detalle}) en tu equipo #{orden.pk}. Míralo en tu presupuesto.', reverse('equipo', args=[orden.pk]))


def orden_reabierta(orden, descuento=0):
    enlace = reverse('equipo', args=[orden.pk])
    extra = f' Se acordó un descuento: {descuento}.' if descuento else ''
    Notificacion.avisar(orden.cliente, f'Tu equipo #{orden.pk} se reabrió y vuelve a estar: {orden.get_estado_display()}.{extra}', enlace)
    if orden.tecnico:
        Notificacion.avisar(orden.tecnico, f'La orden #{orden.pk} se reabrió: continúa en {orden.get_estado_display()}.{extra}', enlace)


def orden_terminada_sin_reparar(orden):
    enlace = reverse('equipo', args=[orden.pk])
    Notificacion.avisar(orden.cliente, f'Tu equipo #{orden.pk} está listo para retirar sin reparar. Revisa lo que corresponde pagar.', enlace)
    if orden.tecnico:
        Notificacion.avisar(orden.tecnico, f'La orden #{orden.pk} se cerró como lista para entrega (sin reparar).', enlace)
