# EventoSuite

Sistema de gestión para dueños de salones de eventos: reservaciones sin
choques de fecha, pagos, contratos en PDF, chat con clientes, mapa y
calendario — todo en un solo panel. Pensado para venderse como suscripción
mensual (SaaS) a dueños de salones reales.

## Stack

- **Backend:** Flask + Flask-SocketIO (chat en tiempo real) + Flask-Mail
- **Base de datos:** MySQL
- **PDF:** ReportLab (contratos y reportes de ingresos)
- **Cobros:** Stripe Checkout (suscripciones)
- **Deploy:** Gunicorn + eventlet (ver `Procfile`, listo para Railway/Heroku)

## Instalación local

1. Crea un entorno virtual e instala dependencias:

   ```bash
   pip install -r requirements.txt
   ```

2. Copia `.env.example` a `.env` y llena tus propios valores (base de datos,
   correo, token de API y, si vas a probar cobros, tus llaves de prueba de
   Stripe). **Nunca subas `.env` a git.**

3. Crea la base de datos ejecutando `database.sql` contra tu MySQL local.

4. Levanta el servidor:

   ```bash
   python app.py
   ```

   Abre `http://127.0.0.1:5000`.

## Roles

- **admin** — operador de la plataforma: ve todo, administra clientes y
  pagos globales, genera reportes.
- **dueno** — cliente pagador del SaaS: gestiona sus propios salones,
  clientes, reservaciones, pagos, contratos y su plan de suscripción.
- **cliente** — usuario final que reserva salones y platica por chat con
  el dueño.

## Planes y cobros

Modelo comercial: primer mes gratis (`gratis`, 1 salón, gancho de
adquisición), luego un único plan de pago (`pro`, $250 cada 6 meses,
salones ilimitados). Ambos viven en la tabla `planes`
(`precio` + `periodo_meses` definen cuánto y cada cuánto se cobra). Cada
dueño tiene un plan asignado (`usuarios.plan_id`) y un estado de
suscripción (`trial`, `activa`, `vencida`). El límite de salones por plan
se aplica al crear un salón nuevo (`/mis_salones`).

El cobro de los planes pagados corre por Stripe Checkout
(`/suscripcion/checkout/<slug>`) y se confirma vía webhook
(`/webhooks/stripe`). Sin `STRIPE_SECRET_KEY` configurada, esas rutas
avisan que el cobro no está activo todavía en vez de fallar — útil para
desarrollar sin una cuenta de Stripe real.

## Migraciones

`database.sql` crea el esquema completo para una instalación nueva.
Si ya tienes una base de datos en producción (por ejemplo en Railway),
usa `migracion_saas.sql` para aplicar los cambios de esquema más
recientes ahí — revísalo antes de correrlo, algunos pasos requieren tu
criterio (a qué dueño asignar datos existentes, etc.).

## Seguridad — antes de vender esto a un cliente real

- Rota cualquier credencial que haya estado en el historial de git.
- Corre con `debug=False` en producción.
- Verifica que `SECRET_KEY` y `API_TOKEN` sean valores aleatorios largos,
  no los de `.env.example`.
- Revisa `templates/terminos.html` y `templates/privacidad.html`: son
  borradores, no asesoría legal — que los revise un abogado antes de
  publicarlos.
