from datetime import timedelta
from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Gasto

MONTO_MAXIMO = Decimal('1000000')


class GastoForm(forms.ModelForm):
    class Meta:
        model = Gasto
        fields = ['tipo', 'concepto', 'monto', 'fecha']
        error_messages = {
            'concepto': {'required': 'Escribe en qué se gastó.'},
            'monto': {'required': 'Escribe el monto.', 'invalid': 'Escribe un monto, por ejemplo 12.50.'},
            'fecha': {'required': 'Elige la fecha.', 'invalid': 'Fecha no válida.'},
        }
        widgets = {
            'tipo': forms.Select(attrs={'class': 'inp w-full'}),
            'concepto': forms.TextInput(attrs={'class': 'inp w-full', 'placeholder': 'Concepto (ej. pago de luz, herramientas)', 'maxlength': 150}),
            'monto': forms.NumberInput(attrs={'class': 'inp w-full', 'step': '0.01', 'min': '0.01', 'placeholder': 'Monto $'}),
            'fecha': forms.DateInput(attrs={'class': 'inp w-full', 'type': 'date'}, format='%Y-%m-%d'),
        }

    def clean_concepto(self):
        concepto = ' '.join(self.cleaned_data['concepto'].split())
        if len(concepto) < 3:
            raise forms.ValidationError('Escribe el concepto (mínimo 3 letras).')
        return concepto

    def clean_monto(self):
        monto = self.cleaned_data['monto']
        if monto <= 0:
            raise forms.ValidationError('El monto debe ser mayor a cero.')
        if monto > MONTO_MAXIMO:
            raise forms.ValidationError('El monto es demasiado alto.')
        return monto

    def clean_fecha(self):
        fecha = self.cleaned_data['fecha']
        hoy = timezone.localdate()
        if fecha > hoy:
            raise forms.ValidationError('La fecha no puede ser futura.')
        if fecha < hoy - timedelta(days=366 * 5):
            raise forms.ValidationError('La fecha es demasiado antigua.')
        return fecha
