"""Paso 25: el técnico busca el repuesto escribiendo (proforma y registro de uso)."""
from django.urls import reverse

from ordenes.test_paso20 import Paso20Base


class BuscadorDeRepuestosTests(Paso20Base):
    def test_cada_fila_de_la_proforma_tiene_buscador_y_texto_de_busqueda(self):
        o = self.orden(self.tec, estado='DIAGNOSTICO')
        self.client.force_login(self.tec)
        html = self.client.get(reverse('equipo', args=[o.pk])).content.decode()
        self.assertIn('data-buscar-fila', html)
        self.assertIn('data-aviso-fila', html)
        self.assertIn('Buscar repuesto: nombre, marca o código', html)
        self.assertIn('data-texto="Pantalla 15.6', html)  # nombre, marca y código para encontrarla

    def test_el_buscador_de_uso_incluye_marca_y_codigo(self):
        o = self.orden(self.tec, estado='REPARACION')
        self.client.force_login(self.tec)
        html = self.client.get(reverse('equipo', args=[o.pk])).content.decode()
        self.assertIn('data-filtro', html)
        self.assertIn('data-aviso', html)
        self.assertIn('PAN1', html)

    def test_el_script_filtra_por_categoria_y_texto_juntos(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / 'static' / 'js' / 'presupuesto.js').read_text(encoding='utf-8')
        self.assertIn('data-buscar-fila', js)
        self.assertIn('function filtrar(sel, categoria, texto, aviso)', js)
