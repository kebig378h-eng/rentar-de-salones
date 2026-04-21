CREATE DATABASE eventos_db;
USE eventos_db;

-- Tabla usuarios
CREATE TABLE usuarios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(100),
    correo VARCHAR(100),
    password VARCHAR(255),
    rol VARCHAR(20)
);

-- Tabla clientes
CREATE TABLE clientes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(100),
    telefono VARCHAR(20),
    correo VARCHAR(100)
);

-- Tabla salones
CREATE TABLE salon (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(100),
    precio DECIMAL(10,2),
    latitud DECIMAL(10,6),
    longitud DECIMAL(10,6),
    imagen VARCHAR(255),
    capacidad INT,
    descripcion TEXT,
    dueno_id INT,
    FOREIGN KEY (dueno_id) REFERENCES usuarios(id)
);

-- Tabla reservaciones
CREATE TABLE reservaciones (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cliente VARCHAR(100),
    fecha DATE,
    tipo VARCHAR(100),
    salon_id INT,
    visto TINYINT(1),
    FOREIGN KEY (salon_id) REFERENCES salon(id)
);

-- Tabla pagos
CREATE TABLE pagos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    reservacion_id INT,
    monto DECIMAL(10,2),
    tipo_pago VARCHAR(50),
    fecha_pago DATE,
    FOREIGN KEY (reservacion_id) REFERENCES reservaciones(id)
);

-- Tabla contratos
CREATE TABLE contratos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    reservacion_id INT,
    fecha_contrato DATE,
    condiciones TEXT,
    FOREIGN KEY (reservacion_id) REFERENCES reservaciones(id)
);

-- Tabla mensajes
CREATE TABLE mensajes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    emisor_id INT,
    receptor_id INT,
    salon_id INT,
    mensaje TEXT,
    fecha DATETIME,
    FOREIGN KEY (emisor_id) REFERENCES usuarios(id),
    FOREIGN KEY (receptor_id) REFERENCES usuarios(id),
    FOREIGN KEY (salon_id) REFERENCES salon(id)
);

-- Tabla reseñas
CREATE TABLE resenas (
    id INT AUTO_INCREMENT PRIMARY KEY,
    salon_id INT,
    usuario_id INT,
    calificacion TINYINT(1),
    comentario TEXT,
    fecha DATETIME,
    FOREIGN KEY (salon_id) REFERENCES salon(id),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
);