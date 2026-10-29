from django import forms


class MensajeForm(forms.Form):
    texto = forms.CharField(
        max_length=1000, strip=True,
        widget=forms.Textarea(attrs={'class': 'inp w-full', 'rows': 2, 'placeholder': 'Escribe tu mensaje…', 'maxlength': 1000}),
        error_messages={'required': 'Escribe un mensaje.', 'max_length': 'El mensaje es muy largo (máximo 1000 letras).'},
    )

    def clean_texto(self):
        texto = self.cleaned_data['texto'].strip()
        if not texto:
            raise forms.ValidationError('Escribe un mensaje.')
        return texto
