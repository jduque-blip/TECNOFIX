// Chat interno: baja al último mensaje, pide los nuevos cada 5 segundos y envía con Ctrl + Enter.
(function () {
  const caja = document.getElementById('mensajes');
  if (!caja) return;
  let ultimo = Number(caja.dataset.ultimo) || 0;
  const bajar = () => { caja.scrollTop = caja.scrollHeight; };
  bajar();

  function burbuja(m) {
    const fila = document.createElement('div');
    fila.className = 'flex' + (m.mio ? ' justify-end' : '');
    fila.dataset.id = m.id;
    const globo = document.createElement('div');
    globo.className = 'max-w-[80%] rounded-2xl px-4 py-2 ' + (m.mio ? 'g1 text-white' : 'glass');
    if (m.orden) {
      const o = document.createElement('div');
      o.className = 'text-[11px] opacity-70 mb-1'; o.textContent = 'Sobre ' + m.orden; globo.appendChild(o);
    }
    const t = document.createElement('div');
    t.className = 'whitespace-pre-line break-words'; t.textContent = m.texto; globo.appendChild(t);
    const h = document.createElement('div');
    h.className = 'text-[11px] opacity-70 mt-1 text-right'; h.textContent = m.hora; globo.appendChild(h);
    fila.appendChild(globo);
    return fila;
  }

  async function pedir() {
    if (document.hidden) return;
    try {
      const r = await fetch(caja.dataset.url + '?despues=' + ultimo, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
      if (!r.ok) return;
      const datos = await r.json();
      if (!datos.mensajes.length) return;
      const abajo = caja.scrollHeight - caja.scrollTop - caja.clientHeight < 80;
      const vacio = caja.querySelector('[data-vacio]'); if (vacio) vacio.remove();
      datos.mensajes.forEach(m => {
        if (!caja.querySelector('[data-id="' + m.id + '"]')) caja.appendChild(burbuja(m));
        ultimo = Math.max(ultimo, m.id);
      });
      if (abajo) bajar();
    } catch (e) { /* sin conexión: se reintenta en el siguiente ciclo */ }
  }
  setInterval(pedir, 5000);

  document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && e.target.matches('textarea[name=texto]')) e.target.form.requestSubmit();
  });
})();
