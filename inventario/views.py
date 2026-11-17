from django.contrib import messages
from django.core.paginator import Paginator
from urllib.parse import urlencode

from django.db import transaction
from django.db.models import F, Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from usuarios.permisos import solo_admin

from . import servicios
from core.models import Configuracion

from . import precios
from .forms import AjusteForm, CategoriaForm, ContactoProveedorForm, CorregirPedidoForm, MargenForm, ProductoForm, ProveedorForm, ReponerForm
from .models import Categoria, MovimientoInventario, PiezaSolicitada, Producto, Proveedor

POR_PAGINA = 10


def _tab(request, **extra):
    return {'tab': 'inv', 'n_bajos': Producto.objects.filter(activo=True, stock__lte=F('stock_minimo')).count(), **extra}


@solo_admin
def lista(request):
    """Productos con búsqueda y filtro de stock bajo."""
    consulta = Producto.objects.select_related('categoria')
    texto = request.GET.get('q', '').strip()
    if texto:
        consulta = consulta.filter(
            Q(nombre__icontains=texto) | Q(codigo__icontains=texto) | Q(marca__icontains=texto) | Q(categoria__nombre__icontains=texto)
        )
    filtro = request.GET.get('filtro', '')
    if filtro == 'bajo':
        consulta = consulta.filter(activo=True, stock__lte=F('stock_minimo'))
    else:
        filtro = ''
    categoria = Categoria.objects.filter(pk=request.GET['categoria']).first() if request.GET.get('categoria', '').isdigit() else None
    if categoria:
        consulta = consulta.filter(categoria=categoria)
    pagina = Paginator(consulta, POR_PAGINA).get_page(request.GET.get('pagina'))
    return render(request, 'inventario/lista.html', _tab(
        request, pagina=pagina, q=texto, filtro=filtro, margen_defecto=Configuracion.margen_defecto(),
        categorias=Categoria.objects.all(), categoria=categoria,
        extra=urlencode({k: v for k, v in (('filtro', filtro), ('categoria', categoria.pk if categoria else '')) if v}),
    ))


@solo_admin
@require_POST
def margen_guardar(request):
    form = MargenForm(request.POST)
    if form.is_valid():
        Configuracion.objects.update_or_create(
            clave=Configuracion.MARGEN,
            defaults={'valor': form.cleaned_data['valor'], 'descripcion': 'Margen de ganancia sobre el costo para sugerir precios de venta'},
        )
        messages.success(request, f'Margen general actualizado a {form.cleaned_data["valor"].normalize():f}%')
    else:
        messages.error(request, next(iter(form.errors.values()))[0])
    return redirect('inventario')


def _pieza_de_origen(request):
    """La pieza de una orden desde la que se abrió «Nuevo producto» (para llenar el formulario). None si no hay o no sirve."""
    numero = (request.POST.get('pieza') if request.method == 'POST' else request.GET.get('pieza')) or ''
    if not numero.isdigit():
        return None
    return PiezaSolicitada.objects.select_related('proveedor', 'orden').filter(
        pk=int(numero), producto__isnull=True, estado__in=[PiezaSolicitada.Estado.ACEPTADA, PiezaSolicitada.Estado.PEDIDA],
    ).first()


def _datos_de_pieza(pieza):
    """Lo que ya se sabe de la pieza: nombre, categoría y proveedor de la cotización, costo y precio al cliente."""
    datos = {'nombre': pieza.nombre_pieza, 'stock_inicial': 0}
    if pieza.precio is not None:
        datos['precio_venta'] = pieza.precio
    if pieza.categoria_id:
        datos['categoria'] = pieza.categoria_id  # la que eligió el técnico al pedirla
    if pieza.proveedor_id:
        datos['proveedor1'] = pieza.proveedor_id
        if pieza.costo is not None:
            datos['precio1'] = pieza.costo
        if 'categoria' not in datos:
            categoria = pieza.proveedor.categorias.order_by('nombre').first()
            if categoria:
                datos['categoria'] = categoria.pk
    elif pieza.costo is not None:
        datos['costo_pedido'] = pieza.costo
    return datos


@solo_admin
def producto_nuevo(request):
    pieza = _pieza_de_origen(request)
    inicial = _datos_de_pieza(pieza) if pieza and request.method == 'GET' else None
    form = ProductoForm(request.POST or None, request.FILES or None, auto_id='prd_%s', initial=inicial)
    if pieza:
        del form.fields['stock_inicial']  # la entrada de la pieza se registra sola al guardar
    if request.method == 'POST' and form.is_valid():
        if pieza:
            from ordenes import presupuestos  # import tardío: ordenes también usa inventario
            try:
                with transaction.atomic():
                    producto = servicios.crear_producto(form, request.user)
                    pieza.producto = producto
                    pieza.save(update_fields=['producto'])
                    presupuestos.pieza_disponible(pieza.pk, request.user)
            except presupuestos.PresupuestoError as e:
                messages.error(request, str(e))
                return redirect('orden_admin', pk=pieza.orden_id)
            producto.refresh_from_db()
            messages.success(request, f'Pieza registrada en el inventario: «{producto.nombre}» (stock {producto.stock}). Se avisó al técnico y al cliente que ya llegó, y la orden #{pieza.orden_id} pasó a reparación.')
        else:
            producto = servicios.crear_producto(form, request.user)
            messages.success(request, f'Producto «{producto.nombre}» creado')
        return redirect('producto_detalle', pk=producto.pk)
    return render(request, 'inventario/producto_form.html', _tab(request, form=form, producto=None, pieza=pieza, hay_proveedores=Proveedor.objects.exists(), margen_defecto=Configuracion.margen_defecto()))


@solo_admin
def producto_editar(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    form = ProductoForm(request.POST or None, request.FILES or None, instance=producto, auto_id='prd_%s')
    if request.method == 'POST' and form.is_valid():
        servicios.actualizar_producto(form)
        messages.success(request, 'Producto actualizado')
        return redirect('producto_detalle', pk=producto.pk)
    return render(request, 'inventario/producto_form.html', _tab(request, form=form, producto=producto, hay_proveedores=Proveedor.objects.exists(), margen_defecto=Configuracion.margen_defecto()))


@solo_admin
def producto_detalle(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    f_corregir = CorregirPedidoForm(auto_id='cor_%s')
    f_reponer = ReponerForm(producto=producto, auto_id='rep_%s', initial=_precio_inicial(producto))
    f_ajuste = AjusteForm(auto_id='aju_%s', initial={'stock_real': producto.stock})
    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'reponer':
            f_reponer = ReponerForm(request.POST, producto=producto, auto_id='rep_%s')
            if f_reponer.is_valid():
                d = f_reponer.cleaned_data
                try:
                    _, costo_anterior = servicios.reponer(
                        producto.pk, d['proveedor'], d['cantidad'], d['precio_unit'], d['observacion'], request.user,
                        actualizar_precio=d['actualizar_precio'],
                    )
                except servicios.StockError as e:
                    f_reponer.add_error(None, str(e))
                else:
                    producto.refresh_from_db()
                    messages.success(
                        request,
                        f'Pedido registrado: {d["cantidad"]} × ${d["precio_unit"]:.2f} = ${d["cantidad"] * d["precio_unit"]:.2f} a {d["proveedor"].nombre}. '
                        f'El stock subió a {producto.stock}.',
                    )
                    if costo_anterior and producto.costo_pedido != costo_anterior:
                        messages.info(
                            request,
                            f'Costo promedio recalculado: de ${costo_anterior} a ${producto.costo_pedido}. Se promedia lo que ya había en estantería '
                            f'(a ${costo_anterior}) con lo que acaba de llegar (a ${d["precio_unit"]:.2f}), para que el precio de venta no deje pérdida.',
                        )
                    if producto.bajo_objetivo and not d['actualizar_precio']:
                        messages.warning(
                            request,
                            f'Con este costo tu margen es {producto.margen_real}% (objetivo {producto.margen_objetivo.normalize():f}%). '
                            f'Precio sugerido: ${producto.precio_sugerido}; el actual es ${producto.precio_venta}. Puedes pulsar «Usar el sugerido».',
                        )
                    return redirect('producto_detalle', pk=pk)
        elif accion == 'usar_sugerido':
            try:
                servicios.usar_sugerido(producto.pk)
            except servicios.StockError as e:
                messages.error(request, str(e))
            else:
                messages.success(request, 'El costo de venta se actualizó al precio sugerido')
            return redirect('producto_detalle', pk=pk)
        elif accion == 'ajustar':
            f_ajuste = AjusteForm(request.POST, auto_id='aju_%s')
            if f_ajuste.is_valid():
                d = f_ajuste.cleaned_data
                try:
                    servicios.ajustar(producto.pk, d['stock_real'], d['motivo'], request.user, error_registro=d['error_registro'])
                except servicios.StockError as e:
                    f_ajuste.add_error(None, str(e))
                else:
                    messages.success(request, 'Stock ajustado. Quedó anotado como error de registro: no cuenta como pérdida.' if d['error_registro'] else 'Stock ajustado')
                    return redirect('producto_detalle', pk=pk)
        elif accion == 'corregir_pedido':
            f_corregir = CorregirPedidoForm(request.POST, auto_id='cor_%s')
            ultimo = producto.pedido_set.order_by('-fecha', '-pk').first()
            if ultimo is None:
                messages.error(request, 'Este producto no tiene pedidos que corregir.')
                return redirect('producto_detalle', pk=pk)
            if f_corregir.is_valid():
                d = f_corregir.cleaned_data
                try:
                    servicios.corregir_pedido(ultimo.pk, d['cantidad_correcta'], d['motivo'], request.user)
                except servicios.StockError as e:
                    f_corregir.add_error(None, str(e))
                else:
                    messages.success(request, f'Pedido corregido a {d["cantidad_correcta"]} unidades. El stock y el gasto de finanzas se ajustaron; no se registró pérdida.')
                    return redirect('producto_detalle', pk=pk)
        producto.refresh_from_db()
    return render(request, 'inventario/producto_detalle.html', _tab(
        request, producto=producto, f_reponer=f_reponer, f_ajuste=f_ajuste, f_corregir=f_corregir,
        ultimo_pedido=producto.pedido_set.select_related('proveedor').order_by('-fecha', '-pk').first(),
        cotizaciones=_comparador(producto),
        historial=producto.historial_precios.select_related('proveedor')[:8],
        movimientos=producto.movimientos.select_related('usuario', 'pedido')[:10],
        sugerida=_cantidad_sugerida(producto),
        no_eliminable=servicios.por_que_no_se_elimina(producto),
    ))


def _comparador(producto):
    """Cada proveedor con lo que cobra, el precio de venta que convendría con él y la ganancia que deja."""
    filas = []
    for c in producto.cotizaciones.select_related('proveedor').order_by('precio'):
        filas.append({
            'cot': c,
            'sugerido': precios.sugerido(c.precio, producto.margen_objetivo),
            'ganancia': producto.precio_venta - c.precio,
            'margen': precios.margen_real(producto.precio_venta, c.precio),
        })
    return filas


def _cantidad_sugerida(producto):
    """Cuántas unidades pedir para llegar al doble del stock mínimo (None si no hay mínimo o ya se está por encima)."""
    if producto.stock_minimo <= 0:
        return None
    falta = producto.stock_minimo * 2 - producto.stock
    return falta if falta > 0 else None


def _precio_inicial(producto):
    mejor = producto.cotizaciones.select_related('proveedor').order_by('precio').first()
    return {'proveedor': mejor.proveedor_id, 'precio_unit': mejor.precio} if mejor else {}


@solo_admin
@require_POST
def producto_activar(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    producto.activo = not producto.activo
    producto.save(update_fields=['activo'])
    messages.success(request, f'«{producto.nombre}» ahora está {"activo" if producto.activo else "desactivado"}')
    return redirect('producto_detalle', pk=pk)


@solo_admin
@require_POST
def producto_eliminar(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    nombre = producto.nombre
    try:
        servicios.eliminar_producto(pk)
    except servicios.StockError as e:
        messages.error(request, str(e))
        return redirect('producto_detalle', pk=pk)
    messages.success(request, f'Producto «{nombre}» eliminado')
    return redirect('inventario')


@solo_admin
def movimientos(request):
    """Kardex: todas las entradas, salidas y ajustes."""
    consulta = MovimientoInventario.objects.select_related('producto', 'usuario', 'pedido')
    tipo = request.GET.get('tipo', '')
    if tipo in MovimientoInventario.Tipo.values:
        consulta = consulta.filter(tipo=tipo)
    else:
        tipo = ''
    texto = request.GET.get('q', '').strip()
    if texto:
        consulta = consulta.filter(Q(producto__nombre__icontains=texto) | Q(producto__codigo__icontains=texto) | Q(motivo__icontains=texto) | Q(pedido__observacion__icontains=texto))
    pagina = Paginator(consulta, 20).get_page(request.GET.get('pagina'))
    return render(request, 'inventario/movimientos.html', _tab(request, pagina=pagina, q=texto, tipo=tipo, tipos=MovimientoInventario.Tipo.choices, extra=urlencode({'tipo': tipo}) if tipo else ''))


@solo_admin
def proveedores(request):
    form = ProveedorForm(request.POST or None, auto_id='prv_%s')
    if request.method == 'POST' and form.is_valid():
        nuevo = form.save()
        messages.success(request, f'Proveedor «{nuevo.nombre}» añadido')
        return redirect('proveedores')
    lista_ = Proveedor.objects.prefetch_related('categorias', 'productoproveedor_set')
    categoria = Categoria.objects.filter(pk=request.GET['categoria']).first() if request.GET.get('categoria', '').isdigit() else None
    if categoria:
        lista_ = lista_.filter(categorias=categoria)
    return render(request, 'inventario/proveedores.html', _tab(
        request, form=form, proveedores=lista_, categorias=Categoria.objects.prefetch_related('proveedores'), categoria=categoria,
        form_categoria=CategoriaForm(auto_id='cat_%s'), todos=Proveedor.objects.count(),
    ))


@solo_admin
@require_POST
def proveedor_contacto(request, pk):
    """Guarda el teléfono y los días de entrega que se editan directamente en la lista."""
    proveedor = get_object_or_404(Proveedor, pk=pk)
    form = ContactoProveedorForm(request.POST, instance=proveedor)
    if form.is_valid():
        form.save()
        messages.success(request, f'Datos de «{proveedor.nombre}» guardados')
    else:
        messages.error(request, next(iter(form.errors.values()))[0])
    return redirect(request.POST.get('volver') if (request.POST.get('volver') or '').startswith('/inventario/proveedores/') else 'proveedores')


@solo_admin
@require_POST
def categoria_crear(request):
    form = CategoriaForm(request.POST)
    if form.is_valid():
        nueva = form.save()
        messages.success(request, f'Categoría «{nueva.nombre}» creada. Ya puedes asignarla a proveedores y productos.')
    else:
        messages.error(request, form.errors['nombre'][0])
    return redirect('proveedores')


@solo_admin
@require_POST
def categoria_eliminar(request, pk):
    categoria = get_object_or_404(Categoria, pk=pk)
    categoria.delete()  # los productos quedan "sin categoría" y los proveedores dejan de tenerla
    messages.success(request, f'Categoría «{categoria.nombre}» eliminada')
    return redirect('proveedores')


@solo_admin
def proveedor_editar(request, pk):
    proveedor = get_object_or_404(Proveedor, pk=pk)
    form = ProveedorForm(request.POST or None, instance=proveedor, auto_id='prv_%s')
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Proveedor actualizado')
        return redirect('proveedores')
    return render(request, 'inventario/proveedor_form.html', _tab(request, form=form, proveedor=proveedor))


@solo_admin
@require_POST
def proveedor_eliminar(request, pk):
    proveedor = get_object_or_404(Proveedor, pk=pk)
    try:
        proveedor.delete()
    except ProtectedError:
        messages.error(request, f'No se puede eliminar a «{proveedor.nombre}» porque ya tiene pedidos registrados.')
    else:
        messages.success(request, 'Proveedor eliminado')
    return redirect('proveedores')
