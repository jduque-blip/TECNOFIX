// Calculadora de reposición: precio según el proveedor, total a pagar y comparación entre proveedores.
(function () {
  var f = document.querySelector('[data-reposicion]');
  if (!f) return;
  var prov = f.querySelector('select[name=proveedor]');
  var cant = f.querySelector('input[name=cantidad]');
  var precio = f.querySelector('input[name=precio_unit]');
  var resumen = f.querySelector('[data-resumen]');
  var venta = parseFloat(f.dataset.venta) || 0;
  var stock = parseInt(f.dataset.stock, 10) || 0;
  var costo = parseFloat(f.dataset.costo) || 0;
  var margen = parseFloat(f.dataset.margen) || 0;
  var dinero = function (n) { return '$' + n.toFixed(2); };

  function opciones() {
    return Array.prototype.slice.call(prov.options).filter(function (o) { return o.dataset.precio; })
      .map(function (o) { return { nombre: o.text, precio: parseFloat(o.dataset.precio) }; });
  }

  function calcular() {
    var n = parseInt(cant.value, 10), p = parseFloat(precio.value);
    if (!(n >= 1) || !(p >= 0)) { resumen.classList.add('hidden'); return; }
    var html = '', lista = opciones();
    lista.forEach(function (o) {
      html += '<div class="flex justify-between gap-2"><span>' + o.nombre + ' (' + dinero(o.precio) + ')</span><b>' + dinero(n * o.precio) + '</b></div>';
    });
    f.querySelector('[data-lineas]').innerHTML = html;
    var detalle = 'Pedido de ' + n + ' × ' + dinero(p) + '. Stock después: ' + (stock + n) + '.';
    if (venta > 0) detalle += ' Si vendes todo a ' + dinero(venta) + ' ganarías ' + dinero(n * (venta - p)) + '.';
    if (lista.length > 1) {
      var precios = lista.map(function (o) { return o.precio; });
      var ahorro = n * (Math.max.apply(null, precios) - Math.min.apply(null, precios));
      if (ahorro > 0) detalle += ' Comprando al más barato ahorras ' + dinero(ahorro) + ' frente al más caro.';
    }
    if (stock > 0 && costo > 0) {
      var prom = Math.round((stock * costo + n * p) / (stock + n) * 100) / 100;
      var sug = Math.ceil(prom * (1 + margen / 100) * 20) / 20;
      detalle += ' Costo promedio nuevo: ' + dinero(prom) + ' (antes ' + dinero(costo) + '; quedan ' + stock + ' unidades al costo anterior). Precio sugerido: ' + dinero(sug) + '.';
      if (venta > 0 && venta < prom) detalle += ' ¡Ojo! Tu precio de venta actual queda por debajo del costo.';
    }
    f.querySelector('[data-total]').textContent = dinero(n * p);
    f.querySelector('[data-detalle]').textContent = detalle;
    resumen.classList.remove('hidden');
  }

  prov.addEventListener('change', function () {
    var o = prov.options[prov.selectedIndex];
    if (o && o.dataset.precio) precio.value = o.dataset.precio;
    calcular();
  });
  [cant, precio].forEach(function (e) { e.addEventListener('input', calcular); });
  var b = f.querySelector('[data-usar-sugerida]');
  if (b) b.addEventListener('click', function () { cant.value = b.dataset.usarSugerida; calcular(); });
  calcular();
})();
