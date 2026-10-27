-- ============================================================
--  MIGRACIÓN SaaS — correr manualmente contra la base de datos
--  real (Railway), en orden, fase por fase.
--  NO se ejecuta automáticamente: revisa cada bloque antes de
--  correrlo, sobre todo los que tocan datos existentes.
-- ============================================================

-- ── FASE 2: clientes por dueño ────────────────────────────────
-- Los clientes existentes quedan con dueno_id = NULL (solo el
-- admin los ve). Reasígnalos a un dueño si corresponde:
--   UPDATE clientes SET dueno_id = <id_del_dueno> WHERE id IN (...);
ALTER TABLE clientes
    ADD COLUMN dueno_id INT NULL AFTER correo,
    ADD FOREIGN KEY (dueno_id) REFERENCES usuarios(id) ON DELETE CASCADE;

-- ── FASE 6 (ya aplicada a database.sql, recuerda correrla también aquí) ──
-- Evita reseñas duplicadas del mismo usuario en el mismo salón.
-- Antes de correr esto, borra duplicados si ya existen:
--   SELECT salon_id, usuario_id, COUNT(*) FROM resenas
--   GROUP BY salon_id, usuario_id HAVING COUNT(*) > 1;
ALTER TABLE resenas
    ADD UNIQUE KEY unique_resena (salon_id, usuario_id);

-- ── FASE 3: planes y suscripciones ────────────────────────────
-- Modelo comercial: primer mes gratis (anzuelo para atraer clientes),
-- luego un único plan de paga de $250 cada 6 meses.
CREATE TABLE IF NOT EXISTS planes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(50) NOT NULL,
    slug VARCHAR(30) NOT NULL UNIQUE,
    precio DECIMAL(10,2) NOT NULL DEFAULT 0,
    periodo_meses INT NOT NULL DEFAULT 1,
    max_salones INT,
    max_reservaciones_mes INT
);

INSERT INTO planes (nombre, slug, precio, periodo_meses, max_salones, max_reservaciones_mes) VALUES
    ('Primer Mes Gratis', 'gratis', 0.00,   1, 1,    NULL),
    ('Pro',                'pro',    250.00, 6, NULL, NULL);

-- Si ya habías corrido una versión anterior de esta migración (con
-- columna `precio_mensual` y planes básico/pro mensuales), corre esto
-- en vez de lo de arriba:
--   ALTER TABLE planes CHANGE precio_mensual precio DECIMAL(10,2) NOT NULL DEFAULT 0;
--   ALTER TABLE planes ADD COLUMN periodo_meses INT NOT NULL DEFAULT 1;
--   DELETE FROM planes WHERE slug = 'basico';
--   UPDATE planes SET nombre='Primer Mes Gratis' WHERE slug='gratis';
--   UPDATE planes SET precio=250.00, periodo_meses=6, max_salones=NULL WHERE slug='pro';
--   -- reasigna a quien haya quedado en 'basico':
--   UPDATE usuarios SET plan_id=(SELECT id FROM planes WHERE slug='gratis')
--   WHERE plan_id=(SELECT id FROM planes WHERE slug='basico');

ALTER TABLE usuarios
    ADD COLUMN plan_id INT,
    ADD COLUMN suscripcion_estado ENUM('trial','activa','vencida') DEFAULT 'trial',
    ADD COLUMN trial_termina DATE,
    ADD FOREIGN KEY (plan_id) REFERENCES planes(id);

-- Due ños que ya existían antes de la migración: los dejo en el plan
-- gratis con la suscripción ya 'activa' (no en trial) para que nadie
-- se quede bloqueado de golpe. Súbelos de plan a mano si corresponde.
UPDATE usuarios
SET plan_id = (SELECT id FROM planes WHERE slug = 'gratis'),
    suscripcion_estado = 'activa'
WHERE rol = 'dueno' AND plan_id IS NULL;

-- ── MENSAJERÍA: conversaciones ────────────────────────────────
-- IMPORTANTE: antes de correr el ALTER de `mensajes`, confirma las
-- columnas reales en producción con `SHOW CREATE TABLE mensajes;` —
-- este archivo ya ha estado desincronizado del esquema real antes
-- (pasó con `reservaciones`), no asumas que coincide con database.sql.
--
-- Los mensajes existentes (chat general / por salón) no tienen una
-- conversación a la que asignarse — no hay forma limpia de migrar su
-- contenido al nuevo modelo. Se documentan como perdidos a propósito
-- en vez de inventar una migración heurística. Si te importa
-- conservarlos, expórtalos primero (`SELECT * FROM mensajes;`) antes
-- de truncar.
CREATE TABLE IF NOT EXISTS conversaciones (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tipo ENUM('directo','soporte') NOT NULL DEFAULT 'directo',
    usuario1_id INT NOT NULL,
    usuario2_id INT,
    salon_id INT,
    estado ENUM('abierta','cerrada') NOT NULL DEFAULT 'abierta',
    fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (usuario1_id) REFERENCES usuarios(id) ON DELETE CASCADE,
    FOREIGN KEY (usuario2_id) REFERENCES usuarios(id) ON DELETE CASCADE,
    FOREIGN KEY (salon_id) REFERENCES salon(id) ON DELETE SET NULL
);

TRUNCATE TABLE mensajes;

-- Los nombres `mensajes_ibfk_2`/`mensajes_ibfk_3` son los que MySQL le
-- pone por default a las FK de receptor_id/salon_id en el orden en que
-- database.sql las declaraba — corre primero `SHOW CREATE TABLE
-- mensajes;` y ajusta estos dos nombres si tu base real los llama distinto.
ALTER TABLE mensajes
    DROP FOREIGN KEY mensajes_ibfk_2,  -- FK de receptor_id
    DROP FOREIGN KEY mensajes_ibfk_3,  -- FK de salon_id
    DROP COLUMN receptor_id,
    DROP COLUMN salon_id,
    ADD COLUMN conversacion_id INT NOT NULL AFTER id,
    ADD FOREIGN KEY (conversacion_id) REFERENCES conversaciones(id) ON DELETE CASCADE;

-- ultimo_mensaje_id, no timestamp: dos mensajes en el mismo segundo no
-- deben poder empatar y esconder uno nuevo como "ya leído".
CREATE TABLE IF NOT EXISTS conversacion_lecturas (
    conversacion_id INT NOT NULL,
    usuario_id INT NOT NULL,
    ultimo_mensaje_id INT NOT NULL DEFAULT 0,
    PRIMARY KEY (conversacion_id, usuario_id),
    FOREIGN KEY (conversacion_id) REFERENCES conversaciones(id) ON DELETE CASCADE,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
);

-- ── RECUPERAR CONTRASEÑA ──────────────────────────────────────
ALTER TABLE usuarios
    ADD COLUMN reset_token VARCHAR(64) NULL,
    ADD COLUMN reset_expira DATETIME NULL;

-- ── FASE 7: vencimiento de planes y reservas dobles ──────────
-- Stripe: guardar la suscripción de cada dueño para que los webhooks
-- de renovación/cancelación sepan a quién actualizar.
ALTER TABLE usuarios
    ADD COLUMN stripe_customer_id VARCHAR(64) NULL,
    ADD COLUMN stripe_subscription_id VARCHAR(64) NULL,
    ADD UNIQUE KEY unique_stripe_sub (stripe_subscription_id);

-- El plan gratis ahora sí vence al pasar `trial_termina`. Los dueños que
-- ya estaban en gratis sin fecha (los de la FASE 3) reciben un mes de
-- gracia desde hoy en vez de quedar bloqueados de golpe.
UPDATE usuarios
SET trial_termina = CURDATE() + INTERVAL 30 DAY
WHERE rol = 'dueno' AND trial_termina IS NULL
  AND plan_id = (SELECT id FROM planes WHERE slug = 'gratis');

-- Impide dos reservaciones del mismo salón el mismo día. Antes de
-- correrlo, busca duplicados que ya existan y resuélvelos a mano:
--   SELECT salon_id, fecha, COUNT(*) FROM reservaciones
--   GROUP BY salon_id, fecha HAVING COUNT(*) > 1;
ALTER TABLE reservaciones
    ADD UNIQUE KEY unique_salon_fecha (salon_id, fecha);

-- ── NOTA (no relacionada a lo de arriba) ──────────────────────
-- reservaciones.cliente_id (declarado en database.sql) no coincide con
-- lo que usa app.py, que lee/escribe una columna `cliente` VARCHAR.
-- Ya se sabía que este archivo puede estar desincronizado del esquema
-- real — revísalo con SHOW CREATE TABLE reservaciones; antes de asumir
-- nada, sobre todo si vas a tocar esa tabla en otra migración.
