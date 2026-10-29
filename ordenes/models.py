import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

def ruta_foto_servicio(instancia, nombre_archivo):
    """Guarda la foto con un nombre aleatorio: evita choques de nombres y nombres raros."""
    return f'servicios/{uuid.uuid4().hex}{Path(nombre_archivo).suffix.lower()}'


def ruta_foto_orden(instancia, nombre_archivo):
    """Fotos que sube el cliente: media/ordenes/<número de orden>/<nombre aleatorio>."""
    return f'ordenes/{instancia.orden_id}/{uuid.uuid4().hex}{Path(nombre_archivo).suffix.lower()}'


def ruta_foto_avance(instancia, nombre_archivo):
    """Fotos que sube el técnico del avance o del resultado: media/ordenes/<orden>/avance/<nombre aleatorio>."""
    return f'ordenes/{instancia.orden_id}/avance/{uuid.uuid4().hex}{Path(nombre_archivo).suffix.lower()}'


DINERO = dict(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])


class Servicio(models.Model):
    foto = models.ImageField('foto', upload_to=ruta_foto_servicio, blank=True, help_text='Foto propia del servicio (JPG, PNG o WEBP).')
    imagen = models.URLField('imagen de ejemplo (vínculo)', max_length=500, blank=True, help_text='Solo se usa si el servicio no tiene foto propia. Si falla, se muestra un ícono.')
    nombre = models.CharField(max_length=100, unique=True)
    precio_base = models.DecimalField(**DINERO)

    class Meta:
        verbose_name_plural = 'servicios'
        ordering = ['id']

    def __str__(self):
        return self.nombre

    @property
    def foto_url(self):
        """Foto que se muestra: la subida por el administrador, o si no hay, la de ejemplo."""
        return self.foto.url if self.foto else self.imagen


class Tarifa(models.Model):
    """Tarifa base de un servicio (por ejemplo, cambio de pantalla). Sin servicio = tarifa general."""

    servicio = models.ForeignKey(
        Servicio, on_delete=models.CASCADE, null=True, blank=True, related_name='tarifas',
        help_text='Vacío = tarifa general, que aplica a todos los servicios.',
    )
    nombre = models.CharField(max_length=100)
    precio = models.DecimalField(**DINERO)

    class Meta:
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['servicio', 'nombre'], name='tarifa_unica_por_servicio'),
            models.UniqueConstraint(fields=['nombre'], condition=Q(servicio__isnull=True), name='tarifa_general_unica'),
        ]

    def __str__(self):
        return f'{self.nombre} ({self.servicio or "general"}) ${self.precio}'


class Orden(models.Model):
    """Solicitud de servicio de un cliente sobre un equipo."""

    class Estado(models.TextChoices):
        RECIBIDO = 'RECIBIDO', 'Recibido'
        DIAGNOSTICO = 'DIAGNOSTICO', 'En diagnóstico'
        REPARACION = 'REPARACION', 'En reparación'
        LISTO = 'LISTO', 'Listo'
        ENTREGADO = 'ENTREGADO', 'Entregado'
        CANCELADA = 'CANCELADA', 'Cancelada'
        RECHAZADA = 'RECHAZADA', 'Rechazada'

    class Prioridad(models.TextChoices):
        NORMAL = 'NORMAL', 'Normal'
        ALTA = 'ALTA', 'Alta'
        URGENTE = 'URGENTE', 'Urgente'

    cliente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='ordenes_como_cliente',
        limit_choices_to={'rol': 'CLIENTE'},
    )
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ordenes_como_tecnico',
        limit_choices_to={'rol': 'TECNICO'},
    )
    servicio = models.ForeignKey(Servicio, on_delete=models.PROTECT)
    modelo = models.CharField('modelo del equipo', max_length=100)
    problema = models.TextField(blank=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.RECIBIDO)
    prioridad = models.CharField(max_length=8, choices=Prioridad.choices, default=Prioridad.NORMAL)
    fecha_estimada = models.DateField('fecha estimada de entrega', null=True, blank=True)
    espera_repuesto = models.BooleanField(default=False)
    entrega_autorizada = models.BooleanField(default=False)
    fecha_prometida = models.DateField('primera fecha prometida al cliente', null=True, blank=True)
    estado_previo = models.CharField('estado antes de cerrarse', max_length=12, blank=True)
    motivo_cierre = models.CharField('motivo de la cancelación o el rechazo', max_length=300, blank=True)
    descuento = models.DecimalField('descuento acordado', max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(Decimal('0'))])
    descuento_sin_mano = models.BooleanField('sin cobrar la mano de obra', default=False)
    descuento_motivo = models.CharField(max_length=200, blank=True)
    cargo_cancelacion = models.DecimalField('mano de obra por cancelación', max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(Decimal('0'))],
                                            help_text='Sin IVA. Lo que paga el cliente si cancela con el trabajo ya en proceso.')
    resultado = models.CharField('cómo terminó (si fue cancelada o rechazada)', max_length=12, blank=True,
                                 help_text='CANCELADA o RECHAZADA. Se conserva aunque el equipo se entregue: es lo que se muestra.')
    iva_porcentaje = models.DecimalField('IVA de esta orden', max_digits=5, decimal_places=2, null=True, blank=True,
                                         help_text='Se fija al crear la orden: un cambio de IVA posterior no la afecta.')
    asignada_en = models.DateTimeField('fecha de asignación al técnico', null=True, blank=True)
    creada_en = models.DateTimeField(default=timezone.now)
    entregada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-creada_en']
        verbose_name = 'orden'
        constraints = [
            models.CheckConstraint(
                condition=~Q(estado='ENTREGADO') | Q(entrega_autorizada=True),
                name='entregada_requiere_autorizacion',
            ),
            models.CheckConstraint(
                condition=Q(estado='ENTREGADO', entregada_en__isnull=False)
                | (~Q(estado='ENTREGADO') & Q(entregada_en__isnull=True)),
                name='fecha_de_entrega_solo_si_entregada',
            ),
            models.CheckConstraint(condition=Q(descuento__gte=0), name='descuento_no_negativo'),
            models.CheckConstraint(condition=Q(cargo_cancelacion__gte=0), name='cargo_cancelacion_no_negativo'),
        ]

    def __str__(self):
        return f'Orden #{self.pk} · {self.modelo}'

    def registrar(self, evento, usuario=None):
        """Anota un cambio en el historial de la orden."""
        return HistorialOrden.objects.create(orden=self, usuario=usuario, evento=evento)

    ORDEN_ESTADOS = ['RECIBIDO', 'DIAGNOSTICO', 'REPARACION', 'LISTO', 'ENTREGADO']
    CERRADAS = ['ENTREGADO', 'CANCELADA', 'RECHAZADA']  # ya no ocupan al taller

    def save(self, *args, **kwargs):
        if self._state.adding and self.iva_porcentaje is None:
            from core.models import Configuracion
            self.iva_porcentaje = Configuracion.iva()
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = list(kwargs['update_fields']) + ['iva_porcentaje']
        super().save(*args, **kwargs)

    @property
    def iva_efectivo(self):
        """IVA con el que se cobra esta orden: el que tenía al crearse."""
        if self.iva_porcentaje is not None:
            return self.iva_porcentaje
        from core.models import Configuracion
        return Configuracion.iva()

    @property
    def cierre_visible(self):
        """'CANCELADA' o 'RECHAZADA' si la orden terminó así (aunque ya se haya entregado el equipo); si no, ''."""
        if self.estado in (self.Estado.CANCELADA, self.Estado.RECHAZADA):
            return self.estado
        return self.resultado

    @property
    def congelada(self):
        """Cancelada por el cliente o rechazada por el administrador: el técnico ya no puede hacer nada."""
        return self.estado in (self.Estado.CANCELADA, self.Estado.RECHAZADA)

    def nombre_estado(self, usuario=None):
        """Nombre del estado para quien lo ve: al técnico le aparece «Rechazada» en ambos casos."""
        base = self.cierre_visible or self.estado
        if base == self.Estado.CANCELADA and usuario is not None and getattr(usuario, 'es_tecnico', False):
            base = self.Estado.RECHAZADA
        nombre = dict(self.Estado.choices)[base]
        if base != self.estado and self.estado == self.Estado.ENTREGADO:
            nombre += ' · equipo entregado'
        elif base != self.estado and self.estado == self.Estado.LISTO:
            nombre += ' · listo para retirar'
        return nombre

    @classmethod
    def posicion(cls, estado):
        return cls.ORDEN_ESTADOS.index(estado)

    @property
    def piezas_sin_usar(self):
        """Piezas que ya llegaron y están en el inventario, pero el técnico todavía no registró como usadas."""
        return [z for z in self.piezas_solicitadas.filter(estado='DISPONIBLE', producto__isnull=False) if not z.usos.exists()]

    @property
    def motivo_sin_avance(self):
        """Por qué el técnico no puede avanzar el estado todavía (None si puede)."""
        if self.estado == self.Estado.ENTREGADO:
            return None
        if self.congelada:
            return 'Esta orden fue rechazada o cancelada. Solo el administrador puede reabrirla; espera sus indicaciones.'
        if self.espera_repuesto:
            return 'Hay una pieza en trámite. La orden pasa sola a «En reparación» cuando la pieza llegue y quede registrada en el inventario.'
        return None

    @property
    def estados_siguientes(self):
        """Estados a los que el técnico puede avanzar (nunca hacia atrás ni hasta Entregado).

        Con una pieza en trámite no avanza; y no marca «Listo» sin registrar las piezas que usó."""
        if self.motivo_sin_avance:
            return []
        sin_usar = bool(self.piezas_sin_usar)
        return [
            (v, n) for v, n in self.Estado.choices
            if v in self.ORDEN_ESTADOS and v != 'ENTREGADO' and self.posicion(v) > self.posicion(self.estado) and not (sin_usar and v == 'LISTO')
        ]

    @property
    def factura(self):
        from . import factura
        return factura.calcular(self)

    @property
    def tiene_descuento(self):
        return bool((self.descuento or 0) > 0 or self.descuento_sin_mano)

    @property
    def total_a_pagar(self):
        """Lo que debe pagar el cliente: ver la factura general."""
        return self.factura['total']

    @property
    def estados_anteriores(self):
        """Estados a los que solo puede volver con permiso del administrador."""
        if self.congelada or self.estado == self.Estado.ENTREGADO:
            return []
        return [(v, n) for v, n in self.Estado.choices if v in self.ORDEN_ESTADOS and self.posicion(v) < self.posicion(self.estado)]

    @property
    def entrega_a_tiempo(self):
        """Entregada en o antes de la última fecha acordada. None si no está entregada o no tenía fecha."""
        if self.estado != self.Estado.ENTREGADO or not self.fecha_estimada or not self.entregada_en:
            return None
        return timezone.localtime(self.entregada_en).date() <= self.fecha_estimada

    @property
    def cumplimiento(self):
        """Texto para reportes: A tiempo, Con retraso, En curso, Sin fecha o No aplica."""
        if self.congelada:
            return 'No aplica'
        if not self.fecha_estimada:
            return 'Sin fecha'
        if self.estado != self.Estado.ENTREGADO:
            return 'Con retraso' if self.atrasada else 'En curso'
        return 'A tiempo' if self.entrega_a_tiempo else 'Con retraso'

    @property
    def presupuesto_aceptado(self):
        return self.presupuestos.filter(estado=Presupuesto.Estado.ACEPTADO).first()

    @property
    def cliente_puede_modificar(self):
        """El cliente puede corregir o eliminar su solicitud solo mientras no tenga técnico asignado."""
        return self.tecnico_id is None and self.estado == self.Estado.RECIBIDO

    @property
    def dias_para_entrega(self):
        """Días que faltan para la fecha estimada (negativo si ya pasó). None si no hay fecha."""
        if not self.fecha_estimada:
            return None
        return (self.fecha_estimada - timezone.localdate()).days

    @property
    def atrasada(self):
        """Pasó la fecha estimada y todavía no se entrega."""
        return bool(
            self.fecha_estimada
            and self.estado not in self.CERRADAS
            and self.fecha_estimada < timezone.localdate()
        )


class OrdenFoto(models.Model):
    orden = models.ForeignKey(Orden, on_delete=models.CASCADE, related_name='fotos')
    imagen = models.ImageField(upload_to=ruta_foto_orden)

    class Meta:
        verbose_name = 'foto de la orden'
        verbose_name_plural = 'fotos de la orden'


class FotoAvance(models.Model):
    """Foto del avance o de cómo quedó el equipo, subida por el técnico para que el cliente la vea."""

    orden = models.ForeignKey(Orden, on_delete=models.CASCADE, related_name='fotos_avance')
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    imagen = models.ImageField(upload_to=ruta_foto_avance)
    creada_en = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['creada_en']
        verbose_name = 'foto de avance'
        verbose_name_plural = 'fotos de avance'


class NotaOrden(models.Model):
    """Nota del técnico sobre el avance. Puede ser interna o visible para el cliente."""

    orden = models.ForeignKey(Orden, on_delete=models.CASCADE, related_name='notas')
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    texto = models.TextField()
    visible_cliente = models.BooleanField('visible para el cliente', default=True)
    creada_en = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-creada_en']
        verbose_name = 'nota de la orden'
        verbose_name_plural = 'notas de órdenes'

    def __str__(self):
        return f'Nota de la orden #{self.orden_id}: {self.texto[:40]}'


class CambioFecha(models.Model):
    """Cada vez que se mueve la fecha de entrega que ya se le había dicho al cliente, con el motivo."""

    orden = models.ForeignKey(Orden, on_delete=models.CASCADE, related_name='cambios_fecha')
    fecha_anterior = models.DateField()
    fecha_nueva = models.DateField(null=True, blank=True)
    motivo = models.CharField(max_length=200)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    creado_en = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['creado_en', 'id']
        verbose_name = 'cambio de fecha de entrega'
        verbose_name_plural = 'cambios de fecha de entrega'

    @property
    def dias(self):
        return (self.fecha_nueva - self.fecha_anterior).days if self.fecha_nueva else None


class RepuestoDanado(models.Model):
    """Informe de un repuesto que se dañó durante el trabajo: qué fue, cuánto costó, por qué y cuándo."""

    orden = models.ForeignKey(Orden, on_delete=models.CASCADE, related_name='repuestos_danados')
    producto = models.ForeignKey('inventario.Producto', on_delete=models.PROTECT, related_name='danos')
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    motivo = models.CharField(max_length=200)
    perdida = models.DecimalField('pérdida', max_digits=10, decimal_places=2)
    tecnico = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    fecha = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-fecha', '-id']
        verbose_name = 'repuesto dañado'
        verbose_name_plural = 'repuestos dañados'


class HistorialOrden(models.Model):
    """Cada cambio de una orden, con fecha y responsable."""

    orden = models.ForeignKey(Orden, on_delete=models.CASCADE, related_name='historial')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    evento = models.CharField(max_length=200)
    fecha = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'historial de la orden'
        verbose_name_plural = 'historial de órdenes'

    def __str__(self):
        return f'{self.evento} ({self.fecha:%d/%m/%Y %H:%M})'


class Presupuesto(models.Model):
    """Cotización que el técnico envía al cliente."""

    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        ACEPTADO = 'ACEPTADO', 'Aceptado'
        RECHAZADO = 'RECHAZADO', 'Rechazado'

    orden = models.ForeignKey(Orden, on_delete=models.CASCADE, related_name='presupuestos')
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    iva_porcentaje = models.DecimalField(max_digits=5, decimal_places=2, default=15)
    iva = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    observacion = models.TextField('observación', blank=True)
    espera_pieza = models.BooleanField(
        'proforma de diagnóstico', default=False,
        help_text='Solo diagnóstico y mano de obra: la pieza se cotiza aparte cuando llegue la respuesta del proveedor.',
    )
    creado_en = models.DateTimeField(default=timezone.now)
    respondido_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-creado_en']
        constraints = [
            models.CheckConstraint(condition=Q(total=F('subtotal') + F('iva')), name='total_es_subtotal_mas_iva'),
            models.UniqueConstraint(
                fields=['orden'], condition=Q(estado='PENDIENTE'), name='un_presupuesto_pendiente_por_orden'
            ),
        ]

    def __str__(self):
        return f'Presupuesto #{self.pk} de la orden {self.orden_id}'

    @property
    def con_descuento(self):
        """Lo que verá pagar el cliente si acepta: el descuento acordado de la orden restado de este presupuesto (el IVA baja en proporción)."""
        from decimal import ROUND_HALF_UP
        if self.estado == self.Estado.ACEPTADO or not self.orden.tiene_descuento or self.subtotal <= 0:
            return None
        mano = sum((i.subtotal for i in self.items.all() if i.tipo == 'SERVICIO'), Decimal('0')) if self.orden.descuento_sin_mano else Decimal('0')
        desc = min((self.orden.descuento or Decimal('0')) + mano, self.subtotal)
        if desc <= 0:
            return None
        subtotal = self.subtotal - desc
        iva = (self.iva * subtotal / self.subtotal).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return {'descuento': desc, 'subtotal': subtotal, 'iva': iva, 'total': subtotal + iva}


class PresupuestoItem(models.Model):
    """Línea del presupuesto: una tarifa (servicio) o un repuesto del inventario."""

    class Tipo(models.TextChoices):
        SERVICIO = 'SERVICIO', 'Servicio'
        REPUESTO = 'REPUESTO', 'Repuesto'

    presupuesto = models.ForeignKey(Presupuesto, on_delete=models.CASCADE, related_name='items')
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    tarifa = models.ForeignKey(Tarifa, on_delete=models.PROTECT, null=True, blank=True)
    producto = models.ForeignKey(
        'inventario.Producto', on_delete=models.PROTECT, null=True, blank=True
    )
    cantidad = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    precio_unit = models.DecimalField('precio por unidad', max_digits=10, decimal_places=2, default=0)
    en_uso_en = models.DateTimeField('marcado en uso', null=True, blank=True)
    devuelto_en = models.DateTimeField('devuelto al inventario (no se usó)', null=True, blank=True)

    class Meta:
        verbose_name = 'línea del presupuesto'
        verbose_name_plural = 'líneas del presupuesto'
        constraints = [
            models.CheckConstraint(
                condition=Q(tipo='SERVICIO', tarifa__isnull=False, producto__isnull=True)
                | Q(tipo='REPUESTO', producto__isnull=False, tarifa__isnull=True),
                name='linea_servicio_o_repuesto',
            ),
        ]

    @property
    def subtotal(self):
        return self.cantidad * self.precio_unit

    def save(self, *args, **kwargs):
        # Copia el precio vigente, para que un cambio posterior no altere presupuestos viejos.
        if not self.precio_unit:
            if self.tipo == self.Tipo.SERVICIO and self.tarifa:
                self.precio_unit = self.tarifa.precio
            elif self.tipo == self.Tipo.REPUESTO and self.producto:
                self.precio_unit = self.producto.precio_venta
        super().save(*args, **kwargs)


class Pago(models.Model):
    """Pago en efectivo o por transferencia. La transferencia lleva comprobante y lo valida el administrador."""

    class Metodo(models.TextChoices):
        EFECTIVO = 'EFECTIVO', 'Efectivo'
        TRANSFERENCIA = 'TRANSFERENCIA', 'Transferencia'

    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente de validar'
        CONFIRMADO = 'CONFIRMADO', 'Confirmado'
        RECHAZADO = 'RECHAZADO', 'Rechazado'

    orden = models.ForeignKey(Orden, on_delete=models.PROTECT, related_name='pagos')
    metodo = models.CharField(max_length=14, choices=Metodo.choices)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    comprobante = models.FileField(upload_to='comprobantes/%Y/%m/', blank=True)
    referencia = models.CharField(max_length=60, blank=True)
    motivo_rechazo = models.TextField(blank=True)
    validado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pagos_validados',
        limit_choices_to={'rol': 'ADMIN'},
    )
    validado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-creado_en']
        constraints = [
            models.CheckConstraint(condition=Q(monto__gt=0), name='pago_monto_positivo'),
            models.CheckConstraint(
                condition=~Q(metodo='TRANSFERENCIA') | ~Q(comprobante=''),
                name='transferencia_requiere_comprobante',
            ),
            models.CheckConstraint(
                condition=Q(estado='PENDIENTE') | (Q(validado_por__isnull=False) & Q(validado_en__isnull=False)),
                name='pago_validado_tiene_responsable',
            ),
            models.CheckConstraint(
                condition=~Q(estado='RECHAZADO') | ~Q(motivo_rechazo=''),
                name='rechazo_requiere_motivo',
            ),
            models.UniqueConstraint(
                fields=['orden'], condition=Q(estado='CONFIRMADO'), name='un_pago_confirmado_por_orden'
            ),
            models.UniqueConstraint(
                fields=['orden'], condition=Q(estado='PENDIENTE'), name='un_pago_pendiente_por_orden'
            ),
        ]

    def __str__(self):
        return f'Pago {self.get_metodo_display()} de ${self.monto} (orden {self.orden_id})'


class SolicitudEstado(models.Model):
    """El técnico no puede retroceder el estado de una orden por su cuenta: lo pide y el administrador decide."""

    class Estado(models.TextChoices):
        PENDIENTE = 'PENDIENTE', 'Pendiente'
        APROBADA = 'APROBADA', 'Aprobada'
        RECHAZADA = 'RECHAZADA', 'Rechazada'

    orden = models.ForeignKey(Orden, on_delete=models.CASCADE, related_name='solicitudes_estado')
    tecnico = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    estado_actual = models.CharField(max_length=12, choices=Orden.Estado.choices)
    estado_pedido = models.CharField(max_length=12, choices=Orden.Estado.choices)
    motivo = models.CharField(max_length=300)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    respuesta = models.CharField(max_length=300, blank=True)
    creada_en = models.DateTimeField(default=timezone.now)
    respondida_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-creada_en']
        verbose_name = 'solicitud de cambio de estado'
        verbose_name_plural = 'solicitudes de cambio de estado'
        constraints = [
            models.UniqueConstraint(fields=['orden'], condition=Q(estado='PENDIENTE'), name='una_solicitud_pendiente_por_orden'),
        ]

    def __str__(self):
        return f'Solicitud de {self.get_estado_actual_display()} a {self.get_estado_pedido_display()} (orden {self.orden_id})'
