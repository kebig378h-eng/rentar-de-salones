# EventoSuite — Plan de desarrollo y lanzamiento comercial

_Fecha: 27 de octubre de 2026_

## 1. Dónde está hoy la app

**Lo que ya funciona (y vende):**

- Reservaciones con bloqueo de fechas por salón (sin choques)
- Clientes, pagos, contratos en PDF y reporte de ingresos (ReportLab)
- Chat en tiempo real dueño ↔ cliente (Socket.IO) + chat de soporte
- Mapa, calendario, ficha pública del salón con reseñas
- 3 roles (admin / dueño / cliente), planes de suscripción con Stripe + webhook
- API REST con token, landing, página de precios, términos/privacidad (borradores), PWA (manifest + service worker)

**Lo que impide venderla hoy:**

| # | Problema | Impacto |
|---|----------|---------|
| 1 | 17 archivos con ~1,200 líneas **sin commit** desde el 23 de abril | Si se daña el disco, se pierde medio año de trabajo |
| 2 | `admin.txt` con correo/contraseña está en el **historial de git** y se subió a GitHub (`rentar-de-salones`) | Cualquiera con acceso al repo ve credenciales |
| 3 | `Procfile` usa `gunicorn app:app` sin worker eventlet | **El chat no funciona en producción** |
| 4 | `socketio.run(app, debug=True)` y `cors_allowed_origins="*"` | Consola de depuración expuesta; cualquier sitio puede conectarse al chat |
| 5 | Sin protección CSRF en formularios ni límite de intentos en login | Riesgo de ataques básicos |
| 6 | Fotos en `static/uploads/` | En Railway/Heroku el disco se borra en cada deploy → se pierden fotos de salones |
| 7 | Correo por Gmail SMTP | Límite ~500 correos/día y caen en spam |
| 8 | Submódulo `renta` apunta a `github.com/usuario/renta` (no existe) | Rompe clones y deploys |
| 9 | `manual.txt` habla de XAMPP; términos/privacidad son borradores | No se puede entregar a un cliente así |
| 10 | Sin pruebas automáticas; `app.py` de 1,474 líneas | Cada arreglo puede romper otra cosa |
| 11 | Precio actual: $250 cada 6 meses (~$42/mes) | Muy bajo para cubrir hosting, comisiones y soporte |

---

## 2. Plan de desarrollo por fases

### Fase 0 — Rescate (1–2 días)

- [ ] Hacer commit de todo el trabajo pendiente
- [ ] Poner el repo de GitHub en **privado**
- [ ] Cambiar la contraseña que estaba en `admin.txt` (y cualquier llave que estuvo en git); limpiar el historial con `git filter-repo` o empezar un repo nuevo limpio
- [ ] Quitar el submódulo `renta` y borrar `__pycache__`, `torre.txt`, `estructura.txt` viejos

### Fase 1 — Listo para producción (semana 1–2)

- [ ] `Procfile`: `web: gunicorn -k eventlet -w 1 app:app`
- [ ] `debug` desde variable de entorno (apagado en producción); CORS de Socket.IO solo a tu dominio
- [ ] Agregar **Flask-WTF** (CSRF) y **Flask-Limiter** (máx. intentos en login/registro/recuperar)
- [ ] Cookies seguras: `SESSION_COOKIE_SECURE`, `HTTPONLY`, `SAMESITE=Lax`
- [ ] Fotos a **Cloudinary** (plan gratis alcanza para empezar) en lugar del disco
- [ ] Correo transaccional con **Brevo** o **Resend** (gratis hasta cierto volumen) + dominio propio
- [ ] Pool de conexiones MySQL y manejo de errores (página 500 amigable)
- [ ] Respaldo automático diario de la base de datos

### Fase 2 — Calidad y pulido (semana 2–3)

- [ ] Pruebas con `pytest` de lo crítico: choque de fechas, permisos por rol, límite de plan, webhook de Stripe
- [ ] Separar `app.py` en blueprints (auth, salones, reservaciones, pagos, chat, api, admin)
- [ ] Revisión en celular de todas las pantallas (los dueños lo usarán desde el teléfono)
- [ ] **Onboarding**: al registrarse, asistente de 3 pasos (crear salón → subir fotos → primera reservación)
- [ ] Recordatorios automáticos por correo: anticipo pendiente, evento en 7 días, pago vencido
- [ ] Contrato PDF con logo y datos del dueño (personalizable) — es lo que más presume un dueño
- [ ] Datos de demostración (`seed_demo.sql`): 3 salones, 20 reservaciones, pagos y chats

### Fase 3 — Lanzamiento técnico (semana 3–4)

- [ ] Dominio propio (ej. `eventosuite.mx`) + HTTPS
- [ ] Deploy en Railway (o VPS) con MySQL administrado
- [ ] Stripe en modo real; evaluar **Mercado Pago** (OXXO, transferencia — muchos dueños en México no tienen tarjeta empresarial)
- [ ] **Aviso de privacidad** y términos revisados por un abogado (ley mexicana de datos personales)
- [ ] Facturación CFDI: decidir si se factura a través de IQ Desarrollo de Software
- [ ] Monitoreo de errores (Sentry gratis) y uptime (UptimeRobot)
- [ ] Instancia **demo** pública con usuario de prueba (se reinicia cada noche)

### Fase 4 — Piloto (semana 4–6)

- [ ] Conseguir **2–3 salones reales** (conocidos, familia, Uruapan/Michoacán) gratis por 2–3 meses a cambio de retroalimentación y testimonio
- [ ] Reunión semanal corta con cada uno; arreglar lo que pidan
- [ ] Medir: reservaciones registradas, uso del chat, qué pantallas no usan
- [ ] Sacar de ahí 2 testimonios con foto y un caso de éxito ("dejé de perder fechas dobles")

---

## 3. Cómo venderlo — tres caminos

### Camino A: SaaS directo a dueños de salones (recomendado para empezar)

- **Cliente:** salones de fiestas, jardines de eventos, quintas, terrazas, salones de hoteles pequeños
- **Dolor que resuelve:** agenda en libreta/WhatsApp, fechas dobles, no saber quién debe anticipo, contratos hechos en Word
- **Precio sugerido** (revisar contra competencia antes de fijarlo):
  - Prueba gratis 30 días
  - Básico ~$299 MXN/mes (1 salón)
  - Pro ~$499 MXN/mes (salones ilimitados, contratos con logo, recordatorios)
  - Pago anual con 2 meses gratis
- **Canales:** visitar salones en persona con la demo en el celular, grupos de Facebook de proveedores de eventos, Google Ads local ("software para salón de eventos"), página de IQIASoft

### Camino B: Licencia o marca blanca a una empresa del ramo

Empresas a las que se les puede ofrecer:

- **Cadenas o grupos** con varios salones/jardines (una sola venta grande)
- **Hoteles** con salones de banquetes
- **Wedding planners y agencias de eventos** que administran varios recintos
- **Directorios/marketplaces de eventos** que quieran dar herramienta de gestión a sus proveedores
- **Asociaciones o cámaras** (CANACO local, asociaciones de banquetes) como canal para sus agremiados

Formas de cobrar:

- Licencia de instalación + mantenimiento mensual
- Marca blanca (su logo, su dominio) con cuota por salón
- Desarrollo a la medida sobre la base de EventoSuite (cobro por proyecto, vía IQ Desarrollo de Software)

### Camino C: Vender el producto completo

Si no quieres operarlo: venta del código + transferencia a una software house o agencia de eventos. Vale mucho más **con clientes pagando** (Fase 4), así que no conviene antes del piloto.

---

## 4. Kit de venta (lo que hay que preparar)

- [ ] Demo en línea con datos de ejemplo
- [ ] Video de 2 minutos: "un día en la vida del dueño del salón"
- [ ] One-pager / tríptico en PDF (sin precios para redes sociales)
- [ ] Presentación de 8–10 diapositivas para empresas (problema → demo → beneficios → planes → piloto)
- [ ] Hoja de precios separada (para enviar por WhatsApp/correo)
- [ ] Contrato de servicio / licencia y SLA básico (soporte en horario, respaldos, tiempo de respuesta)
- [ ] Manual de usuario actualizado (reemplazar `manual.txt` de XAMPP) + guía rápida para el dueño
- [ ] WhatsApp Business para soporte y ventas

---

## 5. Calendario resumido

| Semana | Entregable |
|--------|-----------|
| 0 | Código respaldado, repo privado, credenciales rotadas |
| 1–2 | App segura y desplegable (chat funcionando en producción) |
| 2–3 | Pruebas, onboarding, recordatorios, demo con datos |
| 3–4 | Dominio, pagos reales, legal, demo pública |
| 4–6 | Piloto con 2–3 salones |
| 6+ | Venta: SaaS directo + acercamiento a 5–10 empresas del ramo |

## 6. "Listo para vender" significa

- Un dueño nuevo se registra, crea su salón y registra una reservación **sin ayuda** en menos de 10 minutos
- El chat, los correos y el cobro funcionan en el dominio real
- Hay respaldos diarios y te enteras si la app se cae
- Tienes al menos un salón real usándola y un testimonio
