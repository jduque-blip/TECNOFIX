from django.core.management.base import BaseCommand

from decimal import Decimal

from core.models import Configuracion
from core.imagenes import POR_SERVICIO
from inventario.models import Categoria, Proveedor
from ordenes.models import Servicio, Tarifa

SERVICIOS = [
    ('Reparación de computadoras', 40),
    ('Celulares y tablets', 30),
    ('Impresoras', 35),
    ('Redes y WiFi', 45),
    ('Consolas y gaming', 50),
    ('Mantenimiento preventivo', 25),
]

# Tarifas de ejemplo de cada servicio (el administrador las cambia desde Tarifas)
TARIFAS_POR_SERVICIO = {
    'Reparación de computadoras': [
        ('Diagnóstico', 5), ('Instalación de sistema', 15), ('Respaldo de datos', 12),
        ('Cambio de pieza (mano de obra)', 20), ('Limpieza interna', 10),
    ],
    'Celulares y tablets': [
        ('Diagnóstico', 5), ('Cambio de pantalla (mano de obra)', 25),
        ('Cambio de batería (mano de obra)', 15), ('Configuración y respaldo', 10),
    ],
    'Impresoras': [('Diagnóstico', 5), ('Limpieza de cabezales', 15), ('Mantenimiento de impresora', 20)],
    'Redes y WiFi': [('Diagnóstico', 5), ('Configuración de router', 20), ('Cableado por punto de red', 12)],
    'Consolas y gaming': [
        ('Diagnóstico', 5), ('Limpieza y cambio de pasta térmica', 25), ('Reparación de HDMI (mano de obra)', 30),
    ],
    'Mantenimiento preventivo': [('Limpieza de equipo', 10), ('Revisión general', 15), ('Optimización del sistema', 12)],
}
TARIFAS_GENERALES = [('Mano de obra', 20)]

# Categorías del inventario (las mismas áreas del taller) y proveedores de tecnología de Ecuador.
# Son sugerencias para empezar: el administrador puede editarlos, borrarlos o agregar los suyos.
# No se cargan teléfonos: cada local debe confirmar el suyo y se escribe desde la pantalla de Proveedores.
CATEGORIAS = ['Computadoras', 'Celulares y tablets', 'Impresoras', 'Redes y WiFi', 'Consolas y gaming', 'Accesorios']
PROVEEDORES = {
    'Cartimex': ['Computadoras', 'Celulares y tablets', 'Impresoras', 'Redes y WiFi', 'Accesorios'],  # mayorista, Guayaquil y Quito
    'Megamicro Mayorista': ['Computadoras'],  # Quito
    'IDC Mayorista': ['Computadoras'],  # Quito
    'Tecnosmart Ecuador': ['Computadoras', 'Redes y WiFi'],  # Guayaquil
    'Serimtec Computación': ['Computadoras'],  # Guayaquil
    'Almacenes Point': ['Computadoras', 'Celulares y tablets', 'Impresoras'],  # Quito
    'Zona Tecnológica': ['Computadoras', 'Accesorios'],  # Quito
}

# Las tarifas de la versión anterior eran de todo el taller: cada una pasa al servicio que le corresponde
TARIFAS_ANTIGUAS = {
    'Diagnóstico': 'Reparación de computadoras',
    'Limpieza de equipo': 'Mantenimiento preventivo',
    'Instalación de sistema': 'Reparación de computadoras',
    'Respaldo de datos': 'Reparación de computadoras',
    'Cambio de pantalla (mano de obra)': 'Celulares y tablets',
}


class Command(BaseCommand):
    help = 'Carga los servicios, las tarifas base y el IVA con los que arranca TecnoFix. Se puede ejecutar varias veces.'

    def handle(self, *args, **opciones):
        for nombre, precio in SERVICIOS:
            servicio, _ = Servicio.objects.get_or_create(nombre=nombre, defaults={'precio_base': precio})
            # Si el servicio todavía no tiene foto, se le pone la de ejemplo (nunca pisa una que ya elegiste)
            if not servicio.imagen and not servicio.foto and nombre in POR_SERVICIO:
                servicio.imagen = POR_SERVICIO[nombre]
                servicio.save(update_fields=['imagen'])
        # 1) Las tarifas antiguas (sin servicio) se mueven a su servicio, conservando el precio que ya tenían
        for nombre, destino in TARIFAS_ANTIGUAS.items():
            antigua = Tarifa.objects.filter(servicio__isnull=True, nombre=nombre).first()
            servicio = Servicio.objects.filter(nombre=destino).first()
            if antigua and servicio and not Tarifa.objects.filter(servicio=servicio, nombre=nombre).exists():
                antigua.servicio = servicio
                antigua.save(update_fields=['servicio'])
        # 2) Se completan las que falten, sin tocar las que el administrador ya cambió
        for nombre_servicio, lista in TARIFAS_POR_SERVICIO.items():
            servicio = Servicio.objects.filter(nombre=nombre_servicio).first()
            if servicio:
                for nombre, precio in lista:
                    Tarifa.objects.get_or_create(servicio=servicio, nombre=nombre, defaults={'precio': precio})
        for nombre, precio in TARIFAS_GENERALES:
            Tarifa.objects.get_or_create(servicio=None, nombre=nombre, defaults={'precio': precio})
        for nombre in CATEGORIAS:
            Categoria.objects.get_or_create(nombre=nombre)
        for nombre, categorias in PROVEEDORES.items():
            proveedor, nuevo = Proveedor.objects.get_or_create(nombre=nombre)
            if nuevo:  # a los que ya existen no se les vuelve a poner una categoría que el administrador quitó
                proveedor.categorias.set(Categoria.objects.filter(nombre__in=categorias))
        iva = Configuracion.iva()
        Configuracion.objects.get_or_create(
            clave=Configuracion.CAPITAL,
            defaults={'valor': Decimal('3000'), 'descripcion': 'Capital con el que arranca el negocio'},
        )  # solo si no existe: no pisa el que ya definió el administrador
        self.stdout.write(self.style.SUCCESS(
            f'Listo: {Servicio.objects.count()} servicios, {Tarifa.objects.count()} tarifas, {Proveedor.objects.count()} proveedores, IVA {iva}%.'
        ))
