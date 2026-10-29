"""Cálculo del precio de venta sugerido y del margen. Funciones puras: no tocan la base de datos."""

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

CENTAVO = Decimal('0.01')
PASO = Decimal('0.05')  # el precio sugerido se redondea hacia arriba de 5 en 5 centavos


def sugerido(costo, margen):
    """Costo + margen %, redondeado hacia arriba a 0.05. Sin costo no hay sugerencia."""
    if costo is None or costo <= 0:
        return None
    bruto = Decimal(costo) * (1 + Decimal(margen) / 100)
    return ((bruto / PASO).to_integral_value(ROUND_CEILING) * PASO).quantize(CENTAVO)


def margen_real(venta, costo):
    """Ganancia sobre el costo, en %. None si no hay costo."""
    if costo is None or costo <= 0 or venta is None:
        return None
    return ((Decimal(venta) / Decimal(costo) - 1) * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
