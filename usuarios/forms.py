import re

from django import forms
from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from . import claves, seguridad
from .models import Usuario


# ---------- Formularios del panel de administración de Django ----------

class UsuarioCreationForm(BaseUserCreationForm):
    class Meta:
        model = Usuario
        fields = ('email', 'nombre', 'telefono', 'rol')


class UsuarioChangeForm(UserChangeForm):
    class Meta:
        model = Usuario
        fields = '__all__'


# ---------- Formularios públicos: registro e ingreso ----------

NOMBRE_VALIDO = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ '.-]*$")
CELULAR_EC = re.compile(r'^09\d{8}$')       # 0991234567
FIJO_EC = re.compile(r'^0[2-7]\d{7}$')      # 042123456


class ValidaUsuario:
    """Reglas compartidas por los formularios que crean usuarios (registro de clientes y alta de técnicos)."""

    def clean_nombre(self):
        nombre = ' '.join(self.cleaned_data['nombre'].split())
        if len(nombre) < 3:
            raise ValidationError('Escribe el nombre completo.')
        if not NOMBRE_VALIDO.match(nombre):
            raise ValidationError('El nombre solo puede tener letras y espacios.')
        return nombre

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        try:
            forms.EmailField().clean(email)
        except ValidationError:
            raise ValidationError('Escribe un correo válido, por ejemplo nombre@correo.com.')
        if Usuario.objects.filter(email__iexact=email).exists():
            raise ValidationError('Ese correo ya está registrado.')
        return email

    def clean_telefono(self):
        telefono = re.sub(r'[\s\-().]', '', self.cleaned_data['telefono'])
        if telefono.startswith('+593'):
            telefono = '0' + telefono[4:]
        if not (CELULAR_EC.match(telefono) or FIJO_EC.match(telefono)):
            raise ValidationError('Escribe un teléfono válido de Ecuador, por ejemplo 0991234567.')
        return telefono

    def clean(self):
        datos = super().clean()
        password = datos.get('password')
        # La contraseña se revisa al final porque no puede parecerse al nombre ni al correo
        if password and not self.has_error('password'):
            candidato = Usuario(email=datos.get('email', ''), nombre=datos.get('nombre', ''))
            try:
                password_validation.validate_password(password, candidato)
            except ValidationError as error:
                self.add_error('password', error)
        return datos


class RegistroForm(ValidaUsuario, forms.Form):
    """Registro de un cliente. Quien se registra aquí siempre queda con rol CLIENTE."""

    nombre = forms.CharField(max_length=100, error_messages={'required': 'Escribe tu nombre completo.'})
    email = forms.CharField(max_length=120, error_messages={'required': 'Escribe tu correo.'})
    telefono = forms.CharField(max_length=20, error_messages={'required': 'Escribe tu teléfono.'})
    password = forms.CharField(strip=False, max_length=128, error_messages={'required': 'Escribe una contraseña.'})
    acepto = forms.BooleanField(
        error_messages={'required': 'Debes aceptar el tratamiento de tus datos para registrarte.'}
    )

    def guardar(self):
        """Crea el cliente. Devuelve None si el correo se registró justo en este instante."""
        datos = self.cleaned_data
        try:
            return Usuario.objects.create_user(
                datos['email'],
                datos['nombre'],
                datos['password'],
                telefono=datos['telefono'],
                rol=Usuario.Rol.CLIENTE,
                acepto_datos_en=timezone.now(),
            )
        except IntegrityError:
            self.add_error('email', 'Ese correo ya está registrado. Prueba ingresando.')
            return None


class IngresoForm(forms.Form):
    email = forms.CharField(max_length=120, error_messages={'required': 'Escribe tu correo.'})
    password = forms.CharField(strip=False, max_length=128, error_messages={'required': 'Escribe tu contraseña.'})

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.usuario = None

    def clean(self):
        datos = super().clean()
        email = (datos.get('email') or '').strip().lower()
        password = datos.get('password')
        if not email or not password:
            return datos
        if seguridad.bloqueado(self.request, email):
            raise ValidationError(seguridad.MENSAJE_BLOQUEO, code='bloqueado')
        usuario = authenticate(self.request, username=email, password=password)
        if usuario is None:
            seguridad.registrar_fallo(self.request, email)
            # El mismo mensaje para correo inexistente y clave incorrecta: no revela cuál falló
            raise ValidationError('Correo o contraseña incorrectos.', code='invalido')
        seguridad.limpiar(self.request, email)
        self.usuario = usuario
        return datos


class OlvideContrasenaForm(forms.Form):
    """Pide al administrador una contraseña nueva. Solo pide el correo."""

    email = forms.CharField(max_length=120, error_messages={'required': 'Escribe tu correo.'})

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()


class TecnicoForm(ValidaUsuario, forms.Form):
    """Alta de un técnico por el administrador. La contraseña temporal la genera el sistema."""

    nombre = forms.CharField(max_length=100, error_messages={'required': 'Escribe el nombre.'})
    email = forms.CharField(max_length=120, error_messages={'required': 'Escribe el correo.'})
    telefono = forms.CharField(max_length=20, error_messages={'required': 'Escribe el WhatsApp del técnico.'})

    def clean_telefono(self):
        telefono = super().clean_telefono()
        if not CELULAR_EC.match(telefono):
            raise ValidationError('Escribe un celular válido de Ecuador (empieza con 09), para enviarle el acceso por WhatsApp.')
        return telefono

    def guardar(self):
        """Devuelve (técnico, clave_temporal) o None si el correo se registró justo en este instante."""
        datos = self.cleaned_data
        clave = claves.generar_clave()
        try:
            tecnico = Usuario.objects.create_user(
                datos['email'], datos['nombre'], clave,
                telefono=datos['telefono'], rol=Usuario.Rol.TECNICO, debe_cambiar_contrasena=True,
            )
        except IntegrityError:
            self.add_error('email', 'Ese correo ya está registrado.')
            return None
        return tecnico, clave


class CambioContrasenaForm(forms.Form):
    """Cambio de contraseña de quien tiene la sesión iniciada."""

    actual = forms.CharField(strip=False, error_messages={'required': 'Escribe tu contraseña actual.'})
    nueva = forms.CharField(strip=False, max_length=128, error_messages={'required': 'Escribe la nueva contraseña.'})
    repetir = forms.CharField(strip=False, max_length=128, error_messages={'required': 'Repite la nueva contraseña.'})

    def __init__(self, usuario, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        self.request = request

    def clean_actual(self):
        actual = self.cleaned_data['actual']
        # Adivinar la contraseña actual desde una sesión robada se frena igual que en el ingreso
        if seguridad.bloqueado(self.request, self.usuario.email):
            raise ValidationError(seguridad.MENSAJE_BLOQUEO)
        if not self.usuario.check_password(actual):
            seguridad.registrar_fallo(self.request, self.usuario.email)
            raise ValidationError('La contraseña actual no es correcta.')
        seguridad.limpiar(self.request, self.usuario.email)
        return actual

    def clean(self):
        datos = super().clean()
        nueva, repetir, actual = datos.get('nueva'), datos.get('repetir'), datos.get('actual')
        if not nueva or self.has_error('nueva'):
            return datos
        if actual and nueva == actual:
            self.add_error('nueva', 'La nueva contraseña debe ser distinta de la actual.')
        else:
            try:
                password_validation.validate_password(nueva, self.usuario)
            except ValidationError as error:
                self.add_error('nueva', error)
        if repetir and nueva != repetir and not self.has_error('repetir'):
            self.add_error('repetir', 'Las contraseñas no coinciden.')
        return datos

    def guardar(self):
        self.usuario.set_password(self.cleaned_data['nueva'])
        self.usuario.debe_cambiar_contrasena = False
        self.usuario.save(update_fields=['password', 'debe_cambiar_contrasena'])
        return self.usuario


class RestablecerContrasenaForm(forms.Form):
    """El administrador asigna una contraseña temporal corta (la persona la cambia al ingresar)."""

    password = forms.CharField(strip=False, max_length=128, error_messages={'required': 'Genera o escribe la contraseña temporal.'})
    telefono = forms.CharField(max_length=20, required=False)

    def __init__(self, usuario, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario

    def clean_password(self):
        password = self.cleaned_data['password'].strip()
        # Es temporal: basta con que no sea demasiado corta. No se aplican las reglas de una contraseña propia.
        if len(password) < claves.MINIMO_TEMPORAL:
            raise ValidationError(f'Escribe al menos {claves.MINIMO_TEMPORAL} caracteres o usa "Generar".')
        return password

    def clean_telefono(self):
        telefono = self.cleaned_data.get('telefono', '').strip()
        if not telefono:
            return ''
        return ValidaUsuario.clean_telefono(self)

    def guardar(self):
        self.usuario.set_password(self.cleaned_data['password'])
        self.usuario.debe_cambiar_contrasena = True
        campos = ['password', 'debe_cambiar_contrasena']
        # Si se deja vacío se conserva el teléfono que ya tenía
        if self.cleaned_data['telefono'] and self.cleaned_data['telefono'] != self.usuario.telefono:
            self.usuario.telefono = self.cleaned_data['telefono']
            campos.append('telefono')
        self.usuario.save(update_fields=campos)
        return self.cleaned_data['password']
