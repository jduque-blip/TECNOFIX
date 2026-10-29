from decimal import Decimal

from comunicacion import avisos
from django import forms
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from usuarios.models import Usuario

from . import carga, fechas
from .models import FotoAvance, NotaOrden, Orden, OrdenFoto, Servicio, SolicitudEstado, Tarifa

PRECIO_MAX = Decimal('99999999.99')


def _precio(etiqueta='Precio'):
    return forms.DecimalField(
        label=etiqueta, max_digits=10, decimal_places=2, localize=False,
        error_messages={
            'required': 'Escribe el precio.',
            'invalid': 'Escribe un precio válido, por ejemplo 12.50.',
            'max_digits': 'El precio es demasiado grande.',
            'max_decimal_places': 'Usa máximo 2 decimales.',
            'max_whole_digits': 'El precio es demasiado grande.',
        },
    )


def _precio_valido(valor):
    if valor is None:
        return valor
    if valor <= 0:
        raise forms.ValidationError('El precio debe ser mayor que 0.')
    return valor


class TarifaForm(forms.ModelForm):
    """Tarifa de un servicio (o general si no se indica servicio)."""

    precio = _precio()

    class Meta:
        model = Tarifa
        fields = ['nombre', 'precio']

    def __init__(self, *args, servicio=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.servicio = servicio

    def clean_nombre(self):
        nombre = ' '.join(self.cleaned_data['nombre'].split())
        if len(nombre) < 3:
            raise forms.ValidationError('El nombre es muy corto.')
        repetida = Tarifa.objects.filter(servicio=self.servicio, nombre__iexact=nombre).exclude(pk=self.instance.pk)
        if repetida.exists():
            raise forms.ValidationError('Ya existe una tarifa con ese nombre en este servicio.')
        return nombre

    def clean_precio(self):
        return _precio_valido(self.cleaned_data.get('precio'))

    def validate_unique(self):
        pass  # se valida sin distinguir mayúsculas en clean_nombre

    def save(self, commit=True):
        tarifa = super().save(commit=False)
        tarifa.servicio = self.servicio
        if commit:
            tarifa.save()
        return tarifa


FORMATOS_FOTO = {'JPEG', 'PNG', 'WEBP'}
FOTO_MAX_MB = 3


def _validar_foto(archivo):
    """La foto debe ser una imagen real JPG, PNG o WEBP de hasta 3 MB."""
    if archivo is None or not hasattr(archivo, 'image'):
        return archivo
    if archivo.size > FOTO_MAX_MB * 1024 * 1024:
        raise forms.ValidationError(f'La foto pesa demasiado. El máximo es {FOTO_MAX_MB} MB.')
    if archivo.image.format not in FORMATOS_FOTO:
        raise forms.ValidationError('Usa una foto JPG, PNG o WEBP.')
    return archivo


def _campo_foto(requerida=False):
    return forms.ImageField(
        required=requerida, label='Foto',
        error_messages={'invalid_image': 'El archivo no es una imagen válida. Usa JPG, PNG o WEBP.', 'required': 'Elige una foto.'},
    )


class ServicioForm(forms.ModelForm):
    precio_base = _precio('Precio base')
    foto = _campo_foto()

    class Meta:
        model = Servicio
        fields = ['foto', 'nombre', 'precio_base']

    def clean_foto(self):
        return _validar_foto(self.cleaned_data.get('foto'))

    def clean_nombre(self):
        nombre = ' '.join(self.cleaned_data['nombre'].split())
        if len(nombre) < 3:
            raise forms.ValidationError('El nombre es muy corto.')
        if Servicio.objects.filter(nombre__iexact=nombre).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Ya existe un servicio con ese nombre.')
        return nombre

    def clean_precio_base(self):
        return _precio_valido(self.cleaned_data.get('precio_base'))

    def validate_unique(self):
        pass


class FotoForm(forms.Form):
    """Cambio de la foto de un servicio que ya existe."""

    foto = _campo_foto(requerida=True)

    def clean_foto(self):
        return _validar_foto(self.cleaned_data.get('foto'))


class PrecioForm(forms.Form):
    """Cambio rápido del precio de una tarifa o de un servicio."""

    precio = _precio()

    def clean_precio(self):
        return _precio_valido(self.cleaned_data.get('precio'))


class IvaForm(forms.Form):
    valor = forms.DecimalField(
        max_digits=5, decimal_places=2, localize=False, min_value=0, max_value=100,
        error_messages={
            'required': 'Escribe el porcentaje de IVA.',
            'invalid': 'Escribe un número, por ejemplo 15.',
            'min_value': 'El IVA debe estar entre 0 y 100.',
            'max_value': 'El IVA debe estar entre 0 y 100.',
            'max_decimal_places': 'Usa máximo 2 decimales.',
        },
    )


# ---------------------------------------------------------------- órdenes

MAX_FOTOS_ORDEN = 4


class VariasFotosInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class VariasFotosField(forms.FileField):
    """Campo que acepta varias imágenes a la vez; cada una se valida como foto."""

    def __init__(self, *args, maximo=MAX_FOTOS_ORDEN, **kwargs):
        kwargs.setdefault('widget', VariasFotosInput())
        self.maximo = maximo
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        archivos = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        archivos = [a for a in archivos if a]
        if len(archivos) > self.maximo:
            raise forms.ValidationError(f'Puedes subir máximo {self.maximo} fotos.')
        limpias = []
        for archivo in archivos:
            validada = forms.ImageField(
                error_messages={'invalid_image': 'Uno de los archivos no es una imagen válida. Usa JPG, PNG o WEBP.'}
            ).clean(archivo)
            limpias.append(_validar_foto(validada))
        return limpias


class SolicitudForm(forms.Form):
    """Lo que escribe el cliente al pedir un servicio."""

    servicio = forms.ModelChoiceField(
        queryset=Servicio.objects.all(), empty_label='Elige un servicio',
        widget=forms.Select(attrs={'class': 'inp w-full'}),
        error_messages={'required': 'Elige un servicio.', 'invalid_choice': 'Elige un servicio de la lista.'},
    )
    modelo = forms.CharField(
        max_length=100, error_messages={'required': 'Escribe el modelo del equipo.'},
    )
    problema = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={'class': 'inp w-full', 'rows': 3, 'placeholder': 'Cuéntanos qué le pasa al equipo'}),
        error_messages={'required': 'Cuéntanos qué le pasa al equipo.'},
    )
    fotos = VariasFotosField(required=False)

    def clean_modelo(self):
        modelo = ' '.join(self.cleaned_data['modelo'].split())
        if len(modelo) < 2:
            raise forms.ValidationError('Escribe el modelo del equipo.')
        return modelo

    def clean_problema(self):
        problema = ' '.join(self.cleaned_data['problema'].split())
        if len(problema) < 10:
            raise forms.ValidationError('Describe el problema con un poco más de detalle (mínimo 10 letras).')
        return problema

    @transaction.atomic
    def guardar(self, cliente):
        orden = Orden.objects.create(
            cliente=cliente, servicio=self.cleaned_data['servicio'],
            modelo=self.cleaned_data['modelo'], problema=self.cleaned_data['problema'],
        )
        for archivo in self.cleaned_data['fotos']:
            OrdenFoto.objects.create(orden=orden, imagen=archivo)
        orden.registrar('Solicitud creada por el cliente', cliente)
        avisos.nueva_solicitud(orden)
        return orden


class OrdenGestionForm(forms.Form):
    """Lo que decide el administrador sobre una orden: quién la atiende, en qué va y para cuándo."""

    servicio = forms.ModelChoiceField(
        queryset=Servicio.objects.all(), required=False, label='Servicio (corrígelo si el cliente se equivocó)', empty_label=None,
        widget=forms.Select(attrs={'class': 'inp w-full'}),
    )
    tecnico = forms.ModelChoiceField(queryset=Usuario.objects.none(), required=False, empty_label='Sin asignar', widget=forms.Select(attrs={'class': 'inp w-full'}))
    estado = forms.ChoiceField(choices=[c for c in Orden.Estado.choices if c[0] != Orden.Estado.ENTREGADO], widget=forms.Select(attrs={'class': 'inp w-full'}))
    prioridad = forms.ChoiceField(choices=Orden.Prioridad.choices, widget=forms.Select(attrs={'class': 'inp w-full'}))
    fecha_estimada = forms.DateField(
        required=False, widget=forms.DateInput(attrs={'type': 'date', 'class': 'inp w-full'}, format='%Y-%m-%d'), label='Fecha estimada de entrega',
        error_messages={'invalid': 'Escribe una fecha válida.'},
    )
    motivo_fecha = forms.CharField(
        required=False, max_length=200, label='Motivo del cambio de fecha',
        widget=forms.TextInput(attrs={'class': 'inp w-full', 'placeholder': 'Ej.: la pieza llega 3 días después'}),
    )

    def __init__(self, *args, orden, **kwargs):
        self.orden = orden
        kwargs.setdefault('initial', {
            'servicio': orden.servicio_id, 'tecnico': orden.tecnico_id, 'estado': orden.estado,
            'prioridad': orden.prioridad, 'fecha_estimada': orden.fecha_estimada,
        })
        super().__init__(*args, **kwargs)
        # Solo tecnicos activos (el que ya la tiene asignada se conserva aunque se haya desactivado),
        # del más libre al más ocupado y con lo que está haciendo cada uno
        tecnicos = carga.tecnicos_por_carga(incluir=orden.tecnico_id)
        self.fields['tecnico'].queryset = tecnicos
        primero = next(iter(tecnicos), None)
        self.recomendado = primero.pk if primero and not orden.tecnico_id else None

        def etiqueta(u):
            marca = ' ★ más libre' if u.pk == self.recomendado else ''
            return f'{u.nombre}{"" if u.is_active else " (desactivado)"} · {carga.detalle(u)}{marca}'

        self.fields['tecnico'].label_from_instance = etiqueta

    def clean_fecha_estimada(self):
        fecha = self.cleaned_data.get('fecha_estimada')
        if fecha and fecha != self.orden.fecha_estimada and fecha < timezone.localdate():
            raise forms.ValidationError('La fecha estimada no puede ser anterior a hoy.')
        return fecha

    def clean(self):
        datos = super().clean()
        if datos.get('estado') and datos['estado'] != Orden.Estado.RECIBIDO and not datos.get('tecnico'):
            self.add_error('tecnico', 'Asigna un técnico antes de avanzar el estado.')
        orden = self.orden
        if datos.get('estado') and datos['estado'] != orden.estado:
            if orden.congelada:
                self.add_error('estado', 'Esta orden está cerrada. Usa «Reabrir la orden» para continuarla.')
            elif datos['estado'] in (Orden.Estado.CANCELADA, Orden.Estado.RECHAZADA):
                self.add_error('estado', 'Para rechazar una orden usa el botón «Rechazar la orden».')
        if 'fecha_estimada' in datos and orden.fecha_estimada and datos['fecha_estimada'] != orden.fecha_estimada:
            if len((datos.get('motivo_fecha') or '').strip()) < 5:
                self.add_error('motivo_fecha', 'Cuenta por qué cambia la fecha (mínimo 5 letras): el cliente la verá.')
        return datos

    @transaction.atomic
    def guardar(self, usuario):
        """Aplica los cambios y deja cada uno anotado en el historial."""
        o, d = self.orden, self.cleaned_data
        tecnico_anterior, estado_cambio, fecha_cambio = o.tecnico, d['estado'] != o.estado, d['fecha_estimada'] != o.fecha_estimada
        servicio_anterior = o.servicio
        d['servicio'] = d.get('servicio') or o.servicio
        if d['servicio'] != o.servicio:
            o.registrar(f'Servicio corregido: {o.servicio.nombre} → {d["servicio"].nombre}', usuario)
            o.servicio = d['servicio']
        if d['tecnico'] != o.tecnico:
            o.registrar(f'Asignada a {d["tecnico"].nombre}' if d['tecnico'] else 'Se quitó el técnico asignado', usuario)
            o.tecnico = d['tecnico']
            o.asignada_en = timezone.now() if d['tecnico'] else None
            # Al asignar técnico, el equipo deja de estar «Recibido»: empieza el diagnóstico
            if d['tecnico'] and d['estado'] == Orden.Estado.RECIBIDO and o.estado == Orden.Estado.RECIBIDO:
                d['estado'] = Orden.Estado.DIAGNOSTICO
                estado_cambio = True
        if d['estado'] != o.estado:
            o.registrar(f'Estado: {o.get_estado_display()} → {dict(Orden.Estado.choices)[d["estado"]]}', usuario)
            o.estado = d['estado']
        if d['prioridad'] != o.prioridad:
            o.registrar(f'Prioridad: {dict(Orden.Prioridad.choices)[d["prioridad"]]}', usuario)
            o.prioridad = d['prioridad']
        if d['fecha_estimada'] != o.fecha_estimada:
            motivo = ' '.join((d.get('motivo_fecha') or '').split())
            o.registrar(
                (f'Fecha estimada de entrega: {d["fecha_estimada"]:%d/%m/%Y}' if d['fecha_estimada'] else 'Se quitó la fecha estimada')
                + (f' ({motivo})' if o.fecha_estimada and motivo else ''),
                usuario,
            )
            fechas.cambiar(o, d['fecha_estimada'], motivo, usuario)
        o.save()
        avisos.orden_gestionada(o, tecnico_anterior, estado_cambio, fecha_cambio)
        if o.servicio != servicio_anterior:
            avisos.servicio_corregido(o, servicio_anterior)
        return o


class EdicionOrdenForm(SolicitudForm):
    """El cliente corrige su solicitud (mientras no tenga técnico): datos, fotos nuevas y fotos a quitar."""

    def __init__(self, *args, orden, **kwargs):
        super().__init__(*args, **kwargs)
        self.orden = orden
        self.fotos_a_quitar = []

    def clean(self):
        datos = super().clean()
        ids = [i for i in self.data.getlist('quitar') if i.isdigit()] if hasattr(self.data, 'getlist') else []
        self.fotos_a_quitar = list(self.orden.fotos.filter(pk__in=ids))
        nuevas = datos.get('fotos') or []
        total = self.orden.fotos.count() - len(self.fotos_a_quitar) + len(nuevas)
        if total > MAX_FOTOS_ORDEN and not self.has_error('fotos'):
            self.add_error('fotos', f'Cada solicitud puede tener máximo {MAX_FOTOS_ORDEN} fotos. Quita alguna para añadir más.')
        return datos

    @transaction.atomic
    def guardar_cambios(self, cliente):
        orden = self.orden
        for foto in self.fotos_a_quitar:
            foto.imagen.delete(save=False)
            foto.delete()
        orden.servicio = self.cleaned_data['servicio']
        orden.modelo = self.cleaned_data['modelo']
        orden.problema = self.cleaned_data['problema']
        orden.save(update_fields=['servicio', 'modelo', 'problema'])
        for archivo in self.cleaned_data['fotos']:
            OrdenFoto.objects.create(orden=orden, imagen=archivo)
        orden.registrar('Solicitud corregida por el cliente', cliente)
        avisos.solicitud_corregida(orden)
        return orden


ESTADOS_TECNICO = [c for c in Orden.Estado.choices if c[0] != Orden.Estado.ENTREGADO]


class EstadoTecnicoForm(forms.Form):
    """El técnico solo avanza el estado de su orden (Diagnóstico → Reparación → Listo). Para volver atrás pide permiso.

    La entrega la autoriza el administrador con el pago."""

    estado = forms.ChoiceField(choices=ESTADOS_TECNICO, widget=forms.Select(attrs={'class': 'inp'}),
                               error_messages={'required': 'Elige un estado.', 'invalid_choice': 'Ese estado no está disponible.'})

    def __init__(self, *args, orden, **kwargs):
        super().__init__(*args, **kwargs)
        self.orden = orden
        self.fields['estado'].choices = orden.estados_siguientes

    def clean_estado(self):
        estado = self.cleaned_data['estado']
        if estado == self.orden.estado:
            raise forms.ValidationError('La orden ya está en ese estado.')
        return estado

    def clean(self):
        datos = super().clean()
        enviado = self.data.get('estado') if hasattr(self.data, 'get') else None
        if 'estado' in self.errors and enviado in dict(ESTADOS_TECNICO):
            if self.orden.motivo_sin_avance and enviado != self.orden.estado:
                mensaje = self.orden.motivo_sin_avance
            elif enviado == 'LISTO' and self.orden.piezas_sin_usar:
                mensaje = 'Antes de marcar «Listo», registra la pieza que usaste en la reparación (más abajo, en «Repuestos usados»).'
            elif enviado == self.orden.estado:
                mensaje = 'La orden ya está en ese estado.'
            else:
                mensaje = 'No puedes volver a un estado anterior. Pídele permiso al administrador.'
            self._errors['estado'] = self.error_class([mensaje])
        return datos

    @transaction.atomic
    def guardar(self, tecnico):
        o = self.orden
        nuevo = self.cleaned_data['estado']
        o.registrar(f'Estado: {o.get_estado_display()} → {dict(Orden.Estado.choices)[nuevo]}', tecnico)
        o.estado = nuevo
        o.save(update_fields=['estado'])
        avisos.estado_por_tecnico(o)
        return o


class SolicitudEstadoForm(forms.Form):
    """El técnico pide volver a un estado anterior; el administrador decide."""

    estado = forms.ChoiceField(choices=ESTADOS_TECNICO, error_messages={'required': 'Elige a qué estado quieres volver.', 'invalid_choice': 'Ese estado no está disponible.'})
    motivo = forms.CharField(max_length=300, error_messages={'required': 'Explica por qué necesitas volver atrás.', 'max_length': 'El motivo es muy largo (máximo 300 letras).'})

    def __init__(self, *args, orden, **kwargs):
        super().__init__(*args, **kwargs)
        self.orden = orden
        self.fields['estado'].choices = orden.estados_anteriores

    def clean_motivo(self):
        motivo = ' '.join(self.cleaned_data['motivo'].split())
        if len(motivo) < 5:
            raise forms.ValidationError('Explica por qué necesitas volver atrás.')
        return motivo

    def clean(self):
        datos = super().clean()
        if not self.errors and self.orden.solicitudes_estado.filter(estado=SolicitudEstado.Estado.PENDIENTE).exists():
            raise forms.ValidationError('Ya hay una solicitud pendiente para esta orden. Espera la respuesta del administrador.')
        return datos

    @transaction.atomic
    def guardar(self, tecnico):
        o, d = self.orden, self.cleaned_data
        solicitud = SolicitudEstado.objects.create(
            orden=o, tecnico=tecnico, estado_actual=o.estado, estado_pedido=d['estado'], motivo=d['motivo'],
        )
        o.registrar(f'El técnico pidió volver a {dict(Orden.Estado.choices)[d["estado"]]}: {d["motivo"]}', tecnico)
        avisos.retroceso_solicitado(solicitud)
        return solicitud


class NotaForm(forms.Form):
    texto = forms.CharField(
        max_length=500, strip=True,
        widget=forms.Textarea(attrs={'class': 'inp w-full', 'rows': 3, 'placeholder': 'Diagnóstico, avance o lo que el cliente debe saber', 'maxlength': 500}),
        error_messages={'required': 'Escribe la nota.', 'max_length': 'La nota es muy larga (máximo 500 letras).'},
    )
    visible_cliente = forms.BooleanField(required=False, initial=True)

    def clean_texto(self):
        texto = ' '.join(self.cleaned_data['texto'].split())
        if len(texto) < 3:
            raise forms.ValidationError('Escribe la nota.')
        return texto

    @transaction.atomic
    def guardar(self, orden, tecnico):
        nota = NotaOrden.objects.create(
            orden=orden, autor=tecnico, texto=self.cleaned_data['texto'],
            visible_cliente=self.cleaned_data['visible_cliente'],
        )
        orden.registrar('Nota agregada por el técnico' + (' (visible para el cliente)' if nota.visible_cliente else ' (interna)'), tecnico)
        if nota.visible_cliente:
            avisos.nota_para_cliente(orden)
        return nota


MAX_FOTOS_AVANCE = 8


class FotosAvanceForm(forms.Form):
    """El técnico sube fotos del avance o de cómo quedó el equipo."""

    fotos = VariasFotosField(maximo=MAX_FOTOS_AVANCE, error_messages={'required': 'Elige al menos una foto.'})

    def __init__(self, *args, orden, **kwargs):
        super().__init__(*args, **kwargs)
        self.orden = orden

    def clean(self):
        datos = super().clean()
        nuevas = datos.get('fotos') or []
        if nuevas and self.orden.fotos_avance.count() + len(nuevas) > MAX_FOTOS_AVANCE:
            self.add_error('fotos', f'Cada orden admite máximo {MAX_FOTOS_AVANCE} fotos de avance. Quita alguna para añadir más.')
        return datos

    @transaction.atomic
    def guardar(self, tecnico):
        fotos = [FotoAvance.objects.create(orden=self.orden, autor=tecnico, imagen=f) for f in self.cleaned_data['fotos']]
        self.orden.registrar(f'El técnico subió {len(fotos)} foto{"s" if len(fotos) != 1 else ""} del avance', tecnico)
        avisos.fotos_de_avance(self.orden)
        return fotos


COMPROBANTE_MAX_MB = 3


class ComprobanteForm(forms.Form):
    """Comprobante de la transferencia: foto o PDF de hasta 3 MB, y el número de comprobante."""

    comprobante = forms.FileField(
        widget=forms.ClearableFileInput(attrs={'class': 'inp w-full', 'accept': 'image/jpeg,image/png,image/webp,application/pdf'}),
        error_messages={'required': 'Adjunta el comprobante de la transferencia.'})
    referencia = forms.CharField(max_length=60, widget=forms.TextInput(attrs={'class': 'inp w-full'}), error_messages={'required': 'Escribe el número del comprobante.'})

    def clean_referencia(self):
        referencia = ' '.join(self.cleaned_data['referencia'].split())
        if len(referencia) < 3:
            raise forms.ValidationError('Escribe el número del comprobante.')
        return referencia

    def clean_comprobante(self):
        archivo = self.cleaned_data['comprobante']
        if archivo.size > COMPROBANTE_MAX_MB * 1024 * 1024:
            raise forms.ValidationError(f'El archivo pesa demasiado. El máximo es {COMPROBANTE_MAX_MB} MB.')
        inicio = archivo.read(8)
        archivo.seek(0)
        if inicio.startswith(b'%PDF'):
            return archivo
        try:
            from PIL import Image
            imagen = Image.open(archivo)
            formato = imagen.format
            imagen.verify()
        except Exception:
            raise forms.ValidationError('Adjunta una foto (JPG, PNG o WEBP) o un PDF.')
        finally:
            archivo.seek(0)
        if formato not in FORMATOS_FOTO:
            raise forms.ValidationError('Adjunta una foto (JPG, PNG o WEBP) o un PDF.')
        return archivo
