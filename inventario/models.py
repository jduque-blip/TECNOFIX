from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from . import precios

DINERO = dict(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])


class Categoria(models.Model):
    """Tipo de producto (computadoras, celulares...). Los proveedores y los productos se agrupan por categoría."""

    nombre = models.CharField(max_length=60, unique=True)

    class Meta:
        verbose_name = 'categoría'
        verbose_name_plural = 'categorías'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class Proveedor(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    categorias = models.ManyToManyField(Categoria, blank=True, related_name='proveedores', verbose_name='categorías')
    telefono = models.CharField('teléfono', max_length=20, blank=True)
    dias_entrega = models.PositiveSmallIntegerField('días de entrega', null=True, blank=True)

    class Meta:
        verbose_name_plural = 'proveedores'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre

    @property
    def etiqueta(self):
        """Nombre con sus categorías, para elegirlo en las listas desplegables."""
        cats = ', '.join(c.nombre for c in self.categorias.all())
        return f'{self.nombre} ({cats})' if cats else self.nombre


class Producto(models.Model):
    """Repuesto o producto del inventario."""

    nombre = models.CharField(max_length=120)
    codigo = models.CharField('código', max_length=30, unique=True)
    marca = models.CharField(max_length=60, blank=True)
    categoria = models.ForeignKey(Categoria, null=True, blank=True, on_delete=models.SET_NULL, related_name='productos', verbose_name='categoría')
    imagen = models.ImageField(upload_to='productos/', blank=True, null=True)
    precio_venta = models.DecimalField('costo de venta', **DINERO)
    costo_pedido = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    stock = models.PositiveIntegerField(default=0)
    stock_minimo = models.PositiveIntegerField('stock mínimo', default=0)
    margen_propio = models.DecimalField(
        'margen propio (%)', max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('500'))],
        help_text='Si se deja vacío se usa el margen general.',
    )
    activo = models.BooleanField(default=True)
    proveedores = models.ManyToManyField(Proveedor, through='ProductoProveedor')

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return f'{self.nombre} ({self.codigo})'

    @property
    def stock_bajo(self):
        return self.stock <= self.stock_minimo

    @property
    def ganancia(self):
        """Ganancia por unidad: lo que se cobra menos lo que costó pedirla."""
        return self.precio_venta - self.costo_pedido

    @property
    def margen_objetivo(self):
        """El margen propio del producto o, si no tiene, el general."""
        from core.models import Configuracion
        return self.margen_propio if self.margen_propio is not None else Configuracion.margen_defecto()

    @property
    def margen_real(self):
        return precios.margen_real(self.precio_venta, self.costo_pedido)

    @property
    def precio_sugerido(self):
        return precios.sugerido(self.costo_pedido, self.margen_objetivo)

    @property
    def bajo_objetivo(self):
        """El precio de venta actual deja menos ganancia que la que se busca."""
        sugerido = self.precio_sugerido
        return sugerido is not None and self.precio_venta < sugerido


class ProductoProveedor(models.Model):
    """Precio que ofrece cada proveedor por un producto. Máximo 2 proveedores por producto."""

    MAXIMO = 2

    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='cotizaciones')
    proveedor = models.ForeignKey(Proveedor, on_delete=models.CASCADE)
    precio = models.DecimalField(**DINERO)
    actualizado_en = models.DateTimeField('precio actualizado', default=timezone.now)

    class Meta:
        verbose_name = 'precio de proveedor'
        verbose_name_plural = 'precios de proveedores'
        constraints = [
            models.UniqueConstraint(fields=['producto', 'proveedor'], name='un_precio_por_proveedor'),
        ]

    def __str__(self):
        return f'{self.producto.nombre} · {self.proveedor.nombre}: ${self.precio}'

    def clean(self):
        otros = ProductoProveedor.objects.filter(producto_id=self.producto_id).exclude(pk=self.pk)
        if otros.count() >= self.MAXIMO:
            raise ValidationError(f'Un producto puede tener como máximo {self.MAXIMO} proveedores.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class HistorialPrecio(models.Model):
    """Cada vez que un proveedor cambia lo que cobra por un producto queda una línea aquí."""

    class Origen(models.TextChoices):
        COTIZACION = 'COTIZACION', 'Cotización'
        PEDIDO = 'PEDIDO', 'Pedido'

    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name='historial_precios')
    proveedor = models.ForeignKey(Proveedor, on_delete=models.CASCADE)
    precio = models.DecimalField(**DINERO)
    origen = models.CharField(max_length=12, choices=Origen.choices)
    fecha = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'historial de precio'
        verbose_name_plural = 'historial de precios'

    def __str__(self):
        return f'{self.producto.nombre} · {self.proveedor.nombre}: ${self.precio}'


class Pedido(models.Model):
    """Reposición de stock a un proveedor."""

    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    precio_unit = models.DecimalField('precio por unidad', **DINERO)
    costo_total = models.GeneratedField(
        expression=F('cantidad') * F('precio_unit'),
        output_field=models.DecimalField(max_digits=12, decimal_places=2),
        db_persist=True,
    )
    observacion = models.TextField('observación', blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    fecha = models.DateTimeField(default=timezone.now)
    # Cómo estaba el producto justo antes del pedido: sirve para recalcular el costo promedio si el pedido se corrige.
    stock_antes = models.PositiveIntegerField(null=True, blank=True, editable=False)
    costo_antes = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, editable=False)
    # Constancia de una corrección administrativa (se anotó mal la cantidad): no es una pérdida.
    cantidad_original = models.PositiveIntegerField(null=True, blank=True, editable=False)
    corregido_en = models.DateTimeField(null=True, blank=True, editable=False)
    correccion_motivo = models.CharField(max_length=150, blank=True, editable=False)

    class Meta:
        ordering = ['-fecha']

    def __str__(self):
        return f'Pedido #{self.pk}: {self.cantidad} x {self.producto.nombre}'


class MovimientoInventario(models.Model):
    """Kardex: toda entrada o salida de stock queda registrada aquí."""

    class Tipo(models.TextChoices):
        ENTRADA = 'ENTRADA', 'Entrada'
        SALIDA = 'SALIDA', 'Salida'
        AJUSTE = 'AJUSTE', 'Ajuste'

    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='movimientos')
    tipo = models.CharField(max_length=8, choices=Tipo.choices)
    cantidad = models.IntegerField()
    stock_resultante = models.IntegerField()
    motivo = models.CharField(max_length=150, blank=True)
    pedido = models.ForeignKey(Pedido, on_delete=models.SET_NULL, null=True, blank=True)
    presupuesto = models.ForeignKey(
        'ordenes.Presupuesto', on_delete=models.SET_NULL, null=True, blank=True
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    fecha = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'movimiento de inventario'
        verbose_name_plural = 'movimientos de inventario'
        constraints = [
            models.CheckConstraint(condition=~Q(cantidad=0), name='movimiento_cantidad_distinta_de_cero'),
            models.CheckConstraint(
                condition=Q(tipo='AJUSTE') | Q(cantidad__gt=0), name='entrada_o_salida_positiva'
            ),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} de {self.cantidad} · {self.producto.nombre}'


class PiezaSolicitada(models.Model):
    """Pieza que el técnico necesita y no está en el inventario. El administrador la cotiza, el cliente la acepta y se pide al proveedor."""

    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Esperando cotización'
        COTIZADA = 'COTIZADA', 'Esperando al cliente'
        ACEPTADA = 'ACEPTADA', 'Aceptada: falta pedirla'
        PEDIDA = 'PEDIDA', 'Pedida al proveedor'
        DISPONIBLE = 'DISPONIBLE', 'Disponible'
        RECHAZADA = 'RECHAZADA', 'Rechazada por el cliente'
        DESCARTADA = 'DESCARTADA', 'No se pedirá'

    ABIERTOS = ('PENDIENTE', 'COTIZADA', 'ACEPTADA', 'PEDIDA')

    orden = models.ForeignKey(
        'ordenes.Orden', on_delete=models.CASCADE, related_name='piezas_solicitadas'
    )
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        limit_choices_to={'rol': 'TECNICO'},
    )
    producto = models.ForeignKey(Producto, on_delete=models.SET_NULL, null=True, blank=True)
    categoria = models.ForeignKey(Categoria, on_delete=models.SET_NULL, null=True, blank=True)
    nombre_pieza = models.CharField(max_length=120)
    cantidad = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    observacion = models.TextField('observación', blank=True)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    creada_en = models.DateTimeField(default=timezone.now)
    # Cotización del administrador
    proveedor = models.ForeignKey(Proveedor, on_delete=models.SET_NULL, null=True, blank=True)
    costo = models.DecimalField('costo al proveedor (por unidad)', max_digits=10, decimal_places=2, null=True, blank=True)
    precio = models.DecimalField('precio al cliente (por unidad, sin IVA)', max_digits=10, decimal_places=2, null=True, blank=True)
    iva_porcentaje = models.DecimalField(max_digits=5, decimal_places=2, default=15)
    dias_entrega = models.PositiveSmallIntegerField(null=True, blank=True)
    nota_admin = models.CharField(max_length=300, blank=True)
    cotizada_en = models.DateTimeField(null=True, blank=True)
    respondida_en = models.DateTimeField(null=True, blank=True)
    pedida_en = models.DateTimeField(null=True, blank=True)
    llegada_estimada = models.DateField(null=True, blank=True)
    agotada = models.BooleanField('agotada en el inventario', default=False,
                                  help_text='La pieza ya existe en el inventario pero se agotó: solo hay que reponer el stock.')

    class Meta:
        verbose_name = 'pieza solicitada'
        verbose_name_plural = 'piezas solicitadas'
        ordering = ['-creada_en']

    def __str__(self):
        return f'{self.cantidad} x {self.nombre_pieza} (orden {self.orden_id})'

    @property
    def subtotal(self):
        return None if self.precio is None else self.precio * self.cantidad

    @property
    def iva(self):
        if self.subtotal is None:
            return None
        return (self.subtotal * self.iva_porcentaje / 100).quantize(Decimal('0.01'))

    @property
    def total(self):
        return None if self.subtotal is None else self.subtotal + self.iva

    @property
    def abierta(self):
        return self.estado in self.ABIERTOS

    @staticmethod
    def _con_categorias(consulta):
        lista = list(consulta.prefetch_related('categorias'))
        for v in lista:
            v.cats = ','.join(str(c.pk) for c in v.categorias.all())
        return lista

    @property
    def proveedores_de_su_categoria(self):
        return self._con_categorias(Proveedor.objects.filter(categorias=self.categoria_id).order_by('nombre')) if self.categoria_id else []

    @property
    def proveedores_con_categorias(self):
        """Todos los proveedores, cada uno con `cats` (ids de sus categorías separados por coma) para filtrarlos al elegir la categoría."""
        lista = list(Proveedor.objects.prefetch_related('categorias').order_by('nombre'))
        for v in lista:
            v.cats = ','.join(str(c.pk) for c in v.categorias.all())
        return lista

    @property
    def otros_proveedores(self):
        return self._con_categorias(Proveedor.objects.exclude(categorias=self.categoria_id).order_by('nombre') if self.categoria_id else Proveedor.objects.order_by('nombre'))


class UsoRepuesto(models.Model):
    """Repuesto del inventario que el técnico usó en una orden. Al registrarlo sale del stock."""

    orden = models.ForeignKey('ordenes.Orden', on_delete=models.PROTECT, related_name='usos_repuesto')
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT, related_name='usos')
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    tecnico = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    pieza = models.ForeignKey(PiezaSolicitada, on_delete=models.SET_NULL, null=True, blank=True, related_name='usos')
    precio_unit = models.DecimalField(
        'precio de venta cobrado', max_digits=10, decimal_places=2, default=0,
        help_text='0 si ya estaba cobrado (la pieza pedida para la orden).',
    )
    creado_en = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-creado_en']
        verbose_name = 'repuesto usado'
        verbose_name_plural = 'repuestos usados'

    def __str__(self):
        return f'{self.cantidad} x {self.producto.nombre} (orden {self.orden_id})'
