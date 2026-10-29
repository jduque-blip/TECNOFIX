"""Contraseñas temporales cortas y enlaces de WhatsApp para entregarlas."""

import secrets
from urllib.parse import quote

# Sin letras que se confunden al leerlas (0/o, 1/l/i): fáciles de copiar y de dictar
ALFABETO = 'abcdefghjkmnpqrstuvwxyz23456789'
LARGO = 6
MINIMO_TEMPORAL = 4


def generar_clave():
    return ''.join(secrets.choice(ALFABETO) for _ in range(LARGO))


def numero_whatsapp(telefono):
    """Convierte 0991234567 en 593991234567 (formato internacional que pide wa.me). Vacío si no hay celular válido."""
    digitos = ''.join(c for c in (telefono or '') if c.isdigit())
    if len(digitos) == 10 and digitos.startswith('09'):
        return '593' + digitos[1:]
    return ''


def enlace_whatsapp(usuario, clave=None):
    """Enlace wa.me con el mensaje listo. Con clave: entrega de acceso; sin clave: solo abre el chat."""
    numero = numero_whatsapp(usuario.telefono)
    if not numero:
        return ''
    if clave is None:
        return f'https://wa.me/{numero}'
    texto = (
        f'Hola {usuario.nombre.split()[0]}, este es tu acceso a TecnoFix:\n'
        f'Correo: {usuario.email}\n'
        f'Contraseña temporal: {clave}\n'
        'Al ingresar te pedirá crear tu contraseña nueva.'
    )
    return f'https://wa.me/{numero}?text={quote(texto)}'
