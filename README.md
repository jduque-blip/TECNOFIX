# TecnoFix

Sistema web para un taller de reparación de computadoras y equipos. Se usa en un solo computador (no necesita internet ni hosting).
Hecho con Django 5.2, PostgreSQL, Tailwind CSS (ya compilado) y JavaScript.

## 1. Qué hace

| Rol | Qué puede hacer |
|---|---|
| **Cliente** | Pedir un servicio, ver el avance de su equipo, aceptar o rechazar presupuestos y piezas, pagar (efectivo o transferencia con comprobante), descargar su factura y chatear con el taller. |
| **Técnico** | Ver sus órdenes, cambiar el estado (solo hacia adelante), armar presupuestos o proformas, pedir piezas, usar repuestos del inventario, reportar repuestos dañados y escribir notas. |
| **Administrador** | Asignar técnicos (se ve la carga de cada uno), cotizar piezas, hacer pedidos a proveedores, manejar inventario, validar pagos y entregar equipos, registrar gastos y pérdidas, ver el dashboard y descargar reportes. |

## 2. Lo que necesitas instalar (una sola vez)

1. **Python 3.12 o superior** (marca la casilla *Add Python to PATH* al instalar).
2. **PostgreSQL 16 o superior**. Anota la contraseña del usuario `postgres`.

## 3. Instalación paso a paso (Windows)

Abre una terminal (PowerShell o CMD) dentro de la carpeta del proyecto, la que tiene el archivo `manage.py`.

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**Crear la base de datos** (con pgAdmin o en la terminal de PostgreSQL):

```
CREATE DATABASE tecnofix_app;
```

**Configurar la conexión.** El archivo `config/settings.py` lee estos datos de variables de entorno
(si no existen usa valores de prueba):

| Variable | Qué es |
|---|---|
| `DJANGO_SECRET_KEY` | Clave secreta propia (una frase larga y al azar). |
| `DB_NAME` | Nombre de la base (`tecnofix_app`). |
| `DB_USER` | Usuario de PostgreSQL (`postgres`). |
| `DB_PASSWORD` | Contraseña de ese usuario. |
| `DB_HOST` / `DB_PORT` | `localhost` y `5432`. |

**Crear las tablas y cargar los datos de arranque:**

```
python manage.py migrate
python manage.py cargar_base
python manage.py createsuperuser
```

- `cargar_base` crea los servicios, las tarifas, los proveedores de ejemplo, el IVA (15 %) y el **capital inicial ($3 000)**.
  Se puede repetir sin problema: no borra ni duplica nada, y no cambia el capital si ya lo definiste.
- `createsuperuser` crea al administrador: pide correo, nombre y contraseña.

**Encender el sistema:**

```
python manage.py runserver
```

Abre en el navegador: http://127.0.0.1:8000/

Para apagarlo, `Ctrl + C` en la terminal. Para volver a usarlo otro día: abre la terminal en la carpeta, `venv\Scripts\activate` y `python manage.py runserver`.

## 4. Primeros pasos (como administrador)

1. Entra con el correo y la contraseña del administrador (botón *Ingresar*).
2. **Tarifas**: revisa los precios, el IVA, y escribe los **datos de la cuenta** para transferencias.
3. **Personal**: crea a los técnicos (se les da una contraseña temporal que deben cambiar al entrar).
4. **Inventario → Proveedores**: ajusta los proveedores y sus categorías.
5. **Finanzas**: cambia el **capital inicial** por el real del negocio.

## 5. Cómo fluye una reparación

1. El **cliente** solicita el servicio.
2. El **administrador** asigna un técnico (la orden pasa a *En diagnóstico*).
3. El **técnico** envía el presupuesto. Puede elegir repuestos del inventario filtrando por categoría. Si falta una pieza, envía una **proforma** (solo diagnóstico y mano de obra) y luego **pide la pieza**.
4. Si hay pieza: el administrador la **cotiza** con un proveedor → el cliente **acepta** → el administrador la **pide al proveedor** → al llegar, la **registra en el inventario** (eso avisa al técnico y al cliente) → la orden pasa a *En reparación*.
5. El **técnico** registra los repuestos usados (salen del inventario) y pasa la orden a *Listo*.
6. El **cliente** paga en efectivo (en el taller) o por transferencia (sube el comprobante). El **administrador** confirma el pago.
7. El **administrador** entrega el equipo (*Entregado*). Solo se puede entregar con el pago confirmado.

Desde que se envía el primer presupuesto, todo lo aceptado se ve como una sola **Factura general** (descargable en PDF).

## 6. Finanzas, dashboard y reportes

- **Finanzas**: dinero en caja (capital + cobrado − gastado), valor del inventario, ingresos, gastos y utilidad del mes.
  Cada pedido a proveedor genera su gasto solo. Un faltante de inventario o un repuesto dañado queda como *pérdida de inventario*:
  se muestra aparte y no se resta dos veces, porque la mercadería ya se pagó al comprarla.
- **Dashboard**: 4 indicadores y 11 gráficos del negocio (incluye las entregas a tiempo).
- **Reportes**: estado financiero, ingresos, gastos, órdenes, inventario y movimientos, en PDF o en Excel (CSV), por rango de fechas.

## 7. Pruebas automáticas

```
python manage.py test --noinput
```

Tarda unos 15 minutos porque revisa todo el sistema (más de 470 pruebas). Debe terminar con `OK`.

## 8. Estructura

| Carpeta | Contenido |
|---|---|
| `config/` | Configuración y rutas principales. |
| `core/` | Página de inicio, panel, dashboard y datos de arranque (`cargar_base`). |
| `usuarios/` | Ingreso, registro, roles y personal. |
| `ordenes/` | Órdenes, presupuestos, piezas, pagos y facturas. |
| `inventario/` | Productos, proveedores, pedidos y kardex. |
| `finanzas/` | Gastos y pérdidas. |
| `reportes/` | Reportes PDF/CSV y factura PDF. |
| `comunicacion/` | Notificaciones y mensajes. |
| `templates/`, `static/` | Pantallas, estilos y scripts (incluye Chart.js para los gráficos). |

## 9. Si algo falla

| Problema | Solución |
|---|---|
| `No module named ...` | Activa el entorno (`venv\Scripts\activate`) y repite `pip install -r requirements.txt`. |
| `connection refused` / error de base de datos | PostgreSQL no está encendido, o la contraseña/nombre de la base no coincide con las variables `DB_...`. |
| Aparece «no existe la relación ...» | Falta `python manage.py migrate`. |
| El sistema se ve sin estilos | Recarga con `Ctrl + F5`. |
| No recuerdo la contraseña del administrador | `python manage.py changepassword correo@del-admin.com` |

## 10. Qué no incluye

Este sistema funciona solo en local: no tiene hosting, aplicación móvil, pagos en línea ni factura electrónica del SRI.
Las transferencias se validan a mano con el comprobante, y los avisos por WhatsApp se abren con un enlace (`wa.me`).
