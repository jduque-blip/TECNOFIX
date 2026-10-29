import uuid
from decimal import Decimal
from pathlib import Path

from django.db import models


class Configuracion(models.Model):
    """Parámetros del sistema que puede cambiar el administrador (por ejemplo, el IVA)."""

    IVA = 'IVA_PORCENTAJE'
    MARGEN = 'MARGEN_PORCENTAJE'
    DATOS_PAGO = 'DATOS_TRANSFERENCIA'
    CAPITAL = 'CAPITAL_INICIAL'

    clave = models.CharField(max_length=50, primary_key=True)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    descripcion = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = 'configuración'
        verbose_name_plural = 'configuraciones'

    def __str__(self):
        return f'{self.clave} = {self.valor}'

    @classmethod
    def iva(cls):
        """Porcentaje de IVA vigente. Si no existe todavía, se crea con 15."""
        parametro, _ = cls.objects.get_or_create(
            clave=cls.IVA,
            defaults={'valor': Decimal('15'), 'descripcion': 'Porcentaje de IVA que se suma a los presupuestos'},
        )
        return parametro.valor

    @classmethod
    def margen_defecto(cls):
        """Margen de ganancia (sobre el costo) con el que se sugiere el precio de venta. Por defecto 30."""
        parametro, _ = cls.objects.get_or_create(
            clave=cls.MARGEN,
            defaults={'valor': Decimal('30'), 'descripcion': 'Margen de ganancia sobre el costo para sugerir precios de venta'},
        )
        return parametro.valor

    @classmethod
    def capital_inicial(cls):
        """Dinero con el que arranca el negocio (aporte del dueño). 0 si no se ha definido."""
        parametro = cls.objects.filter(clave=cls.CAPITAL).first()
        return parametro.valor if parametro else Decimal('0')

    @classmethod
    def guardar_capital(cls, valor):
        cls.objects.update_or_create(clave=cls.CAPITAL, defaults={'valor': valor, 'descripcion': 'Capital con el que arranca el negocio'})

    @classmethod
    def datos_transferencia(cls):
        """Texto con la cuenta a la que el cliente transfiere (banco, número, titular). Vacío si no se ha escrito."""
        parametro = cls.objects.filter(clave=cls.DATOS_PAGO).first()
        return parametro.descripcion if parametro else ''

    @classmethod
    def guardar_datos_transferencia(cls, texto):
        cls.objects.update_or_create(clave=cls.DATOS_PAGO, defaults={'valor': Decimal('0'), 'descripcion': texto[:200]})


def ruta_foto_portada(instancia, nombre_archivo):
    return f'portada/{uuid.uuid4().hex}{Path(nombre_archivo).suffix.lower()}'


class Portada(models.Model):
    """Imagen grande de la página principal. Hay una sola; el administrador puede cambiarla."""

    foto = models.ImageField('foto', upload_to=ruta_foto_portada, blank=True)

    class Meta:
        verbose_name = 'portada'
        verbose_name_plural = 'portada'

    def __str__(self):
        return 'Imagen de la portada'

    @classmethod
    def actual(cls):
        portada, _ = cls.objects.get_or_create(pk=1)
        return portada

    @property
    def nombre(self):
        return 'portada'  # el ícono de respaldo es una laptop

    @property
    def foto_url(self):
        """La foto subida, o la de ejemplo mientras no se suba ninguna."""
        from .imagenes import PORTADA
        return self.foto.url if self.foto else PORTADA
