import re
from decimal import Decimal

from django import forms

from core.models import Configuracion
from ordenes.forms import _precio, _precio_valido, _validar_foto
from usuarios.forms import CELULAR_EC, FIJO_EC

from . import precios as calculo
from .models import Categoria, Producto, Proveedor
from .servicios import MAX_UNIDADES

CODIGO = re.compile(r'^[A-Z0-9][A-Z0-9._-]{1,29}$')


def _entero(etiqueta, minimo=0, requerido=True):
    return forms.IntegerField(
        label=etiqueta, min_value=minimo, max_value=MAX_UNIDADES, required=requerido,
        error_messages={
            'required': f'Escribe {etiqueta.lower()}.',
            'invalid': 'Escribe un número entero.',
            'min_value': f'El mínimo es {minimo}.',
            'max_value': f'El máximo es {MAX_UNIDADES}.',
        },
    )


class MargenForm(forms.Form):
    valor = forms.DecimalField(
        max_digits=5, decimal_places=2, localize=False, min_value=0, max_value=500,
        error_messages={
            'required': 'Escribe el margen de ganancia.',
            'invalid': 'Escribe un número, por ejemplo 30.',
            'min_value': 'El margen debe estar entre 0 y 500.',
            'max_value': 'El margen debe estar entre 0 y 500.',
            'max_decimal_places': 'Usa máximo 2 decimales.',
        },
    )


class CategoriaForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ['nombre']

    def clean_nombre(self):
        nombre = ' '.join(self.cleaned_data['nombre'].split())
        if len(nombre) < 3:
            raise forms.ValidationError('El nombre es muy corto.')
        if Categoria.objects.filter(nombre__iexact=nombre).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Ya existe una categoría con ese nombre.')
        return nombre

    def validate_unique(self):
        pass  # se valida sin distinguir mayúsculas en clean_nombre


class ContactoProveedorForm(forms.ModelForm):
    """Teléfono y días de entrega: lo que más cambia de un proveedor."""

    dias_entrega = forms.IntegerField(
        label='Días de entrega', required=False, min_value=0, max_value=365,
        error_messages={'invalid': 'Escribe un número entero de días.', 'min_value': 'No puede ser negativo.', 'max_value': 'El máximo es 365 días.'},
    )

    class Meta:
        model = Proveedor
        fields = ['telefono', 'dias_entrega']

    def clean_telefono(self):
        telefono = re.sub(r'[\s\-().]', '', self.cleaned_data['telefono'])
        if telefono.startswith('+593'):
            telefono = '0' + telefono[4:]
        if telefono and not (CELULAR_EC.match(telefono) or FIJO_EC.match(telefono)):
            raise forms.ValidationError('Escribe un teléfono válido de Ecuador, por ejemplo 0991234567.')
        return telefono


class ProveedorForm(ContactoProveedorForm):
    categorias = forms.ModelMultipleChoiceField(Categoria.objects.all(), required=False, label='Categorías')

    class Meta:
        model = Proveedor
        fields = ['nombre', 'telefono', 'dias_entrega', 'categorias']

    def clean_nombre(self):
        nombre = ' '.join(self.cleaned_data['nombre'].split())
        if len(nombre) < 3:
            raise forms.ValidationError('El nombre es muy corto.')
        if Proveedor.objects.filter(nombre__iexact=nombre).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Ya existe un proveedor con ese nombre.')
        return nombre

    def validate_unique(self):
        pass  # se valida sin distinguir mayúsculas en clean_nombre


class ProductoForm(forms.ModelForm):
    """Producto con hasta dos proveedores y el precio que ofrece cada uno."""

    precio_venta = _precio('Costo de venta')
    costo_pedido = forms.DecimalField(
        label='Costo de pedido', required=False, max_digits=10, decimal_places=2, min_value=0, localize=False,
        error_messages={'invalid': 'Escribe un costo válido, por ejemplo 8.50.'},
    )
    margen_propio = forms.DecimalField(
        label='Margen propio', required=False, max_digits=5, decimal_places=2, min_value=0, max_value=500, localize=False,
        error_messages={'invalid': 'Escribe un número, por ejemplo 35.', 'min_value': 'Debe estar entre 0 y 500.', 'max_value': 'Debe estar entre 0 y 500.'},
    )
    stock_minimo = _entero('Stock mínimo')
    stock_inicial = _entero('Stock inicial', requerido=False)
    proveedor1 = forms.ModelChoiceField(Proveedor.objects.prefetch_related('categorias'), required=False, label='Proveedor 1')
    precio1 = _precio('Precio del proveedor 1')
    proveedor2 = forms.ModelChoiceField(Proveedor.objects.prefetch_related('categorias'), required=False, label='Proveedor 2')
    precio2 = _precio('Precio del proveedor 2')
    imagen = forms.ImageField(
        required=False, label='Imagen',
        error_messages={'invalid_image': 'El archivo no es una imagen válida. Usa JPG, PNG o WEBP.'},
    )

    class Meta:
        model = Producto
        fields = ['nombre', 'codigo', 'marca', 'categoria', 'imagen', 'precio_venta', 'costo_pedido', 'margen_propio', 'stock_minimo', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['categoria'].empty_label = 'Sin categoría'
        if 'stock_inicial' in self.fields and not self.is_bound:
            self.initial['stock_inicial'] = 0  # al empezar se parte de cero y se sube con las reposiciones
        self.fields['precio_venta'].required = False  # si se deja vacío se usa el sugerido
        self.margen_general = Configuracion.margen_defecto()
        self.costo_original = self.instance.costo_pedido if self.instance.pk else None  # el formulario puede dejar el campo en blanco
        if self.instance.margen_propio is None and not self.is_bound:
            self.initial['margen_propio'] = self.margen_general  # el margen arranca con el general y se puede cambiar
        self.fields['precio1'].required = False
        self.fields['precio2'].required = False
        if self.instance.pk:
            del self.fields['stock_inicial']
            if not self.is_bound:
                for n, c in enumerate(self.instance.cotizaciones.select_related('proveedor').order_by('pk')[:2], start=1):
                    self.initial[f'proveedor{n}'] = c.proveedor_id
                    self.initial[f'precio{n}'] = c.precio

    def clean_nombre(self):
        nombre = ' '.join(self.cleaned_data['nombre'].split())
        if len(nombre) < 3:
            raise forms.ValidationError('El nombre es muy corto.')
        return nombre

    def clean_codigo(self):
        codigo = self.cleaned_data['codigo'].strip().upper()
        if not CODIGO.match(codigo):
            raise forms.ValidationError('Usa 2 a 30 letras, números, punto, guion o guion bajo (sin espacios).')
        if Producto.objects.filter(codigo__iexact=codigo).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Ya existe un producto con ese código.')
        return codigo

    def clean_imagen(self):
        return _validar_foto(self.cleaned_data.get('imagen'))

    def clean_precio_venta(self):
        return _precio_valido(self.cleaned_data.get('precio_venta'))

    def clean(self):
        datos = super().clean()
        vistos = set()
        for n in (1, 2):
            proveedor, precio = datos.get(f'proveedor{n}'), datos.get(f'precio{n}')
            if proveedor and precio is None and f'precio{n}' not in self.errors:
                self.add_error(f'precio{n}', 'Escribe el precio de este proveedor.')
            elif precio is not None and not proveedor and f'proveedor{n}' not in self.errors:
                self.add_error(f'proveedor{n}', 'Elige el proveedor de ese precio.')
            elif proveedor and precio is not None:
                if precio <= 0:
                    self.add_error(f'precio{n}', 'El precio debe ser mayor que 0.')
                if proveedor.pk in vistos:
                    self.add_error(f'proveedor{n}', 'No repitas el mismo proveedor.')
                vistos.add(proveedor.pk)
        nombre, marca = datos.get('nombre'), ' '.join((datos.get('marca') or '').split())
        if nombre and 'nombre' not in self.errors:
            repetido = Producto.objects.filter(nombre__iexact=nombre, marca__iexact=marca).exclude(pk=self.instance.pk).first()
            if repetido:
                estado = ' (está desactivado: actívalo en lugar de crear otro)' if not repetido.activo else ''
                self.add_error('nombre', f'Ya existe «{repetido.nombre}»{" de la marca " + repetido.marca if repetido.marca else " sin marca"}{estado}. '
                                         'Para tener más unidades repón su stock; si es de otra marca, escribe la marca.')
        datos['marca'] = marca
        # Un margen igual al general no es "propio": así, si el general cambia, el producto lo sigue
        if datos.get('margen_propio') is not None and datos['margen_propio'] == self.margen_general:
            datos['margen_propio'] = None
        costo, venta = self.costo_final(), datos.get('precio_venta')
        if venta is None and 'precio_venta' not in self.errors:
            margen = datos['margen_propio'] if datos.get('margen_propio') is not None else self.margen_general
            venta = calculo.sugerido(costo, margen)
            if venta is None:
                self.add_error('precio_venta', 'Escribe el costo de venta (o el precio de un proveedor para calcularlo).')
            else:
                datos['precio_venta'] = venta
        if venta is not None and costo is not None and 'costo_pedido' not in self.errors and venta < costo:
            self.add_error('precio_venta', 'El costo de venta no puede ser menor que el costo de pedido.')
        return datos

    def cotizaciones(self):
        d = self.cleaned_data
        return [(d[f'proveedor{n}'], d[f'precio{n}']) for n in (1, 2) if d.get(f'proveedor{n}') and d.get(f'precio{n}')]

    def _cotizaciones_cambiaron(self):
        actuales = {c.proveedor_id: c.precio for c in self.instance.cotizaciones.all()}
        return actuales != {proveedor.pk: precio for proveedor, precio in self.cotizaciones()}

    def costo_final(self):
        """El costo de pedido es el precio del proveedor más barato. Sin proveedores, el que se escriba (o el que ya tenía).

        Al editar un producto cuyos proveedores no cambiaron se respeta el costo que ya tiene (por ejemplo, el último pedido)."""
        d = self.cleaned_data
        precios = [precio for _, precio in self.cotizaciones()]
        if precios:
            if self.instance.pk and not self._cotizaciones_cambiaron():
                return self.costo_original
            return min(precios)
        if d.get('costo_pedido') is not None:
            return d['costo_pedido']
        return self.costo_original if self.costo_original is not None else Decimal('0')


class ReponerForm(forms.Form):
    proveedor = forms.ModelChoiceField(Proveedor.objects.none(), label='Proveedor', error_messages={'required': 'Elige el proveedor.', 'invalid_choice': 'Elige un proveedor de este producto.'})
    cantidad = _entero('Cantidad', minimo=1)
    precio_unit = _precio('Precio por unidad')
    observacion = forms.CharField(required=False, max_length=300, label='Observación')
    actualizar_precio = forms.BooleanField(required=False, label='Actualizar el precio de venta al sugerido con el nuevo costo')

    def __init__(self, *args, producto, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['proveedor'].queryset = Proveedor.objects.filter(productoproveedor__producto=producto).distinct()

    def clean_precio_unit(self):
        return _precio_valido(self.cleaned_data.get('precio_unit'))


class AjusteForm(forms.Form):
    stock_real = _entero('Stock real')
    error_registro = forms.BooleanField(required=False, label='Fue un error de registro (no es una pérdida)')
    motivo = forms.CharField(max_length=150, label='Motivo', error_messages={'required': 'Explica el motivo del ajuste.'})

    def clean_motivo(self):
        motivo = ' '.join(self.cleaned_data['motivo'].split())
        if len(motivo) < 4:
            raise forms.ValidationError('Explica el motivo del ajuste.')
        return motivo


class CorregirPedidoForm(forms.Form):
    """El administrador anotó mal la cantidad del último pedido: se corrige sin que cuente como pérdida."""
    cantidad_correcta = _entero('Cantidad correcta', minimo=1)
    motivo = forms.CharField(max_length=100, label='Qué pasó', error_messages={'required': 'Explica qué pasó (por ejemplo, escribí 4 y eran 2).'})

    def clean_motivo(self):
        motivo = ' '.join(self.cleaned_data['motivo'].split())
        if len(motivo) < 4:
            raise forms.ValidationError('Explica qué pasó (por ejemplo, escribí 4 y eran 2).')
        return motivo
