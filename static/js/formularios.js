// Comportamientos de formularios que se usan en varias pantallas.
//   <button data-confirmar="¿Seguro?"> -> pide confirmación antes de enviar el formulario
//   <input data-buscar>                -> envía la búsqueda mientras se escribe
//   <input type=file data-archivo>     -> muestra el nombre del archivo elegido en [data-nombre-archivo]
//   <input type=file multiple data-fotos data-vista="#id" data-aviso="#id" data-max="4" data-max-mb="3">
//                                      -> acumula las fotos que se van eligiendo, las muestra en miniatura y deja quitar cada una
//   <input data-autoenviar>            -> envía su formulario al terminar de editar (al cambiar el valor)

//   <button data-generar-clave="#campo"> -> escribe en el campo una clave corta aleatoria (sin letras confusas)
//   <button data-copiar="#elemento">   -> copia el texto del elemento al portapapeles

(function () {
  document.addEventListener('click', e => {
    const gen = e.target.closest('[data-generar-clave]');
    if (gen) {
      const campo = document.querySelector(gen.dataset.generarClave);
      const letras = 'abcdefghjkmnpqrstuvwxyz23456789';
      const azar = new Uint32Array(6);
      crypto.getRandomValues(azar);
      if (campo) campo.value = Array.from(azar, n => letras[n % letras.length]).join('');
      return;
    }
    const cop = e.target.closest('[data-copiar]');
    if (cop) {
      const texto = (document.querySelector(cop.dataset.copiar) || {}).textContent || '';
      const listo = () => { cop.lastChild.textContent = ' ¡Copiada!'; };
      if (navigator.clipboard) navigator.clipboard.writeText(texto.trim()).then(listo, () => {});
      else {
        const r = document.createRange(); r.selectNodeContents(document.querySelector(cop.dataset.copiar));
        const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); document.execCommand('copy'); listo();
      }
    }
  });

  document.addEventListener('submit', e => {
    const aviso = e.submitter && e.submitter.dataset.confirmar;
    if (aviso && !window.confirm(aviso)) e.preventDefault();
  });

  document.addEventListener('change', e => {
    if (e.target.matches('[data-autoenviar]') && e.target.form) e.target.form.requestSubmit();
  });

  document.addEventListener('change', e => {
    if (!e.target.matches('[data-archivo]')) return;
    const nombre = e.target.closest('label').querySelector('[data-nombre-archivo]');
    const n = e.target.files.length;
    if (nombre && n) nombre.textContent = n === 1 ? e.target.files[0].name : n + ' fotos elegidas';
  });

  document.querySelectorAll('[data-buscar]').forEach(campo => {
    let reloj;
    campo.addEventListener('input', () => {
      clearTimeout(reloj);
      reloj = setTimeout(() => campo.form.submit(), 400);
    });
    // Tras buscar, el cursor vuelve al final del texto para seguir escribiendo
    if (campo.value) {
      campo.focus();
      campo.setSelectionRange(campo.value.length, campo.value.length);
    }
  });

  // ---- Varias fotos: cada vez que se elige una, se SUMA a las anteriores (el navegador por defecto las reemplaza)
  document.querySelectorAll('input[data-fotos]').forEach(input => {
    const max = Number(input.dataset.max) || 4;
    const maxMb = Number(input.dataset.maxMb) || 3;
    const vista = document.querySelector(input.dataset.vista);
    const aviso = document.querySelector(input.dataset.aviso);
    const etiqueta = input.closest('label').querySelector('[data-nombre-archivo]');
    const textoInicial = etiqueta ? etiqueta.textContent : '';
    let archivos = [];

    function avisar(texto) {
      if (aviso) aviso.textContent = texto || '';
    }

    function sincronizar() {
      // El formulario envía lo que haya en input.files, así que se reconstruye con la lista acumulada
      const lista = new DataTransfer();
      archivos.forEach(a => lista.items.add(a));
      input.files = lista.files;
      vista.innerHTML = '';
      archivos.forEach((archivo, i) => {
        const caja = document.createElement('div');
        caja.className = 'relative';
        const img = document.createElement('img');
        img.src = URL.createObjectURL(archivo);
        img.alt = archivo.name;
        img.className = 'w-20 h-20 object-cover rounded-xl';
        const quitar = document.createElement('button');
        quitar.type = 'button';
        quitar.textContent = '×';
        quitar.title = 'Quitar esta foto';
        quitar.setAttribute('aria-label', 'Quitar la foto ' + archivo.name);
        quitar.className = 'absolute -top-2 -right-2 w-6 h-6 rounded-full bg-rose-500 text-white font-bold leading-none shadow';
        quitar.addEventListener('click', () => {
          archivos.splice(i, 1);
          avisar('');
          sincronizar();
        });
        caja.append(img, quitar);
        vista.append(caja);
      });
      if (etiqueta) {
        etiqueta.textContent = archivos.length
          ? archivos.length + ' de ' + max + ' fotos. Toca aquí para añadir más'
          : textoInicial;
      }
    }

    input.addEventListener('change', () => {
      let mensaje = '';
      for (const archivo of Array.from(input.files)) {
        const repetida = archivos.some(a => a.name === archivo.name && a.size === archivo.size && a.lastModified === archivo.lastModified);
        if (repetida) continue;
        if (!/^image\/(jpeg|png|webp)$/.test(archivo.type)) {
          mensaje = '«' + archivo.name + '» no es JPG, PNG ni WEBP.';
          continue;
        }
        if (archivo.size > maxMb * 1024 * 1024) {
          mensaje = '«' + archivo.name + '» pesa más de ' + maxMb + ' MB.';
          continue;
        }
        if (archivos.length >= max) {
          mensaje = 'Máximo ' + max + ' fotos. Quita alguna para añadir otra.';
          break;
        }
        archivos.push(archivo);
      }
      avisar(mensaje);
      sincronizar();
    });
  });
})();
