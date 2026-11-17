// Presupuesto del técnico: filas de repuestos y total en vivo (el servidor recalcula todo al guardar).
(function () {
  var f = document.querySelector('[data-presupuesto]');
  if (!f) return;
  var cont = f.querySelector('[data-repuestos]');
  var plantilla = f.querySelector('template[data-plantilla]');
  var iva = parseFloat(f.dataset.iva) || 0;
  var dinero = function (n) { return '$' + n.toFixed(2); };

  function total() {
    var sub = 0;
    f.querySelectorAll('input[name^=tarifa_]').forEach(function (i) {
      var n = parseInt(i.value, 10);
      if (n > 0 && i.dataset.precio) sub += n * parseFloat(i.dataset.precio);
    });
    f.querySelectorAll('[data-fila-servicio]').forEach(function (fila) {
      var o = fila.querySelector('select').selectedOptions[0];
      var n = parseInt(fila.querySelector('input').value, 10);
      if (o && o.dataset.precio && n > 0) sub += n * parseFloat(o.dataset.precio);
    });
    cont.querySelectorAll('[data-fila]').forEach(function (fila) {
      var o = fila.querySelector('select[name=producto]').selectedOptions[0];
      var n = parseInt(fila.querySelector('input').value, 10);
      if (o && o.dataset.precio && n > 0) sub += n * parseFloat(o.dataset.precio);
    });
    var im = Math.round(sub * iva) / 100;
    f.querySelector('[data-subtotal]').textContent = dinero(sub);
    f.querySelector('[data-iva-monto]').textContent = dinero(im);
    f.querySelector('[data-total]').textContent = dinero(sub + im);
  }

  function agregar() {
    cont.appendChild(plantilla.content.cloneNode(true));
    total();
  }
  f.querySelector('[data-agregar-repuesto]').addEventListener('click', agregar);
  f.addEventListener('input', total);
  f.addEventListener('change', total);
  f.addEventListener('click', function (e) {
    if (e.target.matches('[data-quitar]')) { e.target.closest('[data-fila]').remove(); total(); }
  });
  var servicios = f.querySelector('[data-servicios]'), plantillaServ = f.querySelector('template[data-plantilla-servicio]');
  var btnServ = f.querySelector('[data-agregar-servicio]');
  if (btnServ && servicios && plantillaServ) {
    btnServ.addEventListener('click', function () { servicios.appendChild(plantillaServ.content.cloneNode(true)); total(); });
    servicios.addEventListener('click', function (e) {
      if (e.target.matches('[data-quitar-servicio]')) { e.target.closest('[data-fila-servicio]').remove(); total(); }
    });
    servicios.appendChild(plantillaServ.content.cloneNode(true));  // una fila lista para elegir
  }
  var falta = f.querySelector('[data-faltan]'), plantillaFalta = f.querySelector('[data-plantilla-falta]');
  var btnFalta = f.querySelector('[data-agregar-falta]');
  if (btnFalta && falta && plantillaFalta) {
    btnFalta.addEventListener('click', function () { falta.appendChild(plantillaFalta.content.cloneNode(true)); });
    falta.addEventListener('click', function (e) {
      if (e.target.matches('[data-quitar-falta]')) e.target.closest('[data-fila-falta]').remove();
    });
  }
  total();
})();

// Cotización de la pieza (administrador): los días salen del proveedor y el precio se sugiere con el margen.
document.querySelectorAll('[data-cotizar]').forEach(function (c) {
  var prov = c.querySelector('[data-proveedor]'), dias = c.querySelector('[data-dias]');
  var costo = c.querySelector('[data-costo]'), precio = c.querySelector('[data-precio]');
  var margen = parseFloat(c.dataset.margen) || 0, tocado = false;
  prov.addEventListener('change', function () {
    var o = prov.selectedOptions[0];
    if (o && o.dataset.dias !== undefined) dias.value = o.dataset.dias;
  });
  precio.addEventListener('input', function () { tocado = true; });
  costo.addEventListener('input', function () {
    var v = parseFloat(costo.value.replace(',', '.'));
    if (!tocado && v > 0) precio.value = (Math.ceil(v * (1 + margen / 100) * 20) / 20).toFixed(2);
  });
});

// Búsqueda de repuestos (técnico): categoría + texto escrito, sobre la misma lista. Sirve con miles de productos:
// se escribe parte del nombre, la marca o el código y la lista queda solo con lo que coincide.
(function () {
  var quitar = function (t) { return (t || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, ''); };
  function filtrar(sel, categoria, texto, aviso) {
    var palabras = quitar(texto).split(/\s+/).filter(Boolean), visibles = 0, unico = null;
    Array.prototype.forEach.call(sel.options, function (o) {
      if (!o.value) return;
      if (o._t === undefined) o._t = quitar(o.dataset.texto || o.text);
      var ok = (!categoria || o.dataset.categoria === categoria) &&
        palabras.every(function (w) { return o._t.indexOf(w) !== -1; });
      o.hidden = !ok; o.disabled = !ok;
      if (ok) { visibles++; unico = o; }
    });
    if (sel.selectedOptions[0] && sel.selectedOptions[0].disabled) sel.value = '';
    if (palabras.length && visibles === 1) sel.value = unico.value;   // una sola coincidencia: ya queda elegida
    sel.size = (palabras.length && visibles > 1) ? Math.min(visibles + 1, 7) : 1;   // varias: se muestran como lista
    if (aviso) {
      aviso.textContent = palabras.length || categoria
        ? (visibles ? visibles + ' resultado' + (visibles === 1 ? '' : 's') : 'No hay repuestos con eso. Si no está en el inventario, pídelo abajo como pieza que falta.')
        : '';
    }
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  }
  // al elegir de la lista desplegada vuelve a su tamaño normal
  document.addEventListener('change', function (e) {
    if (e.target.matches('select[name=producto]') && e.target.size > 1 && e.target.value) e.target.size = 1;
  });
  // Fila de la proforma: su propia categoría y su propio buscador
  function fila(e) {
    var f = e.target.closest('[data-fila]');
    if (!f) return;
    filtrar(f.querySelector('select[name=producto]'), f.querySelector('[data-categoria-fila]').value,
      f.querySelector('[data-buscar-fila]').value, f.querySelector('[data-aviso-fila]'));
  }
  document.addEventListener('input', function (e) { if (e.target.matches('[data-buscar-fila]')) fila(e); });
  document.addEventListener('change', function (e) { if (e.target.matches('[data-categoria-fila]')) fila(e); });
  // Formulario «Buscar otro repuesto en el inventario»
  document.querySelectorAll('[data-uso]').forEach(function (f) {
    var q = f.querySelector('[data-filtro]'), cat = f.querySelector('[data-categoria-filtro]'), lista = f.querySelector('[data-lista]'), aviso = f.querySelector('[data-aviso]');
    var ir = function () { filtrar(lista, cat ? cat.value : '', q.value, aviso); };
    q.addEventListener('input', ir);
    if (cat) cat.addEventListener('change', ir);
  });
})();

// Tipo de pieza (técnico): «agotada» muestra el inventario; «nueva» pide el nombre.
document.querySelectorAll('[data-tipo-pieza]').forEach(function (g) {
  var f = g.closest('form'), nueva = f.querySelector('[data-bloque-nueva]'), agot = f.querySelector('[data-bloque-agotada]');
  function mostrar() {
    var esAgotada = f.querySelector('[name="tipo_pieza"]:checked').value === 'agotada';
    nueva.classList.toggle('hidden', esAgotada);
    agot.classList.toggle('hidden', !esAgotada);
    agot.querySelector('select').disabled = !esAgotada;
  }
  g.addEventListener('change', mostrar);
  mostrar();
});

// Cotización (administrador): al elegir la categoría solo se ven los proveedores de esa categoría.
document.querySelectorAll('[data-categoria-pieza]').forEach(function (cat) {
  var prov = cat.closest('form').querySelector('[data-proveedor]');
  function filtrar() {
    var id = cat.value;
    Array.prototype.forEach.call(prov.options, function (o) {
      if (!o.value) return;
      var ok = !id || (o.dataset.cats || '').split(',').indexOf(id) !== -1;
      o.hidden = !ok; o.disabled = !ok;
    });
    if (prov.selectedOptions[0] && prov.selectedOptions[0].disabled) prov.value = '';
  }
  cat.addEventListener('change', filtrar);
});
