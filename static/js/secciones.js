// Secciones plegables: «Abrir todo / Minimizar todo», los accesos rápidos abren la sección y se recuerda cómo la dejó cada quien.
(function () {
  var raiz = document.querySelector('[data-secciones]');
  if (!raiz) return;
  var clave = 'tecnofix_secciones_' + (raiz.dataset.secciones || 'x');
  var guardado = {};
  try { guardado = JSON.parse(localStorage.getItem(clave) || '{}') || {}; } catch (e) { guardado = {}; }
  var cajas = Array.prototype.slice.call(raiz.querySelectorAll('details'));

  function guardar() {
    var datos = {};
    cajas.forEach(function (d) { if (d.id) datos[d.id] = d.open; });
    try { localStorage.setItem(clave, JSON.stringify(datos)); } catch (e) { /* sin almacenamiento: no pasa nada */ }
  }
  cajas.forEach(function (d) {
    if (d.id && Object.prototype.hasOwnProperty.call(guardado, d.id) && !d.hasAttribute('data-fija')) d.open = !!guardado[d.id];
    d.addEventListener('toggle', guardar);
  });

  function todo(abrir) { cajas.forEach(function (d) { d.open = abrir; }); guardar(); }
  document.querySelectorAll('[data-abrir-todo]').forEach(function (b) { b.addEventListener('click', function () { todo(true); }); });
  document.querySelectorAll('[data-cerrar-todo]').forEach(function (b) { b.addEventListener('click', function () { todo(false); }); });

  document.querySelectorAll('[data-ir-seccion]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      var destino = document.querySelector(a.getAttribute('href'));
      if (!destino) return;
      e.preventDefault();
      if (destino.tagName === 'DETAILS') destino.open = true;
      destino.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
})();
