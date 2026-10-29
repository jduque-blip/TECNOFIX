// Formulario del producto: proveedores por categoría y precios calculados.
//   1) Al elegir la categoría, las listas de proveedores muestran solo los que venden esa categoría.
//   2) Costo de pedido = precio del proveedor más barato (se llena solo; se puede escribir a mano si no hay proveedores).
//   3) Costo de venta = costo de pedido + margen %, redondeado hacia arriba de 5 en 5 centavos (igual que en el servidor).
//      Mientras no lo toques a mano, se recalcula solo; si lo cambias, se respeta tu valor ("Usar sugerido" lo recupera).
(function () {
  const f = document.querySelector('form[data-precio]');
  if (!f) return;
  const campo = n => f.querySelector('[name=' + n + ']');
  const num = n => { const v = parseFloat((campo(n) || {}).value); return isNaN(v) ? null : v; };
  const caja = f.querySelector('[data-sugerencia]');
  const general = parseFloat(f.dataset.margenGeneral) || 0;
  const nuevo = f.dataset.nuevo === '1';
  const venta = campo('precio_venta');
  const costo = campo('costo_pedido');
  const margenCampo = campo('margen_propio');
  let automatico = nuevo || !venta.value; // un producto existente conserva su precio hasta que se pida el sugerido

  // ---- 1) proveedores de la categoría elegida
  const cat = f.querySelector('[data-categoria]');
  const selects = [...f.querySelectorAll('[data-proveedor]')];
  const aviso = f.querySelector('[data-aviso-proveedores]');
  const verTodos = f.querySelector('[data-ver-todos]');
  let todos = false;

  function filtrarProveedores() {
    const c = cat ? cat.value : '';
    const nombre = c ? cat.options[cat.selectedIndex].text : '';
    selects.forEach(sel => {
      [...sel.options].forEach(o => {
        if (!o.value) return;
        const cats = (o.dataset.cats || '').split(',');
        const coincide = !c || todos || cats.includes(c) || o.selected;
        o.hidden = !coincide;
        o.disabled = !coincide;
      });
    });
    if (!aviso) return;
    if (!c) { aviso.textContent = 'Elige primero la categoría y aquí aparecerán los proveedores que la venden.'; if (verTodos) verTodos.classList.add('hidden'); return; }
    const hay = selects[0] && [...selects[0].options].some(o => o.value && !o.hidden);
    aviso.textContent = todos ? 'Mostrando todos los proveedores.'
      : hay ? 'Proveedores que venden «' + nombre + '» (aunque vendan otras categorías también).'
            : 'Ningún proveedor tiene la categoría «' + nombre + '» todavía. Puedes asignársela desde Proveedores.';
    if (verTodos) { verTodos.classList.remove('hidden'); verTodos.textContent = todos ? 'Solo los de la categoría' : 'Mostrar todos los proveedores'; }
  }
  if (verTodos) verTodos.addEventListener('click', () => { todos = !todos; filtrarProveedores(); });
  if (cat) cat.addEventListener('change', filtrarProveedores);

  // ---- 2) y 3) precios
  function sugerido(c, margen) {
    const centavos = Math.round(c * (1 + margen / 100) * 100 * 1e6) / 1e6; // evita errores de coma flotante
    return Math.ceil(centavos / 5 - 1e-9) * 5 / 100;
  }
  const precios = () => ['1', '2'].filter(n => campo('proveedor' + n).value).map(n => num('precio' + n)).filter(v => v && v > 0);

  function actualizar() {
    const ps = precios();
    if (ps.length) { costo.value = Math.min(...ps).toFixed(2); costo.readOnly = true; costo.classList.add('opacity-70'); }
    else { costo.readOnly = false; costo.classList.remove('opacity-70'); }
    const c = num('costo_pedido');
    const m = num('margen_propio');
    const margen = m !== null ? m : general;
    caja.textContent = '';
    if (!c || c <= 0) { caja.textContent = 'Escribe el precio de un proveedor para calcular el costo de venta.'; caja.className = 'text-xs px-1 mt-1 opacity-60'; return; }
    const s = sugerido(c, margen);
    if (automatico) venta.value = s.toFixed(2);
    caja.className = 'text-xs px-1 mt-1';
    caja.append('Sugerido: $' + s.toFixed(2) + ' (costo $' + c.toFixed(2) + ' + ' + margen + '%) ');
    if (!automatico) {
      const usar = document.createElement('button');
      usar.type = 'button'; usar.className = 'underline font-bold'; usar.textContent = 'Usar sugerido';
      usar.addEventListener('click', () => { automatico = true; actualizar(); });
      caja.appendChild(usar);
    }
    const v = num('precio_venta');
    if (v) {
      const real = (v / c - 1) * 100;
      const t = document.createElement('span');
      t.className = real < margen - 0.05 ? 'text-amber-500 font-bold' : 'opacity-70';
      t.textContent = ' · margen con este precio: ' + real.toFixed(1) + '%';
      caja.appendChild(t);
    }
  }

  venta.addEventListener('input', () => { automatico = false; actualizar(); });
  f.addEventListener('input', e => { if (e.target !== venta) actualizar(); });
  f.addEventListener('change', e => { if (e.target !== venta) actualizar(); });
  filtrarProveedores();
  actualizar();
})();
