CREATE DATABASE IF NOT EXISTS eventos_db;
USE eventos_db;

-- =========================
-- USUARIOS
-- =========================
CREATE TABLE usuarios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    correo VARCHAR(100) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    rol ENUM('admin','dueno','cliente') DEFAULT 'cliente',
    reset_token VARCHAR(64),    -- hash sha256 del token de recuperación, no el token en claro
    reset_expira DATETIME
);

-- =========================
-- CLIENTES
-- =========================
CREATE TABLE clientes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    telefono VARCHAR(20),
    correo VARCHAR(100),
    dueno_id INT,

    FOREIGN KEY (dueno_id) REFERENCES usuarios(id)
        ON DELETE CASCADE
);

-- =========================
-- SALONES
-- =========================
CREATE TABLE salon (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    precio DECIMAL(10,2) NOT NULL,
    latitud DECIMAL(10,6),
    longitud DECIMAL(10,6),
    imagen VARCHAR(255),
    capacidad INT,
    descripcion TEXT,
    dueno_id INT NOT NULL,
    FOREIGN KEY (dueno_id) REFERENCES usuarios(id)
        ON DELETE CASCADE
);

-- =========================
-- RESERVACIONES (MEJORADA)
-- =========================
CREATE TABLE reservaciones (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id INT,
    fecha DATE NOT NULL,
    tipo VARCHAR(100),
    salon_id INT,
    visto TINYINT(1) DEFAULT 0,

    -- Un salón no puede tener dos reservaciones el mismo día, aunque
    -- dos personas reserven al mismo tiempo.
    UNIQUE KEY unique_salon_fecha (salon_id, fecha),
    FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        ON DELETE SET NULL,
    FOREIGN KEY (salon_id) REFERENCES salon(id)
        ON DELETE CASCADE
);

-- =========================
-- PAGOS
-- =========================
CREATE TABLE pagos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    reservacion_id INT NOT NULL,
    monto DECIMAL(10,2) NOT NULL,
    tipo_pago VARCHAR(50),
    fecha_pago DATE,

    FOREIGN KEY (reservacion_id) REFERENCES reservaciones(id)
        ON DELETE CASCADE
);

-- =========================
-- CONTRATOS
-- =========================
CREATE TABLE contratos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    reservacion_id INT NOT NULL,
    fecha_contrato DATE,
    condiciones TEXT,

    FOREIGN KEY (reservacion_id) REFERENCES reservaciones(id)
        ON DELETE CASCADE
);

-- =========================
-- CONVERSACIONES
-- =========================
-- tipo='directo': usuario1_id/usuario2_id son los dos participantes
--   (siempre guardados en orden canónico LEAST/GREATEST para que
--   buscar una conversación existente sea una igualdad simple).
-- tipo='soporte': usuario1_id = quien pide ayuda, usuario2_id siempre
--   NULL (cualquier admin puede verla y responder).
CREATE TABLE conversaciones (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tipo ENUM('directo','soporte') NOT NULL DEFAULT 'directo',
    usuario1_id INT NOT NULL,
    usuario2_id INT,
    salon_id INT,
    estado ENUM('abierta','cerrada') NOT NULL DEFAULT 'abierta',
    fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (usuario1_id) REFERENCES usuarios(id)
        ON DELETE CASCADE,
    FOREIGN KEY (usuario2_id) REFERENCES usuarios(id)
        ON DELETE CASCADE,
    FOREIGN KEY (salon_id) REFERENCES salon(id)
        ON DELETE SET NULL
);

-- =========================
-- MENSAJES
-- =========================
CREATE TABLE mensajes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    conversacion_id INT NOT NULL,
    emisor_id INT NOT NULL,
    mensaje TEXT,
    fecha DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (conversacion_id) REFERENCES conversaciones(id)
        ON DELETE CASCADE,
    FOREIGN KEY (emisor_id) REFERENCES usuarios(id)
        ON DELETE CASCADE
);

-- =========================
-- LECTURAS DE CONVERSACIÓN (indicador de "no leído")
-- =========================
-- ultimo_mensaje_id (no un timestamp) para no depender de la precisión
-- de segundo de DATETIME — dos mensajes en el mismo segundo no deben
-- poder empatar y esconder un mensaje nuevo como "ya leído".
CREATE TABLE conversacion_lecturas (
    conversacion_id INT NOT NULL,
    usuario_id INT NOT NULL,
    ultimo_mensaje_id INT NOT NULL DEFAULT 0,

    PRIMARY KEY (conversacion_id, usuario_id),
    FOREIGN KEY (conversacion_id) REFERENCES conversaciones(id)
        ON DELETE CASCADE,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        ON DELETE CASCADE
);

-- =========================
-- RESEÑAS
-- =========================
CREATE TABLE resenas (
    id INT AUTO_INCREMENT PRIMARY KEY,
    salon_id INT,
    usuario_id INT,
    calificacion TINYINT NOT NULL,
    comentario TEXT,
    fecha DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (salon_id) REFERENCES salon(id)
        ON DELETE CASCADE,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        ON DELETE CASCADE,
    UNIQUE KEY unique_resena (salon_id, usuario_id)
);

-- =========================
-- PLANES (SaaS)
-- =========================
CREATE TABLE planes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(50) NOT NULL,
    slug VARCHAR(30) NOT NULL UNIQUE,
    precio DECIMAL(10,2) NOT NULL DEFAULT 0,
    periodo_meses INT NOT NULL DEFAULT 1,  -- cada cuántos meses se cobra `precio`
    max_salones INT,               -- NULL = ilimitado
    max_reservaciones_mes INT      -- NULL = ilimitado
);

-- Primer mes gratis (anzuelo para atraer clientes) y luego un único
-- plan de paga: $250 cada 6 meses, salones ilimitados.
INSERT INTO planes (nombre, slug, precio, periodo_meses, max_salones, max_reservaciones_mes) VALUES
    ('Primer Mes Gratis', 'gratis', 0.00,   1, 1,    NULL),
    ('Pro',                'pro',    250.00, 6, NULL, NULL);

-- Suscripción de cada dueño (columnas agregadas por ALTER porque
-- `planes` se crea después de `usuarios` en este script).
ALTER TABLE usuarios
    ADD COLUMN plan_id INT,
    ADD COLUMN suscripcion_estado ENUM('trial','activa','vencida') DEFAULT 'trial',
    ADD COLUMN trial_termina DATE,
    ADD COLUMN stripe_customer_id VARCHAR(64),
    ADD COLUMN stripe_subscription_id VARCHAR(64),
    ADD UNIQUE KEY unique_stripe_sub (stripe_subscription_id),
    ADD FOREIGN KEY (plan_id) REFERENCES planes(id);