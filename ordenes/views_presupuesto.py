"""Presupuestos y piezas: acciones del técnico, del cliente y del administrador sobre una orden."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from core.models import Configuracion
from inventario.models import Categoria, PiezaSolicitada, Producto, Proveedor, UsoRepuesto
from usuarios.permisos import solo_admin

from . import presupuestos
from .models import Orden, Presupuesto, PresupuestoItem


def _entero(valor):
    return int(valor) if str(valor).strip().isdigit() else None


@login_required
@require_POST
def presupuesto_crear(request, pk):
    if not request.user.es_tecnico:
        raise Http404
    orden = get_object_or_404(Orden, pk=pk, tecnico=request.user)
    tarifas = {}
    for clave, valor in request.POST.items():
        if clave.startswith('tarifa_') and valor.strip() not in ('', '0'):
            numero = _entero(clave[7:])
            if numero is None:
                messages.error(request, 'Datos no válidos.')
                return redirect('equipo', pk=pk)
            tarifas[numero] = valor
    for sel, cant in zip(request.POST.getlist('servicio_sel'), request.POST.getlist('servicio_cant')):
        if not sel.strip():
            continue
        numero = _entero(sel)
        if numero is None:
            messages.error(request, 'Datos no válidos.')
            return redirect('equipo', pk=pk)
        try:
            tarifas[numero] = str(int(tarifas.get(numero, 0)) + int(cant or 1))   # el mismo servicio repetido se suma
        except ValueError:
            messages.error(request, 'La cantidad de cada servicio debe ser un número.')
            return redirect('equipo', pk=pk)
    repuestos = []
    for producto, cantidad in zip(request.POST.getlist('producto'), request.POST.getlist('cantidad_producto')):
        if producto.strip() == '' and cantidad.strip() == '':
            continue
        numero = _entero(producto)
        if numero is None:
            messages.error(request, 'Elige el repuesto en cada línea o quita la línea vacía.')
            return redirect('equipo', pk=pk)
        repuestos.append((numero, cantidad))
    # Piezas que faltan (se piden al administrador junto con el presupuesto): nombre y cantidad por línea
    faltan = []
    for nombre, cantidad in zip(request.POST.getlist('falta_nombre'), request.POST.getlist('falta_cantidad')):
        nombre = ' '.join(nombre.split())
        if not nombre:
            continue
        if len(nombre) < 3:
            messages.error(request, 'Escribe el nombre completo de cada pieza que falta (o quita la línea).')
            return redirect('equipo', pk=pk)
        faltan.append((nombre, cantidad or '1'))
    try:
        p = presupuestos.crear(orden.pk, request.user, tarifas, repuestos, request.POST.get('observacion', ''), proforma=request.POST.get('proforma') == 'on')
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
        return redirect('equipo', pk=pk)
    pedidas = 0
    for nombre, cantidad in faltan:
        try:
            presupuestos.solicitar_pieza(orden.pk, request.user, nombre, cantidad, '')
            pedidas += 1
        except presupuestos.PresupuestoError as e:
            messages.error(request, f'La pieza «{nombre}» no se pudo pedir: {e}')
    extra = f' Además pediste {pedidas} pieza{"s" if pedidas != 1 else ""} al administrador.' if pedidas else ''
    messages.success(request, f'{"Proforma de diagnóstico" if p.espera_pieza else "Presupuesto"} enviado al cliente: total ${p.total}.{extra}')
    return redirect('equipo', pk=pk)


@login_required
@require_POST
def presupuesto_retirar(request, pk, presupuesto_pk):
    if not request.user.es_tecnico:
        raise Http404
    get_object_or_404(Presupuesto, pk=presupuesto_pk, orden_id=pk, orden__tecnico=request.user)
    try:
        presupuestos.retirar(presupuesto_pk, request.user)
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, 'Presupuesto retirado. Puedes armar uno nuevo.')
    return redirect('equipo', pk=pk)


@login_required
@require_POST
def presupuesto_responder(request, pk, presupuesto_pk):
    if not request.user.es_cliente:
        raise Http404
    get_object_or_404(Presupuesto, pk=presupuesto_pk, orden_id=pk, orden__cliente=request.user)
    acepta = request.POST.get('respuesta') == 'aceptar'
    if not acepta and request.POST.get('respuesta') != 'rechazar':
        messages.error(request, 'Elige aceptar o rechazar.')
        return redirect('equipo', pk=pk)
    try:
        presupuestos.responder(presupuesto_pk, request.user, acepta, request.POST.get('motivo', ''))
    except presupuestos.SinStock as e:
        presupuestos.avisar_falta_de_stock(presupuesto_pk)
        messages.error(request, str(e))
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, 'Presupuesto aceptado. El técnico ya puede continuar.' if acepta else 'Presupuesto rechazado: tu equipo quedó como «Cancelada». Habla con la administración si quieres negociar el precio.')
    return redirect('equipo', pk=pk)


@login_required
@require_POST
def pieza_pedir(request, pk):
    if not request.user.es_tecnico:
        raise Http404
    orden = get_object_or_404(Orden, pk=pk, tecnico=request.user)
    producto = None
    numero = _entero(request.POST.get('producto', ''))
    if numero is not None:
        producto = Producto.objects.filter(pk=numero).first()
    categoria = None
    numero_cat = _entero(request.POST.get('categoria', ''))
    if numero_cat is not None:
        categoria = Categoria.objects.filter(pk=numero_cat).first()
    nombre = request.POST.get('nombre_pieza', '') or (producto.nombre if producto else '')
    try:
        presupuestos.solicitar_pieza(orden.pk, request.user, nombre, request.POST.get('cantidad_pieza', '1'), request.POST.get('observacion_pieza', ''), producto, categoria,
                                     agotada=request.POST.get('tipo_pieza') == 'agotada')
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, 'Pieza solicitada. El administrador ya fue avisado.')
    return redirect('equipo', pk=pk)


def _decimal(valor):
    valor = (valor or '').strip().replace(',', '.')
    if not valor:
        return None
    try:
        d = Decimal(valor)
    except InvalidOperation:
        raise presupuestos.PresupuestoError('Escribe los precios solo con números, por ejemplo 45.50.')
    if d < 0 or d > Decimal('99999999'):
        raise presupuestos.PresupuestoError('Precio no válido.')
    return d.quantize(Decimal('0.01'))


def _accion_admin(request, pk, pieza_pk, ok, funcion):
    get_object_or_404(PiezaSolicitada, pk=pieza_pk, orden_id=pk)
    try:
        funcion()
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
    except PiezaSolicitada.DoesNotExist:
        raise Http404
    else:
        messages.success(request, ok)
    return redirect('orden_admin', pk=pk)


@solo_admin
@require_POST
def pieza_cotizar(request, pk, pieza_pk):
    get_object_or_404(PiezaSolicitada, pk=pieza_pk, orden_id=pk)
    try:
        proveedor = Proveedor.objects.filter(pk=_entero(request.POST.get('proveedor', ''))).first() if request.POST.get('proveedor') else None
        dias = _entero(request.POST.get('dias_entrega', ''))
        if dias is not None and dias > 365:
            raise presupuestos.PresupuestoError('Los días de entrega no pueden pasar de 365.')
        presupuestos.cotizar_pieza(
            pieza_pk, request.user, proveedor, _decimal(request.POST.get('costo')), _decimal(request.POST.get('precio')), dias,
            request.POST.get('nota_admin', ''), Categoria.objects.filter(pk=_entero(request.POST.get('categoria', ''))).first() if request.POST.get('categoria') else None,
        )
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, 'Cotización enviada al cliente y al técnico.')
    return redirect('orden_admin', pk=pk)


@solo_admin
@require_POST
def pieza_descartar(request, pk, pieza_pk):
    return _accion_admin(request, pk, pieza_pk, 'Listo: no se pedirá esa pieza. Se avisó al técnico (y al cliente si lo marcaste).', lambda: presupuestos.descartar_pieza(
        pieza_pk, request.user, request.POST.get('nota_admin', ''), request.POST.get('avisar_cliente') == 'on',
        request.POST.get('cerrar_orden') == 'on', request.POST.get('cobro', 'nada'), request.POST.get('cargo', '')))


@solo_admin
@require_POST
def pieza_reponer(request, pk, pieza_pk):
    dias = _entero(request.POST.get('dias_reposicion', ''))
    return _accion_admin(request, pk, pieza_pk, 'Listo: se avisó al técnico y al cliente. Cuando registres la reposición en el inventario, el técnico recibirá el aviso de que llegó.',
                         lambda: presupuestos.reponer_agotada(pieza_pk, request.user, dias))


@solo_admin
@require_POST
def pieza_pedida(request, pk, pieza_pk):
    return _accion_admin(request, pk, pieza_pk, 'Pieza marcada como pedida al proveedor. Se avisó al cliente y al técnico.', lambda: presupuestos.pedir_pieza(pieza_pk, request.user))


@login_required
@require_POST
def pieza_responder(request, pk, pieza_pk):
    if not request.user.es_cliente:
        raise Http404
    get_object_or_404(PiezaSolicitada, pk=pieza_pk, orden_id=pk, orden__cliente=request.user)
    respuesta = request.POST.get('respuesta')
    if respuesta not in ('aceptar', 'rechazar'):
        messages.error(request, 'Elige aceptar o rechazar.')
        return redirect('equipo', pk=pk)
    try:
        presupuestos.responder_pieza(pieza_pk, request.user, respuesta == 'aceptar', request.POST.get('motivo', ''))
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, 'Pieza aceptada. La pediremos al proveedor.' if respuesta == 'aceptar' else 'Pieza rechazada: tu equipo quedó como «Cancelada». Habla con la administración si quieres negociar el precio.')
    return redirect('equipo', pk=pk)


@login_required
@require_POST
def repuesto_usar(request, pk):
    if not request.user.es_tecnico:
        raise Http404
    get_object_or_404(Orden, pk=pk, tecnico=request.user)
    try:
        pieza_pk = _entero(request.POST.get('pieza', ''))
        producto_pk = _entero(request.POST.get('producto', ''))
        if pieza_pk is None and producto_pk is None:
            raise presupuestos.PresupuestoError('Elige el repuesto del inventario.')
        uso = presupuestos.usar_repuesto(pk, request.user, producto_pk, request.POST.get('cantidad', '1'), pieza_pk, reemplazo=request.POST.get('reemplazo') == '1')
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
    else:
        cobro = 'No se cobra al cliente.' if uso.precio_unit == 0 else 'Se suma a la factura del cliente y él recibió un aviso.'
        messages.success(request, f'Registrado: {uso.cantidad} × {uso.producto.nombre}. Ya salió del inventario. {cobro}')
    return redirect('equipo', pk=pk)


@login_required
@require_POST
def repuesto_danado(request, pk):
    if not request.user.es_tecnico:
        raise Http404
    get_object_or_404(Orden, pk=pk, tecnico=request.user)
    try:
        producto_pk = _entero(request.POST.get('producto', ''))
        perdida = presupuestos.reportar_danado(pk, request.user, producto_pk, request.POST.get('cantidad', '1'), request.POST.get('motivo', ''))
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f'Daño reportado. Salió del inventario y quedó como pérdida (${perdida}). Se avisó al administrador.')
    return redirect('equipo', pk=pk)


@login_required
@require_POST
def repuesto_marcar(request, pk, item_pk):
    if not request.user.es_tecnico:
        raise Http404
    get_object_or_404(Orden, pk=pk, tecnico=request.user)
    try:
        item = presupuestos.marcar_en_uso(item_pk, request.user)
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f'Marcado en uso: {item.cantidad} × {item.producto.nombre}. Se avisó al administrador.')
    return redirect('equipo', pk=pk)


@login_required
@require_POST
def repuesto_quitar(request, pk, uso_pk):
    if not request.user.es_tecnico:
        raise Http404
    get_object_or_404(UsoRepuesto, pk=uso_pk, orden_id=pk, orden__tecnico=request.user)
    try:
        presupuestos.quitar_uso(uso_pk, request.user)
    except presupuestos.PresupuestoError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, 'Repuesto devuelto al inventario.')
    return redirect('equipo', pk=pk)


def contexto(orden, usuario):
    """Lo que las pantallas de la orden necesitan para mostrar presupuestos y piezas."""
    pendiente_o_aceptado = orden.presupuestos.filter(estado__in=[Presupuesto.Estado.PENDIENTE, Presupuesto.Estado.ACEPTADO]).exists()
    abierta = orden.estado not in Orden.CERRADAS
    datos = {
        'presupuestos': orden.presupuestos.prefetch_related('items__tarifa', 'items__producto'),
        'piezas': orden.piezas_solicitadas.select_related('tecnico', 'producto'),
    }
    datos['factura'] = orden.factura
    datos['total_orden'] = datos['factura']['total']
    cotizada = orden.piezas_solicitadas.filter(estado=PiezaSolicitada.Estado.COTIZADA).first()
    datos['total_con_pieza'] = datos['total_orden'] + (cotizada.total if cotizada and cotizada.total else 0)
    if cotizada and cotizada.dias_entrega is not None:
        nueva = presupuestos.fecha_con_pieza(orden, cotizada.dias_entrega)
        if nueva != orden.fecha_estimada:
            datos['fecha_nueva'] = nueva
    if usuario.es_admin and abierta:
        datos['proveedores_disp'] = Proveedor.objects.order_by('nombre')
        datos['margen'] = Configuracion.margen_defecto()
        datos['categorias_disp'] = Categoria.objects.order_by('nombre')
    if not usuario.es_cliente:
        datos['danados'] = orden.repuestos_danados.select_related('producto', 'tecnico')
    datos['usos'] = orden.usos_repuesto.select_related('producto', 'tecnico')
    datos['repuestos_presupuesto'] = list(PresupuestoItem.objects.filter(
        presupuesto__orden=orden, presupuesto__estado=Presupuesto.Estado.ACEPTADO, tipo=PresupuestoItem.Tipo.REPUESTO,
    ).select_related('producto').order_by('pk'))
    if usuario.es_tecnico and orden.estado in (Orden.Estado.REPARACION, Orden.Estado.DIAGNOSTICO):
        datos['productos_danio'] = presupuestos.productos_en_uso(orden)
    if usuario.es_tecnico and orden.estado == Orden.Estado.REPARACION:
        datos['piezas_por_usar'] = orden.piezas_sin_usar
        datos['productos_uso'] = Producto.objects.filter(activo=True, stock__gt=0).select_related('categoria').order_by('nombre')
    if usuario.es_tecnico and abierta:
        # El presupuesto se arma durante el diagnóstico; en reparación ya no se ofrece
        sin_pendiente = not orden.presupuestos.filter(estado=Presupuesto.Estado.PENDIENTE).exists()
        datos['presupuesto_adicional'] = sin_pendiente and orden.presupuestos.filter(estado=Presupuesto.Estado.ACEPTADO).exists()
        datos['puede_presupuestar'] = sin_pendiente and orden.estado in (Orden.Estado.RECIBIDO, Orden.Estado.DIAGNOSTICO, Orden.Estado.REPARACION)
        if datos['puede_presupuestar']:
            datos['tarifas_disp'] = presupuestos.tarifas_disponibles(orden)
            datos['repuestos_disp'] = presupuestos.repuestos_disponibles()
            datos['iva'] = orden.iva_efectivo
        datos['categorias_disp'] = Categoria.objects.order_by('nombre')
        datos['productos_agotados'] = Producto.objects.filter(activo=True, stock__lte=0).select_related('categoria').order_by('nombre')
        datos['categorias_inventario'] = Categoria.objects.filter(productos__activo=True, productos__stock__gt=0).distinct().order_by('nombre')
    return datos
