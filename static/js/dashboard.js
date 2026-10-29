// Dashboard del administrador: diez gráficos con Chart.js (incluido en static/js/vendor, no necesita internet).
(function () {
  if (typeof Chart === 'undefined') return;
  var datos = JSON.parse(document.getElementById('datos-dashboard').textContent);
  var graficos = [];
  var COL = { verde: '#10b981', rojo: '#f43f5e', ambar: '#f59e0b', violeta: '#8b5cf6', celeste: '#0ea5e9', gris: '#94a3b8', naranja: '#f97316' };
  var CATEGORIAS = [COL.violeta, COL.celeste, COL.ambar, COL.verde, COL.rojo, COL.gris];

  function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
  var dinero = function (n) { return '$' + Number(n).toFixed(2); };

  function vacio(canvas, hayDatos) {
    var msg = canvas.parentNode.querySelector('[data-vacio]');
    msg.classList.toggle('hidden', hayDatos);
    msg.classList.toggle('flex', !hayDatos);
    canvas.classList.toggle('hidden', !hayDatos);
    return hayDatos;
  }
  var alguno = function (v) { return v.some(function (n) { return n > 0; }); };

  function construir() {
    graficos.forEach(function (g) { g.destroy(); });
    graficos = [];
    var fg = css('--fg') || '#f8fafc';
    var rejilla = css('--bd') || 'rgba(255,255,255,.16)';
    Chart.defaults.color = fg;
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
    var ejes = function (extra) {
      return Object.assign({ x: { grid: { display: false }, ticks: { color: fg } }, y: { beginAtZero: true, grid: { color: rejilla }, ticks: { color: fg } } }, extra || {});
    };
    var por = function (id) { return document.querySelector('[data-grafico="' + id + '"]'); };
    var crear = function (id, hay, config) {
      var c = por(id);
      if (!c || !vacio(c, hay)) return;
      graficos.push(new Chart(c, config));
    };
    var d = datos;

    crear('ingresos_gastos', alguno(d.ingresos_gastos.ingresos) || alguno(d.ingresos_gastos.gastos), {
      type: 'bar',
      data: { labels: d.ingresos_gastos.etiquetas, datasets: [
        { label: 'Ingresos', data: d.ingresos_gastos.ingresos, backgroundColor: COL.verde, borderRadius: 4 },
        { label: 'Gastos', data: d.ingresos_gastos.gastos, backgroundColor: COL.rojo, borderRadius: 4 } ] },
      options: { scales: ejes(), plugins: { tooltip: { callbacks: { label: function (c) { return c.dataset.label + ': ' + dinero(c.parsed.y); } } } } }
    });

    var u = d.utilidad;
    crear('utilidad', u.valores.some(function (n) { return n !== 0; }), {
      type: 'line',
      data: { labels: u.etiquetas, datasets: [{ label: 'Utilidad', data: u.valores, borderColor: COL.violeta, backgroundColor: 'rgba(139,92,246,.2)', fill: true, tension: .3, pointRadius: 4 }] },
      options: { scales: Object.assign(ejes(), { y: { grid: { color: rejilla }, ticks: { color: fg } } }), plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (c) { return dinero(c.parsed.y); } } } } }
    });

    crear('recibidas', alguno(d.recibidas.valores), {
      type: 'bar',
      data: { labels: d.recibidas.etiquetas, datasets: [{ label: 'Equipos', data: d.recibidas.valores, backgroundColor: COL.celeste, borderRadius: 4 }] },
      options: { scales: Object.assign(ejes(), { y: { beginAtZero: true, grid: { color: rejilla }, ticks: { color: fg, precision: 0 } } }), plugins: { legend: { display: false } } }
    });

    crear('estados', alguno(d.estados.valores), {
      type: 'doughnut',
      data: { labels: d.estados.etiquetas, datasets: [{ data: d.estados.valores, backgroundColor: [COL.gris, COL.ambar, COL.violeta, COL.verde, COL.celeste, COL.naranja, COL.rojo], borderColor: css('--mo') || '#160a35' }] },
      options: { plugins: { legend: { position: 'bottom' } } }
    });

    crear('servicios', alguno(d.servicios.valores), {
      type: 'bar',
      data: { labels: d.servicios.etiquetas, datasets: [{ label: 'Órdenes', data: d.servicios.valores, backgroundColor: COL.violeta, borderRadius: 4 }] },
      options: { indexAxis: 'y', scales: { x: { beginAtZero: true, grid: { color: rejilla }, ticks: { color: fg, precision: 0 } }, y: { grid: { display: false }, ticks: { color: fg } } }, plugins: { legend: { display: false } } }
    });

    crear('carga', d.carga.etiquetas.length > 0, {
      type: 'bar',
      data: { labels: d.carga.etiquetas, datasets: [{ label: 'Casos activos', data: d.carga.valores, backgroundColor: COL.ambar, borderRadius: 4 }] },
      options: { scales: Object.assign(ejes(), { y: { beginAtZero: true, grid: { color: rejilla }, ticks: { color: fg, precision: 0 } } }), plugins: { legend: { display: false } } }
    });

    crear('composicion', alguno(d.composicion.valores), {
      type: 'doughnut',
      data: { labels: d.composicion.etiquetas, datasets: [{ data: d.composicion.valores, backgroundColor: [COL.celeste, COL.violeta, COL.rojo, COL.naranja], borderColor: css('--mo') || '#160a35' }] },
      options: { plugins: { legend: { position: 'bottom' }, tooltip: { callbacks: { label: function (c) { return c.label + ': ' + dinero(c.parsed); } } } } }
    });

    crear('mas_usados', d.mas_usados.etiquetas.length > 0, {
      type: 'bar',
      data: { labels: d.mas_usados.etiquetas, datasets: [{ label: 'Unidades', data: d.mas_usados.valores, backgroundColor: COL.verde, borderRadius: 4 }] },
      options: { indexAxis: 'y', scales: { x: { beginAtZero: true, grid: { color: rejilla }, ticks: { color: fg, precision: 0 } }, y: { grid: { display: false }, ticks: { color: fg } } }, plugins: { legend: { display: false } } }
    });

    crear('stock_bajo', d.stock_bajo.etiquetas.length > 0, {
      type: 'bar',
      data: { labels: d.stock_bajo.etiquetas, datasets: [
        { label: 'Stock actual', data: d.stock_bajo.stock, backgroundColor: COL.rojo, borderRadius: 4 },
        { label: 'Mínimo', data: d.stock_bajo.minimo, backgroundColor: COL.gris, borderRadius: 4 } ] },
      options: { scales: Object.assign(ejes(), { y: { beginAtZero: true, grid: { color: rejilla }, ticks: { color: fg, precision: 0 } } }) }
    });

    crear('metodos', alguno(d.metodos.valores), {
      type: 'doughnut',
      data: { labels: d.metodos.etiquetas, datasets: [{ data: d.metodos.valores, backgroundColor: [COL.verde, COL.celeste], borderColor: css('--mo') || '#160a35' }] },
      options: { plugins: { legend: { position: 'bottom' } } }
    });

    crear('cumplimiento', alguno(d.cumplimiento.valores), {
      type: 'doughnut',
      data: { labels: d.cumplimiento.etiquetas, datasets: [{ data: d.cumplimiento.valores, backgroundColor: [COL.verde, COL.rojo], borderColor: css('--mo') || '#160a35' }] },
      options: { plugins: { legend: { position: 'bottom' } } }
    });
  }

  construir();
  // Al cambiar entre día y noche los colores del texto cambian: se vuelven a dibujar
  new MutationObserver(construir).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
})();
