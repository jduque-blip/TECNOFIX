"""Convierte un Reporte en CSV (para Excel) o PDF."""

import csv
import io
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

NEGOCIO = 'TecnoFix · Servicio técnico'


def _es_numero(v):
    try:
        Decimal(v)
        return True
    except Exception:
        return False


def _seguro(v):
    """Evita que Excel ejecute como fórmula un texto que empieza con = + - @ (un número negativo se deja tal cual)."""
    if not isinstance(v, str) or not v or v[0] not in '=+-@' or _es_numero(v):
        return v
    return "'" + v


def csv_respuesta(rep, nombre):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    w.writerow([rep.titulo])
    w.writerow([rep.periodo])
    w.writerow([])
    w.writerow(rep.columnas)
    for fila in rep.filas:
        w.writerow([_seguro(c) for c in fila])
    if rep.resumen:
        w.writerow([])
        for etiqueta, valor in rep.resumen:
            w.writerow([_seguro(etiqueta), valor])
    if rep.truncado:
        w.writerow([])
        w.writerow([f'Se muestran las primeras filas. Acorta el período para ver el resto.'])
    r = HttpResponse(('﻿' + buf.getvalue()).encode('utf-8'), content_type='text/csv; charset=utf-8')
    r['Content-Disposition'] = f'attachment; filename="{nombre}.csv"'
    return r


def _pie(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(1.5 * cm, 1 * cm, f'{NEGOCIO} · Generado el {timezone.localtime():%d/%m/%Y %H:%M}')
    canvas.drawRightString(doc.pagesize[0] - 1.5 * cm, 1 * cm, f'Página {doc.page}')
    canvas.restoreState()


def pdf_respuesta(rep, nombre):
    ancha = len(rep.columnas) > 5
    tam = landscape(A4) if ancha else A4
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=tam, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.5 * cm, bottomMargin=1.8 * cm, title=rep.titulo, author=NEGOCIO)
    est = getSampleStyleSheet()
    celda = ParagraphStyle('celda', parent=est['BodyText'], fontSize=8, leading=10)
    celda_der = ParagraphStyle('celda_der', parent=celda, alignment=2)
    cab = ParagraphStyle('cab', parent=celda, textColor=colors.white, fontName='Helvetica-Bold')
    cab_der = ParagraphStyle('cab_der', parent=cab, alignment=2)
    esc = lambda t: str(t).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    historia = [Paragraph(esc(NEGOCIO), est['Normal']), Paragraph(esc(rep.titulo), est['Title']), Paragraph(esc(rep.periodo), est['Normal']), Spacer(1, 0.4 * cm)]
    datos = [[Paragraph(esc(c), cab_der if i in rep.derecha else cab) for i, c in enumerate(rep.columnas)]]
    for fila in rep.filas:
        datos.append([Paragraph(esc(c), celda_der if i in rep.derecha else celda) for i, c in enumerate(fila)])
    if not rep.filas:
        datos.append([Paragraph('Sin registros en este período.', celda)] + [''] * (len(rep.columnas) - 1))
    ancho = tam[0] - 3 * cm
    tabla = LongTable(datos, colWidths=[ancho / len(rep.columnas)] * len(rep.columnas), repeatRows=1)
    tabla.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6d28d9')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f3ff')]),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#d4d4d8')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    historia.append(tabla)
    if rep.resumen:
        historia += [Spacer(1, 0.5 * cm), Paragraph('<b>Resumen</b>', est['Heading3'])]
        resumen = LongTable([[Paragraph(esc(e), celda), Paragraph(f'<b>{esc(v)}</b>', celda_der)] for e, v in rep.resumen], colWidths=[ancho * 0.7, ancho * 0.3])
        resumen.setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, -1), 0.25, colors.HexColor('#d4d4d8'))]))
        historia.append(resumen)
    if rep.truncado:
        historia.append(Paragraph('Se muestran solo las primeras filas. Acorta el período para ver el resto.', celda))
    doc.build(historia, onFirstPage=_pie, onLaterPages=_pie)
    r = HttpResponse(buf.getvalue(), content_type='application/pdf')
    r['Content-Disposition'] = f'inline; filename="{nombre}.pdf"'
    return r


def factura_pdf(orden, factura, pagado):
    """Comprobante de una orden: lo que se facturó y si ya está pagado."""
    from reportes.datos import Reporte, dinero
    filas = [[l['detalle'], l['tipo'], l['cantidad'], dinero(l['precio_unit']), dinero(l['subtotal'])] for l in factura['lineas']]
    resumen = [('Subtotal', f'${dinero(factura["subtotal"])}'), ('IVA', f'${dinero(factura["iva"])}'), ('Total', f'${dinero(factura["total"])}'),
               ('Estado del pago', 'Pagado' if pagado else 'Pendiente')]
    rep = Reporte('factura', f'Factura general · Orden #{orden.pk}', ['Detalle', 'Tipo', 'Cant.', 'Precio $', 'Subtotal $'], filas, resumen, derecha=(2, 3, 4),
                  periodo=f'Cliente: {orden.cliente.nombre} · Equipo: {orden.modelo} · Recibido el {timezone.localtime(orden.creada_en):%d/%m/%Y}')
    return pdf_respuesta(rep, f'factura-orden-{orden.pk}')
