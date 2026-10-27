from flask import (Flask, render_template, request, redirect,
                   session, flash, abort, send_file, jsonify)
from flask_socketio import SocketIO, emit, join_room
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from dotenv import load_dotenv
from datetime import date, timedelta
import mysql.connector
import stripe
import uuid
import os
import secrets
import hashlib

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
socketio = SocketIO(app, cors_allowed_origins="*")

# ── CORREO ───────────────────────────────────────────────────────────────────
app.config["MAIL_SERVER"]         = os.getenv("MAIL_SERVER",  "smtp.gmail.com")
app.config["MAIL_PORT"]           = int(os.getenv("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"]        = os.getenv("MAIL_USE_TLS", "True") == "True"
app.config["MAIL_USERNAME"]       = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"]       = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")
mail = Mail(app)

# ── STRIPE ───────────────────────────────────────────────────────────────────
# Sin STRIPE_SECRET_KEY configurada, las rutas de checkout avisan que el
# cobro todavía no está activado en vez de fallar.
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PRECIOS = {
    "pro": os.getenv("STRIPE_PRICE_ID_PRO"),  # $250 cada 6 meses
}

EXTENSIONES_PERMITIDAS = {"jpg", "jpeg", "png", "webp"}
TAMANO_MAXIMO_MB       = 5


# ── BD ───────────────────────────────────────────────────────────────────────

def get_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME")
    )

def get_cursor():
    db = get_db()
    return db, db.cursor(dictionary=True, buffered=True)


# ── HELPERS ───────────────────────────────────────────────────────────────────

def extension_permitida(filename):
    return ("." in filename and
            filename.rsplit(".", 1)[1].lower() in EXTENSIONES_PERMITIDAS)

def guardar_imagen(archivo):
    if not archivo or not archivo.filename:
        return None
    if not extension_permitida(archivo.filename):
        flash("Solo se permiten imágenes JPG, PNG o WEBP.", "error")
        return None
    archivo.seek(0, 2)
    if archivo.tell() / (1024 * 1024) > TAMANO_MAXIMO_MB:
        flash(f"La imagen no debe superar {TAMANO_MAXIMO_MB}MB.", "error")
        return None
    archivo.seek(0)
    ext    = archivo.filename.rsplit(".", 1)[1].lower()
    nombre = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(os.path.join("static", "uploads"), exist_ok=True)
    archivo.save(os.path.join("static", "uploads", nombre))
    return nombre

def _plan_actual(cursor, user_id):
    """Plan + estado de suscripción del dueño dado, o None si no tiene plan."""
    cursor.execute("""
        SELECT p.*, u.suscripcion_estado, u.trial_termina
        FROM usuarios u JOIN planes p ON u.plan_id = p.id
        WHERE u.id=%s
    """, (user_id,))
    return cursor.fetchone()

def _suscripcion_vigente(cursor, db, user_id):
    """True si el dueño puede usar la app. El plan gratis solo dura hasta
    `trial_termina`; al pasarse se marca la suscripción como 'vencida'."""
    plan = _plan_actual(cursor, user_id)
    if not plan or plan["suscripcion_estado"] == "vencida":
        return False
    if plan["slug"] == "gratis":
        if plan["trial_termina"] and plan["trial_termina"] >= date.today():
            return True
        cursor.execute("UPDATE usuarios SET suscripcion_estado='vencida' WHERE id=%s", (user_id,))
        db.commit()
        return False
    return True

def enviar_correo_reservacion(correo_cliente, nombre_cliente,
                               salon_nombre, fecha, tipo):
    try:
        msg = Message(
            subject="✅ Confirmación de Reservación — EventoSuite",
            recipients=[correo_cliente]
        )
        msg.html = f"""
        <div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:24px">
            <h2 style="color:#1abc9c">🎪 EventoSuite</h2>
            <h3>¡Hola, {nombre_cliente}!</h3>
            <p>Tu reservación ha sido confirmada:</p>
            <table style="width:100%;border-collapse:collapse;margin:16px 0">
                <tr style="background:#f4f6f9">
                    <td style="padding:10px;font-weight:600">Salón</td>
                    <td style="padding:10px">{salon_nombre}</td>
                </tr>
                <tr>
                    <td style="padding:10px;font-weight:600">Fecha</td>
                    <td style="padding:10px">{fecha}</td>
                </tr>
                <tr style="background:#f4f6f9">
                    <td style="padding:10px;font-weight:600">Tipo de evento</td>
                    <td style="padding:10px">{tipo}</td>
                </tr>
            </table>
            <p style="color:#aaa;font-size:12px">EventoSuite — Sistema de Gestión de Eventos</p>
        </div>
        """
        mail.send(msg)
    except Exception as e:
        app.logger.warning(f"Correo no enviado: {e}")

def enviar_correo_recuperacion(correo, nombre, link):
    try:
        msg = Message(
            subject="🔑 Recupera tu contraseña — EventoSuite",
            recipients=[correo]
        )
        msg.html = f"""
        <div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:24px">
            <h2 style="color:#1abc9c">🎪 EventoSuite</h2>
            <h3>¡Hola, {nombre}!</h3>
            <p>Recibimos una solicitud para restablecer tu contraseña. Este enlace es válido por 1 hora:</p>
            <p style="margin:20px 0"><a href="{link}" style="background:#1abc9c;color:white;padding:12px 24px;border-radius:6px;text-decoration:none">Restablecer contraseña</a></p>
            <p style="color:#aaa;font-size:12px">Si tú no pediste esto, ignora este correo — tu contraseña sigue siendo la misma.</p>
        </div>
        """
        mail.send(msg)
    except Exception as e:
        app.logger.warning(f"Correo no enviado: {e}")


# ── DECORADORES ───────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorador(*args, **kwargs):
        if "rol" not in session:
            flash("Debes iniciar sesión para continuar.", "error")
            return redirect("/login")
        return f(*args, **kwargs)
    return decorador

def roles_permitidos(*roles):
    def wrapper(f):
        @wraps(f)
        def decorador(*args, **kwargs):
            if "rol" not in session:
                return redirect("/login")
            if session["rol"] not in roles:
                abort(403)
            return f(*args, **kwargs)
        return decorador
    return wrapper

def api_login_required(f):
    """Protege endpoints de la API verificando sesión o token."""
    @wraps(f)
    def decorador(*args, **kwargs):
        token = request.headers.get("X-API-Token")
        api_token = os.getenv("API_TOKEN", "")
        if token and api_token and token == api_token:
            return f(*args, **kwargs)
        if "rol" in session:
            return f(*args, **kwargs)
        return jsonify({"error": "No autorizado"}), 401
    return decorador


# Lo único que un dueño con suscripción vencida puede abrir: lo necesario
# para ver su plan y pagar.
ENDPOINTS_SIN_SUSCRIPCION = {
    "static", "landing", "login", "logout", "registro", "terminos", "privacidad",
    "recuperar", "recuperar_confirmar", "precios", "mi_plan",
    "suscripcion_checkout", "suscripcion_exito", "webhook_stripe",
}

@app.before_request
def verificar_suscripcion():
    if session.get("rol") != "dueno" or request.endpoint in ENDPOINTS_SIN_SUSCRIPCION:
        return
    db, cursor = get_cursor()
    vigente = _suscripcion_vigente(cursor, db, session["user_id"])
    cursor.close(); db.close()
    if not vigente:
        flash("Tu suscripción terminó. Renueva tu plan para seguir usando EventoSuite.", "error")
        return redirect("/mi_plan")


@app.context_processor
def inject_anio_actual():
    return {"anio_actual": date.today().year}


# ── PÁGINAS DE ERROR ──────────────────────────────────────────────────────────

@app.errorhandler(403)
def error_403(e):
    return render_template("403.html"), 403

@app.errorhandler(404)
def error_404(e):
    return render_template("404.html"), 404


# ── LEGAL ─────────────────────────────────────────────────────────────────────

@app.route("/terminos")
def terminos():
    return render_template("terminos.html")

@app.route("/privacidad")
def privacidad():
    return render_template("privacidad.html")


# ── AUTH ─────────────────────────────────────────────────────────────────────

@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        db, cursor = get_cursor()
        try:
            password_hash = generate_password_hash(request.form["password"])
            rol = request.form["rol"]
            plan_id, estado, trial_fin = None, None, None
            if rol == "dueno":
                cursor.execute("SELECT id FROM planes WHERE slug='gratis'")
                plan_gratis = cursor.fetchone()
                plan_id   = plan_gratis["id"] if plan_gratis else None
                estado    = "trial"
                trial_fin = date.today() + timedelta(days=30)  # primer mes gratis
            cursor.execute("""
                INSERT INTO usuarios (nombre, correo, password, rol, plan_id, suscripcion_estado, trial_termina)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (request.form["nombre"].strip(),
                  request.form["correo"].strip().lower(),
                  password_hash, rol, plan_id, estado, trial_fin))
            db.commit()
            flash("Cuenta creada. Inicia sesión.", "success")
            return redirect("/login")
        except mysql.connector.IntegrityError:
            flash("Ese correo ya está registrado.", "error")
        finally:
            cursor.close(); db.close()
    return render_template("registro.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "rol" in session:
        return _redirigir_por_rol(session["rol"])
    if request.method == "POST":
        correo   = request.form["correo"].strip().lower()
        password = request.form["password"]
        db, cursor = get_cursor()
        cursor.execute("SELECT * FROM usuarios WHERE correo=%s", (correo,))
        user = cursor.fetchone()
        cursor.close(); db.close()
        if user and check_password_hash(user["password"], password):
            session["usuario"] = user["nombre"]
            session["rol"]     = user["rol"]
            session["user_id"] = user["id"]
            session["correo"]  = user["correo"]
            flash(f"Bienvenido, {user['nombre']}.", "success")
            return _redirigir_por_rol(user["rol"])
        flash("Correo o contraseña incorrectos.", "error")
    return render_template("login.html")

def _redirigir_por_rol(rol):
    if rol == "admin": return redirect("/panel")
    if rol == "dueno": return redirect("/inicio")
    return redirect("/mapa")

@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada.", "success")
    return redirect("/login")


@app.route("/recuperar", methods=["GET", "POST"])
def recuperar():
    if request.method == "POST":
        correo = request.form.get("correo", "").strip().lower()
        db, cursor = get_cursor()
        cursor.execute("SELECT id, nombre FROM usuarios WHERE correo=%s", (correo,))
        user = cursor.fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            cursor.execute("UPDATE usuarios SET reset_token=%s, reset_expira=NOW() + INTERVAL 1 HOUR WHERE id=%s",
                           (token_hash, user["id"]))
            db.commit()
            link = request.url_root.rstrip("/") + f"/recuperar/{token}"
            enviar_correo_recuperacion(correo, user["nombre"], link)
        cursor.close(); db.close()
        # Mismo mensaje exista o no la cuenta — no revela qué correos están registrados.
        flash("Si ese correo está registrado, te enviamos instrucciones para recuperar tu contraseña.", "success")
        return redirect("/login")
    return render_template("recuperar.html")


@app.route("/recuperar/<token>", methods=["GET", "POST"])
def recuperar_confirmar(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db, cursor = get_cursor()
    cursor.execute("SELECT id FROM usuarios WHERE reset_token=%s AND reset_expira > NOW()", (token_hash,))
    user = cursor.fetchone()
    if not user:
        cursor.close(); db.close()
        flash("Ese enlace ya no es válido — pide uno nuevo.", "error")
        return redirect("/recuperar")

    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < 6:
            cursor.close(); db.close()
            flash("La contraseña debe tener al menos 6 caracteres.", "error")
            return redirect(f"/recuperar/{token}")
        cursor.execute("UPDATE usuarios SET password=%s, reset_token=NULL, reset_expira=NULL WHERE id=%s",
                       (generate_password_hash(password), user["id"]))
        db.commit(); cursor.close(); db.close()
        flash("Contraseña actualizada. Ya puedes iniciar sesión.", "success")
        return redirect("/login")

    cursor.close(); db.close()
    return render_template("recuperar_confirmar.html", token=token)


# ── LANDING ───────────────────────────────────────────────────────────────────

@app.route("/")
def landing():
    if "rol" in session:
        return _redirigir_por_rol(session["rol"])
    return render_template("landing.html")


# ── DASHBOARD ─────────────────────────────────────────────────────────────────

@app.route("/panel")
@roles_permitidos("admin")
def index():
    db, cursor = get_cursor()
    cursor.execute("SELECT COUNT(*) as total FROM clientes")
    total_clientes = cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) as total FROM reservaciones")
    total_reservaciones = cursor.fetchone()["total"]
    cursor.execute("SELECT IFNULL(SUM(monto),0) as total FROM pagos")
    ingresos = cursor.fetchone()["total"]
    cursor.execute("SELECT fecha FROM reservaciones WHERE fecha >= CURDATE() ORDER BY fecha ASC LIMIT 1")
    proximo = cursor.fetchone()
    proximo_evento = str(proximo["fecha"]) if proximo else "Sin eventos"
    cursor.execute("SELECT MONTH(fecha_pago) as mes, SUM(monto) as total FROM pagos GROUP BY MONTH(fecha_pago) ORDER BY mes")
    datos = cursor.fetchall()
    meses  = [str(d["mes"]) for d in datos]
    montos = [float(d["total"]) for d in datos]
    cursor.execute("SELECT MONTH(fecha) as mes, COUNT(*) as total FROM reservaciones GROUP BY MONTH(fecha) ORDER BY mes")
    datos_res   = cursor.fetchall()
    meses_res   = [str(d["mes"]) for d in datos_res]
    totales_res = [int(d["total"]) for d in datos_res]
    cursor.close(); db.close()
    return render_template("index.html",
        total_clientes=total_clientes, total_reservaciones=total_reservaciones,
        ingresos=ingresos, proximo_evento=proximo_evento,
        meses=meses, montos=montos, meses_res=meses_res, totales_res=totales_res)


@app.route("/inicio")
@roles_permitidos("dueno")
def inicio_dueno():
    db, cursor = get_cursor()
    uid = session["user_id"]
    cursor.execute("SELECT COUNT(*) as total FROM clientes WHERE dueno_id=%s", (uid,))
    total_clientes = cursor.fetchone()["total"]
    cursor.execute("""
        SELECT COUNT(*) as total FROM reservaciones r JOIN salon s ON r.salon_id=s.id
        WHERE s.dueno_id=%s AND MONTH(r.fecha)=MONTH(CURDATE()) AND YEAR(r.fecha)=YEAR(CURDATE())
    """, (uid,))
    reservaciones_mes = cursor.fetchone()["total"]
    cursor.execute("""
        SELECT IFNULL(SUM(p.monto),0) as total FROM pagos p
        JOIN reservaciones r ON p.reservacion_id=r.id JOIN salon s ON r.salon_id=s.id
        WHERE s.dueno_id=%s
    """, (uid,))
    ingresos = cursor.fetchone()["total"]
    cursor.execute("""
        SELECT r.fecha FROM reservaciones r JOIN salon s ON r.salon_id=s.id
        WHERE s.dueno_id=%s AND r.fecha >= CURDATE() ORDER BY r.fecha ASC LIMIT 1
    """, (uid,))
    proximo = cursor.fetchone()
    proximo_evento = str(proximo["fecha"]) if proximo else "Sin eventos"
    cursor.execute("""
        SELECT MONTH(p.fecha_pago) as mes, SUM(p.monto) as total FROM pagos p
        JOIN reservaciones r ON p.reservacion_id=r.id JOIN salon s ON r.salon_id=s.id
        WHERE s.dueno_id=%s GROUP BY MONTH(p.fecha_pago) ORDER BY mes
    """, (uid,))
    datos = cursor.fetchall()
    meses  = [str(d["mes"]) for d in datos]
    montos = [float(d["total"]) for d in datos]
    cursor.execute("""
        SELECT MONTH(r.fecha) as mes, COUNT(*) as total FROM reservaciones r
        JOIN salon s ON r.salon_id=s.id WHERE s.dueno_id=%s
        GROUP BY MONTH(r.fecha) ORDER BY mes
    """, (uid,))
    datos_res   = cursor.fetchall()
    meses_res   = [str(d["mes"]) for d in datos_res]
    totales_res = [int(d["total"]) for d in datos_res]
    cursor.close(); db.close()
    return render_template("inicio_dueno.html",
        total_clientes=total_clientes, reservaciones_mes=reservaciones_mes,
        ingresos=ingresos, proximo_evento=proximo_evento,
        meses=meses, montos=montos, meses_res=meses_res, totales_res=totales_res)


# ── CLIENTES ─────────────────────────────────────────────────────────────────

@app.route("/clientes", methods=["GET", "POST"])
@roles_permitidos("admin", "dueno")
def clientes():
    db, cursor = get_cursor()
    es_dueno = session["rol"] == "dueno"
    if request.method == "POST":
        cursor.execute("INSERT INTO clientes (nombre, telefono, correo, dueno_id) VALUES (%s,%s,%s,%s)",
                       (request.form["nombre"], request.form["telefono"], request.form["correo"],
                        session["user_id"] if es_dueno else None))
        db.commit()
        flash("Cliente registrado.", "success")
        return redirect("/clientes")
    if es_dueno:
        cursor.execute("SELECT * FROM clientes WHERE dueno_id=%s ORDER BY nombre", (session["user_id"],))
    else:
        cursor.execute("SELECT * FROM clientes ORDER BY nombre")
    lista = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("clientes.html", clientes=lista)

@app.route("/clientes/eliminar/<int:cliente_id>", methods=["POST"])
@roles_permitidos("admin", "dueno")
def eliminar_cliente(cliente_id):
    db, cursor = get_cursor()
    if session["rol"] == "dueno":
        cursor.execute("SELECT id FROM clientes WHERE id=%s AND dueno_id=%s", (cliente_id, session["user_id"]))
        if not cursor.fetchone(): cursor.close(); db.close(); abort(403)
    cursor.execute("DELETE FROM clientes WHERE id=%s", (cliente_id,))
    db.commit(); cursor.close(); db.close()
    flash("Cliente eliminado.", "success")
    return redirect("/clientes")

@app.route("/clientes/editar/<int:cliente_id>", methods=["GET", "POST"])
@roles_permitidos("admin", "dueno")
def editar_cliente(cliente_id):
    db, cursor = get_cursor()
    if session["rol"] == "dueno":
        cursor.execute("SELECT id FROM clientes WHERE id=%s AND dueno_id=%s", (cliente_id, session["user_id"]))
        if not cursor.fetchone(): cursor.close(); db.close(); abort(403)
    if request.method == "POST":
        cursor.execute("UPDATE clientes SET nombre=%s, telefono=%s, correo=%s WHERE id=%s",
                       (request.form["nombre"], request.form["telefono"],
                        request.form["correo"], cliente_id))
        db.commit(); cursor.close(); db.close()
        flash("Cliente actualizado.", "success")
        return redirect("/clientes")
    cursor.execute("SELECT * FROM clientes WHERE id=%s", (cliente_id,))
    cliente = cursor.fetchone()
    cursor.close(); db.close()
    if not cliente: abort(404)
    return render_template("editar_cliente.html", cliente=cliente)


# ── RESERVACIONES ─────────────────────────────────────────────────────────────

@app.route("/reservaciones", methods=["GET", "POST"])
@roles_permitidos("admin", "dueno")
def reservaciones():
    db, cursor = get_cursor()
    if request.method == "POST":
        if session["rol"] == "dueno":
            cursor.execute("SELECT id FROM salon WHERE id=%s AND dueno_id=%s",
                           (request.form["salon"], session["user_id"]))
            if not cursor.fetchone():
                cursor.close(); db.close(); abort(403)
        cursor.execute("SELECT id FROM reservaciones WHERE fecha=%s AND salon_id=%s",
                       (request.form["fecha"], request.form["salon"]))
        if cursor.fetchone():
            flash("Ese salón ya está reservado en esa fecha.", "error")
        else:
            try:
                cursor.execute("INSERT INTO reservaciones (cliente, fecha, tipo, salon_id) VALUES (%s,%s,%s,%s)",
                               (request.form["cliente"], request.form["fecha"],
                                request.form["tipo"],    request.form["salon"]))
                db.commit()
            except mysql.connector.IntegrityError:
                # Alguien más reservó esa fecha entre el SELECT y el INSERT;
                # el UNIQUE (salon_id, fecha) de la base de datos lo detiene.
                cursor.close(); db.close()
                flash("Ese salón ya está reservado en esa fecha.", "error")
                return redirect("/reservaciones")
            flash("Reservación creada.", "success")
            if session["rol"] == "dueno":
                cursor.execute("SELECT correo FROM clientes WHERE nombre=%s AND dueno_id=%s LIMIT 1",
                               (request.form["cliente"], session["user_id"]))
            else:
                cursor.execute("SELECT correo FROM clientes WHERE nombre=%s LIMIT 1", (request.form["cliente"],))
            c = cursor.fetchone()
            if c and c["correo"]:
                cursor.execute("SELECT nombre FROM salon WHERE id=%s", (request.form["salon"],))
                s = cursor.fetchone()
                enviar_correo_reservacion(c["correo"], request.form["cliente"],
                                          s["nombre"] if s else "Salón",
                                          request.form["fecha"], request.form["tipo"])
        cursor.close(); db.close()
        return redirect("/reservaciones")

    if session["rol"] == "dueno":
        cursor.execute("SELECT r.* FROM reservaciones r JOIN salon s ON r.salon_id=s.id WHERE s.dueno_id=%s ORDER BY r.fecha DESC", (session["user_id"],))
    else:
        cursor.execute("SELECT * FROM reservaciones ORDER BY fecha DESC")
    reservaciones = cursor.fetchall()
    if session["rol"] == "dueno":
        cursor.execute("SELECT * FROM clientes WHERE dueno_id=%s ORDER BY nombre", (session["user_id"],))
    else:
        cursor.execute("SELECT * FROM clientes ORDER BY nombre")
    clientes = cursor.fetchall()
    if session["rol"] == "dueno":
        cursor.execute("SELECT * FROM salon WHERE dueno_id=%s", (session["user_id"],))
    else:
        cursor.execute("SELECT * FROM salon")
    salones = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("reservaciones.html",
        reservaciones=reservaciones, clientes=clientes, salones=salones,
        today=date.today().isoformat())

@app.route("/api/fechas_ocupadas/<int:salon_id>")
@roles_permitidos("admin", "dueno")
def api_fechas_ocupadas(salon_id):
    db, cursor = get_cursor()
    cursor.execute("SELECT fecha FROM reservaciones WHERE salon_id=%s", (salon_id,))
    fechas = [str(r["fecha"]) for r in cursor.fetchall()]
    cursor.close(); db.close()
    return jsonify(fechas)

@app.route("/reservaciones/eliminar/<int:res_id>", methods=["POST"])
@roles_permitidos("admin", "dueno")
def eliminar_reservacion(res_id):
    db, cursor = get_cursor()
    if session["rol"] == "dueno" and not _reservacion_es_del_dueno(cursor, res_id, session["user_id"]):
        cursor.close(); db.close(); abort(403)
    cursor.execute("DELETE FROM reservaciones WHERE id=%s", (res_id,))
    db.commit(); cursor.close(); db.close()
    flash("Reservación eliminada.", "success")
    return redirect("/reservaciones")


# ── PAGOS ─────────────────────────────────────────────────────────────────────

@app.route("/pagos", methods=["GET", "POST"])
@roles_permitidos("admin", "dueno")
def pagos():
    db, cursor = get_cursor()
    es_dueno = session["rol"] == "dueno"
    if request.method == "POST":
        if es_dueno and not _reservacion_es_del_dueno(cursor, request.form["reservacion"], session["user_id"]):
            cursor.close(); db.close(); abort(403)
        cursor.execute("INSERT INTO pagos (reservacion_id, monto, tipo_pago, fecha_pago) VALUES (%s,%s,%s,%s)",
                       (request.form["reservacion"], request.form["monto"],
                        request.form["tipo"],        request.form["fecha"]))
        db.commit()
        flash("Pago registrado.", "success")
        return redirect("/pagos")
    if es_dueno:
        cursor.execute("""
            SELECT p.*, r.cliente, r.fecha as fecha_reservacion FROM pagos p
            JOIN reservaciones r ON p.reservacion_id=r.id
            JOIN salon s ON r.salon_id=s.id
            WHERE s.dueno_id=%s ORDER BY p.fecha_pago DESC
        """, (session["user_id"],))
    else:
        cursor.execute("SELECT p.*, r.cliente, r.fecha as fecha_reservacion FROM pagos p JOIN reservaciones r ON p.reservacion_id=r.id ORDER BY p.fecha_pago DESC")
    pagos = cursor.fetchall()
    if es_dueno:
        cursor.execute("SELECT r.* FROM reservaciones r JOIN salon s ON r.salon_id=s.id WHERE s.dueno_id=%s ORDER BY r.fecha DESC", (session["user_id"],))
    else:
        cursor.execute("SELECT * FROM reservaciones ORDER BY fecha DESC")
    reservaciones = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("pagos.html", pagos=pagos, reservaciones=reservaciones)


# ── CONTRATOS ─────────────────────────────────────────────────────────────────

def _reservacion_es_del_dueno(cursor, reservacion_id, user_id):
    """True si la reservación pertenece a un salón del dueño dado."""
    cursor.execute("""
        SELECT s.dueno_id FROM reservaciones r JOIN salon s ON r.salon_id=s.id
        WHERE r.id=%s
    """, (reservacion_id,))
    row = cursor.fetchone()
    return bool(row) and row["dueno_id"] == user_id

@app.route("/contratos", methods=["GET", "POST"])
@roles_permitidos("admin", "dueno")
def contratos():
    db, cursor = get_cursor()
    es_dueno = session["rol"] == "dueno"
    if request.method == "POST":
        if es_dueno and not _reservacion_es_del_dueno(cursor, request.form["reservacion"], session["user_id"]):
            cursor.close(); db.close(); abort(403)
        cursor.execute("SELECT id FROM contratos WHERE reservacion_id=%s", (request.form["reservacion"],))
        if cursor.fetchone():
            flash("Ya existe un contrato para esa reservación.", "error")
        else:
            cursor.execute("INSERT INTO contratos (reservacion_id, fecha_contrato, condiciones) VALUES (%s,%s,%s)",
                           (request.form["reservacion"], request.form["fecha"], request.form["condiciones"]))
            db.commit()
            flash("Contrato generado.", "success")
        return redirect("/contratos")
    if es_dueno:
        cursor.execute("""
            SELECT c.*, r.cliente, r.fecha as fecha_reservacion, r.tipo
            FROM contratos c JOIN reservaciones r ON c.reservacion_id=r.id
            JOIN salon s ON r.salon_id=s.id
            WHERE s.dueno_id=%s ORDER BY c.fecha_contrato DESC
        """, (session["user_id"],))
    else:
        cursor.execute("SELECT c.*, r.cliente, r.fecha as fecha_reservacion, r.tipo FROM contratos c JOIN reservaciones r ON c.reservacion_id=r.id ORDER BY c.fecha_contrato DESC")
    contratos = cursor.fetchall()
    if es_dueno:
        cursor.execute("SELECT r.* FROM reservaciones r JOIN salon s ON r.salon_id=s.id WHERE s.dueno_id=%s ORDER BY r.fecha DESC", (session["user_id"],))
    else:
        cursor.execute("SELECT * FROM reservaciones ORDER BY fecha DESC")
    reservaciones = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("contratos.html", contratos=contratos, reservaciones=reservaciones)

@app.route("/contratos/generar/<int:contrato_id>")
@roles_permitidos("admin", "dueno")
def generar_contrato_pdf(contrato_id):
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    import io
    db, cursor = get_cursor()
    cursor.execute("""
        SELECT c.*, r.cliente, r.fecha as fecha_evento, r.tipo,
               s.nombre as salon_nombre, s.precio as salon_precio,
               s.dueno_id as salon_dueno_id, u.nombre as dueno_nombre
        FROM contratos c JOIN reservaciones r ON c.reservacion_id=r.id
        JOIN salon s ON r.salon_id=s.id LEFT JOIN usuarios u ON s.dueno_id=u.id
        WHERE c.id=%s
    """, (contrato_id,))
    ct = cursor.fetchone()
    cursor.close(); db.close()
    if not ct: abort(404)
    if session["rol"] == "dueno" and ct["salon_dueno_id"] != session["user_id"]:
        abort(403)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    T  = ParagraphStyle("t",  fontSize=14, fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=6)
    S  = ParagraphStyle("s",  fontSize=11, fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=4, textColor=colors.HexColor("#2c3e50"))
    SC = ParagraphStyle("sc", fontSize=10, fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#2c3e50"))
    N  = ParagraphStyle("n",  fontSize=9,  fontName="Helvetica", alignment=TA_JUSTIFY, spaceAfter=4, leading=14)
    C  = ParagraphStyle("c",  fontSize=9,  fontName="Helvetica", spaceAfter=6, leading=14)
    F  = ParagraphStyle("f",  fontSize=9,  fontName="Helvetica", alignment=TA_CENTER, spaceBefore=20)
    def copia(para):
        e = []
        e.append(Paragraph("CONTRATO DE ARRENDAMIENTO DE SALÓN PARA EVENTOS", T))
        e.append(Paragraph(f"— COPIA PARA EL {'CLIENTE' if para=='cliente' else 'ARRENDADOR'} —", S))
        e.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1abc9c")))
        e.append(Spacer(1, 10))
        e.append(Paragraph("I. PARTES", SC))
        e.append(Paragraph(f"<b>ARRENDADOR:</b> {ct['dueno_nombre'] or 'Propietario'}", C))
        e.append(Paragraph(f"<b>ARRENDATARIO:</b> {ct['cliente']}", C))
        e.append(Paragraph("II. OBJETO", SC))
        e.append(Paragraph(f"Uso temporal del salón <b>{ct['salon_nombre']}</b> para evento de tipo <b>{ct['tipo']}</b>.", N))
        e.append(Paragraph("III. FECHA Y DURACIÓN", SC))
        e.append(Paragraph(f"<b>Fecha del evento:</b> {ct['fecha_evento']}", C))
        e.append(Paragraph("<b>Hora de inicio:</b> ______________________", C))
        e.append(Paragraph("<b>Hora de término:</b> ______________________", C))
        e.append(Paragraph("IV. MONTO Y FORMA DE PAGO", SC))
        e.append(Paragraph(f"<b>Precio total:</b> ${ct['salon_precio']}", C))
        e.append(Paragraph("<b>Anticipo:</b> $______________________", C))
        e.append(Paragraph("<b>Saldo restante:</b> $______________________", C))
        e.append(Paragraph("V. RESPONSABILIDADES DEL CLIENTE", SC))
        for i, r in enumerate(["Hacer uso adecuado de las instalaciones.", "Respetar horarios.", "Cubrir cualquier daño.", "Cumplir normas de seguridad.", "No exceder la capacidad."], 1):
            e.append(Paragraph(f"{i}. {r}", C))
        e.append(Paragraph("VI. RESPONSABILIDADES DEL ARRENDADOR", SC))
        for i, r in enumerate(["Entregar el salón en condiciones óptimas.", "Garantizar el uso en la fecha acordada.", "Proporcionar los servicios incluidos."], 1):
            e.append(Paragraph(f"{i}. {r}", C))
        e.append(Paragraph("VII. CANCELACIONES", SC))
        e.append(Paragraph("• Cliente: el anticipo puede ser no reembolsable.", C))
        e.append(Paragraph("• Arrendador: se reembolsa el total pagado.", C))
        e.append(Paragraph("VIII. ACEPTACIÓN", SC))
        e.append(Paragraph("Al realizar la reservación ambas partes aceptan los términos del presente contrato.", N))
        if ct.get("condiciones"):
            e.append(Paragraph("IX. CONDICIONES ADICIONALES", SC))
            e.append(Paragraph(ct["condiciones"], N))
        e.append(Spacer(1, 20))
        e.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#ccc")))
        e.append(Spacer(1, 10))
        e.append(Paragraph("Firma del cliente: ______________________      Firma del arrendador: ______________________", F))
        e.append(Spacer(1, 16))
        e.append(Paragraph(f"Fecha de firma: ______________________      Contrato #: {ct['id']}", F))
        return e
    doc.build(copia("cliente") + [PageBreak()] + copia("arrendador"))
    buffer.seek(0)
    nombre = f"contrato_{contrato_id}_{ct['cliente'].replace(' ','_')}.pdf"
    return send_file(buffer, as_attachment=True, download_name=nombre, mimetype="application/pdf")


# ── REPORTE PDF ───────────────────────────────────────────────────────────────

@app.route("/reportes/ingresos")
@roles_permitidos("admin", "dueno")
def reporte_ingresos():
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from datetime import datetime
    import io
    db, cursor = get_cursor()
    es_dueno = session["rol"] == "dueno"
    if es_dueno:
        cursor.execute("""
            SELECT MONTHNAME(p.fecha_pago) as mes, SUM(p.monto) as total, COUNT(*) as cantidad
            FROM pagos p JOIN reservaciones r ON p.reservacion_id=r.id JOIN salon s ON r.salon_id=s.id
            WHERE s.dueno_id=%s GROUP BY MONTH(p.fecha_pago) ORDER BY MONTH(p.fecha_pago)
        """, (session["user_id"],))
    else:
        cursor.execute("SELECT MONTHNAME(fecha_pago) as mes, SUM(monto) as total, COUNT(*) as cantidad FROM pagos GROUP BY MONTH(fecha_pago) ORDER BY MONTH(fecha_pago)")
    por_mes = cursor.fetchall()
    if es_dueno:
        cursor.execute("""
            SELECT s.nombre, SUM(p.monto) as total, COUNT(p.id) as pagos FROM pagos p
            JOIN reservaciones r ON p.reservacion_id=r.id JOIN salon s ON r.salon_id=s.id
            WHERE s.dueno_id=%s GROUP BY s.id ORDER BY total DESC
        """, (session["user_id"],))
    else:
        cursor.execute("SELECT s.nombre, SUM(p.monto) as total, COUNT(p.id) as pagos FROM pagos p JOIN reservaciones r ON p.reservacion_id=r.id JOIN salon s ON r.salon_id=s.id GROUP BY s.id ORDER BY total DESC")
    por_salon = cursor.fetchall()
    if es_dueno:
        cursor.execute("""
            SELECT IFNULL(SUM(p.monto),0) as total, COUNT(*) as cantidad FROM pagos p
            JOIN reservaciones r ON p.reservacion_id=r.id JOIN salon s ON r.salon_id=s.id
            WHERE s.dueno_id=%s
        """, (session["user_id"],))
    else:
        cursor.execute("SELECT IFNULL(SUM(monto),0) as total, COUNT(*) as cantidad FROM pagos")
    totales = cursor.fetchone()
    cursor.close(); db.close()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    VERDE = colors.HexColor("#1abc9c"); OSCURO = colors.HexColor("#2c3e50"); GRIS = colors.HexColor("#f4f6f9")
    TT = ParagraphStyle("tt", fontSize=18, fontName="Helvetica-Bold", alignment=TA_CENTER, spaceAfter=4, textColor=OSCURO)
    TS = ParagraphStyle("ts", fontSize=11, fontName="Helvetica", alignment=TA_CENTER, spaceAfter=16, textColor=colors.HexColor("#7f8c8d"))
    SC = ParagraphStyle("sc", fontSize=12, fontName="Helvetica-Bold", spaceBefore=18, spaceAfter=8, textColor=OSCURO)
    h = []
    h.append(Paragraph("Reporte de Ingresos", TT))
    h.append(Paragraph(f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}", TS))
    h.append(HRFlowable(width="100%", thickness=2, color=VERDE)); h.append(Spacer(1, 12))
    h.append(Paragraph("Resumen General", SC))
    t = Table([["Métrica","Valor"],["Total de pagos",str(totales["cantidad"])],["Ingresos totales",f"${float(totales['total']):,.2f}"],["Promedio por pago",f"${float(totales['total'])/max(totales['cantidad'],1):,.2f}"]], colWidths=[10*cm,6*cm])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),OSCURO),("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),10),("ROWBACKGROUNDS",(0,1),(-1,-1),[GRIS,colors.white]),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#ddd")),("PADDING",(0,0),(-1,-1),8)]))
    h.append(t)
    h.append(Paragraph("Ingresos por Mes", SC))
    if por_mes:
        data = [["Mes","Pagos","Total"]] + [[r["mes"],str(r["cantidad"]),f"${float(r['total']):,.2f}"] for r in por_mes]
        t2 = Table(data, colWidths=[8*cm,4*cm,4*cm])
        t2.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),VERDE),("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),10),("ROWBACKGROUNDS",(0,1),(-1,-1),[GRIS,colors.white]),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#ddd")),("PADDING",(0,0),(-1,-1),8),("ALIGN",(1,0),(-1,-1),"CENTER")]))
        h.append(t2)
    h.append(Paragraph("Ingresos por Salón", SC))
    if por_salon:
        data = [["Salón","Pagos","Total"]] + [[r["nombre"],str(r["pagos"]),f"${float(r['total']):,.2f}"] for r in por_salon]
        t3 = Table(data, colWidths=[8*cm,3*cm,5*cm])
        t3.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),OSCURO),("TEXTCOLOR",(0,0),(-1,0),colors.white),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,-1),10),("ROWBACKGROUNDS",(0,1),(-1,-1),[GRIS,colors.white]),("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#ddd")),("PADDING",(0,0),(-1,-1),8),("ALIGN",(1,0),(-1,-1),"CENTER")]))
        h.append(t3)
    doc.build(h)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"reporte_{datetime.now().strftime('%Y%m%d')}.pdf", mimetype="application/pdf")


# ── MIS SALONES ───────────────────────────────────────────────────────────────

@app.route("/mis_salones", methods=["GET", "POST"])
@roles_permitidos("dueno")
def mis_salones():
    db, cursor = get_cursor()
    if request.method == "POST":
        plan = _plan_actual(cursor, session["user_id"])
        cursor.execute("SELECT COUNT(*) as total FROM salon WHERE dueno_id=%s", (session["user_id"],))
        actuales = cursor.fetchone()["total"]
        if plan and plan["max_salones"] is not None and actuales >= plan["max_salones"]:
            cursor.close(); db.close()
            flash(f"Tu plan {plan['nombre']} permite hasta {plan['max_salones']} salón(es). Mejora tu plan para agregar más.", "error")
            return redirect("/mi_plan")
        imagen = guardar_imagen(request.files.get("imagen"))
        cursor.execute("INSERT INTO salon (nombre, precio, latitud, longitud, imagen, dueno_id) VALUES (%s,%s,%s,%s,%s,%s)",
                       (request.form["nombre"], request.form["precio"],
                        request.form["latitud"], request.form["longitud"],
                        imagen, session["user_id"]))
        db.commit()
        flash("Salón registrado.", "success")
        return redirect("/mis_salones")
    cursor.execute("SELECT * FROM salon WHERE dueno_id=%s", (session["user_id"],))
    salones = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("mis_salones.html", salones=salones)

@app.route("/mis_salones/eliminar/<int:salon_id>", methods=["POST"])
@roles_permitidos("dueno")
def eliminar_salon(salon_id):
    db, cursor = get_cursor()
    cursor.execute("SELECT id FROM salon WHERE id=%s AND dueno_id=%s", (salon_id, session["user_id"]))
    if not cursor.fetchone(): cursor.close(); db.close(); abort(403)
    cursor.execute("DELETE FROM salon WHERE id=%s", (salon_id,))
    db.commit(); cursor.close(); db.close()
    flash("Salón eliminado.", "success")
    return redirect("/mis_salones")

@app.route("/mis_salones/editar/<int:salon_id>", methods=["GET", "POST"])
@roles_permitidos("dueno")
def editar_salon(salon_id):
    db, cursor = get_cursor()
    cursor.execute("SELECT * FROM salon WHERE id=%s AND dueno_id=%s", (salon_id, session["user_id"]))
    salon = cursor.fetchone()
    if not salon: cursor.close(); db.close(); abort(403)
    if request.method == "POST":
        imagen = guardar_imagen(request.files.get("imagen")) or salon["imagen"]
        cursor.execute("UPDATE salon SET nombre=%s, precio=%s, latitud=%s, longitud=%s, imagen=%s WHERE id=%s",
                       (request.form["nombre"], request.form["precio"],
                        request.form["latitud"], request.form["longitud"],
                        imagen, salon_id))
        db.commit(); cursor.close(); db.close()
        flash("Salón actualizado.", "success")
        return redirect("/mis_salones")
    cursor.close(); db.close()
    return render_template("editar_salon.html", salon=salon)


# ── PLANES ────────────────────────────────────────────────────────────────────

@app.route("/precios")
def precios():
    db, cursor = get_cursor()
    cursor.execute("SELECT * FROM planes ORDER BY precio ASC")
    planes = cursor.fetchall()
    plan_actual = _plan_actual(cursor, session["user_id"]) if session.get("rol") == "dueno" else None
    cursor.close(); db.close()
    return render_template("precios.html", planes=planes, plan=plan_actual)

@app.route("/mi_plan")
@roles_permitidos("dueno")
def mi_plan():
    db, cursor = get_cursor()
    plan = _plan_actual(cursor, session["user_id"])
    cursor.execute("SELECT COUNT(*) as total FROM salon WHERE dueno_id=%s", (session["user_id"],))
    salones_usados = cursor.fetchone()["total"]
    cursor.execute("SELECT * FROM planes ORDER BY precio ASC")
    planes = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("mi_plan.html", plan=plan, salones_usados=salones_usados, planes=planes)


@app.route("/suscripcion/checkout/<slug>", methods=["POST"])
@roles_permitidos("dueno")
def suscripcion_checkout(slug):
    db, cursor = get_cursor()
    cursor.execute("SELECT * FROM planes WHERE slug=%s", (slug,))
    plan = cursor.fetchone()
    if not plan:
        cursor.close(); db.close(); abort(404)

    # El mes gratis solo se da al registrarse; regresar a él dejaría
    # usar la app gratis para siempre.
    if slug == "gratis":
        cursor.close(); db.close()
        flash("El mes gratis solo aplica al crear tu cuenta.", "error")
        return redirect("/mi_plan")

    actual = _plan_actual(cursor, session["user_id"])
    cursor.close(); db.close()
    if actual and actual["slug"] == slug and actual["suscripcion_estado"] == "activa":
        flash(f"Ya tienes el plan {plan['nombre']} activo.", "success")
        return redirect("/mi_plan")

    price_id = STRIPE_PRECIOS.get(slug)
    if not stripe.api_key or not price_id:
        flash("El cobro con Stripe todavía no está configurado en este servidor — contacta al administrador.", "error")
        return redirect("/mi_plan")

    checkout_session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(session["user_id"]),
        metadata={"user_id": str(session["user_id"]), "plan_slug": slug},
        success_url=request.url_root.rstrip("/") + "/suscripcion/exito?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=request.url_root.rstrip("/") + "/mi_plan",
    )
    return redirect(checkout_session.url, code=303)


@app.route("/suscripcion/exito")
@roles_permitidos("dueno")
def suscripcion_exito():
    flash("¡Pago recibido! Tu plan se activa en cuanto Stripe confirme el pago (unos segundos).", "success")
    return redirect("/mi_plan")


@app.route("/webhooks/stripe", methods=["POST"])
def webhook_stripe():
    """Fuente de verdad para activar el plan tras el pago — Stripe llama
    aquí directamente, no depende de que el cliente vuelva a /suscripcion/exito."""
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        return jsonify({"error": "Webhook no configurado"}), 400
    try:
        event = stripe.Webhook.construct_event(
            request.data, request.headers.get("Stripe-Signature"), secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        return jsonify({"error": "Firma inválida"}), 400

    tipo = event["type"]
    data = event["data"]["object"]

    if tipo == "checkout.session.completed":
        metadata  = data.get("metadata") or {}
        user_id   = metadata.get("user_id") or data.get("client_reference_id")
        plan_slug = metadata.get("plan_slug")
        if user_id and plan_slug:
            db, cursor = get_cursor()
            cursor.execute("SELECT id FROM planes WHERE slug=%s", (plan_slug,))
            plan = cursor.fetchone()
            if plan:
                # Se guarda la suscripción de Stripe para que los eventos de
                # renovación/cancelación de abajo sepan a qué dueño tocar.
                cursor.execute("""
                    UPDATE usuarios SET plan_id=%s, suscripcion_estado='activa',
                           stripe_customer_id=%s, stripe_subscription_id=%s
                    WHERE id=%s
                """, (plan["id"], data.get("customer"), data.get("subscription"), user_id))
                db.commit()
            cursor.close(); db.close()

    elif tipo in ("customer.subscription.updated", "customer.subscription.deleted"):
        # Renovaciones, pagos fallidos y cancelaciones llegan como cambios de
        # estado de la suscripción. En 'past_due' Stripe sigue reintentando el
        # cobro, así que el dueño conserva el acceso hasta que pase a 'unpaid'
        # o 'canceled'.
        status = "canceled" if tipo == "customer.subscription.deleted" else data.get("status")
        if status in ("active", "trialing"):
            estado = "activa"
        elif status in ("canceled", "unpaid", "incomplete_expired", "paused"):
            estado = "vencida"
        else:
            estado = None
        if estado:
            db, cursor = get_cursor()
            cursor.execute("UPDATE usuarios SET suscripcion_estado=%s WHERE stripe_subscription_id=%s",
                           (estado, data.get("id")))
            db.commit(); cursor.close(); db.close()

    return jsonify({"ok": True})


# ── ADMIN ─────────────────────────────────────────────────────────────────────

@app.route("/admin/usuarios")
@roles_permitidos("admin")
def admin_usuarios():
    db, cursor = get_cursor()
    cursor.execute("""
        SELECT u.*, p.nombre as plan_nombre, p.slug as plan_slug,
               (SELECT COUNT(*) FROM salon WHERE dueno_id=u.id) as total_salones
        FROM usuarios u LEFT JOIN planes p ON u.plan_id = p.id
        ORDER BY FIELD(u.rol,'admin','dueno','cliente'), u.nombre
    """)
    usuarios = cursor.fetchall()
    cursor.execute("SELECT * FROM planes ORDER BY precio ASC")
    planes = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("admin_usuarios.html",
        admins=[u for u in usuarios if u["rol"] == "admin"],
        duenos=[u for u in usuarios if u["rol"] == "dueno"],
        clientes=[u for u in usuarios if u["rol"] == "cliente"],
        planes=planes)


@app.route("/admin/usuarios/<int:user_id>/plan", methods=["POST"])
@roles_permitidos("admin")
def admin_cambiar_plan(user_id):
    db, cursor = get_cursor()
    cursor.execute("SELECT rol FROM usuarios WHERE id=%s", (user_id,))
    u = cursor.fetchone()
    if not u or u["rol"] != "dueno":
        cursor.close(); db.close(); abort(404)
    cursor.execute("UPDATE usuarios SET plan_id=%s, suscripcion_estado=%s WHERE id=%s",
                   (request.form["plan_id"], request.form["suscripcion_estado"], user_id))
    db.commit(); cursor.close(); db.close()
    flash("Plan actualizado.", "success")
    return redirect("/admin/usuarios")


@app.route("/admin/salones")
@roles_permitidos("admin")
def admin_salones():
    db, cursor = get_cursor()
    cursor.execute("""
        SELECT s.*, u.nombre as dueno_nombre FROM salon s
        LEFT JOIN usuarios u ON s.dueno_id = u.id
        ORDER BY u.nombre, s.nombre
    """)
    salones = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("admin_salones.html", salones=salones)


@app.route("/admin/salones/eliminar/<int:salon_id>", methods=["POST"])
@roles_permitidos("admin")
def admin_eliminar_salon(salon_id):
    db, cursor = get_cursor()
    cursor.execute("DELETE FROM salon WHERE id=%s", (salon_id,))
    db.commit(); cursor.close(); db.close()
    flash("Salón eliminado.", "success")
    return redirect("/admin/salones")


# ── MAPA ──────────────────────────────────────────────────────────────────────

@app.route("/mapa")
@login_required
def mapa():
    db, cursor = get_cursor()
    cursor.execute("""
        SELECT s.*, u.nombre as dueno_nombre, ROUND(AVG(r.calificacion),1) as promedio
        FROM salon s
        LEFT JOIN usuarios u ON s.dueno_id = u.id
        LEFT JOIN resenas r ON r.salon_id=s.id
        GROUP BY s.id
    """)
    salones = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("mapa.html", salones=salones)


# ── DETALLE SALÓN ─────────────────────────────────────────────────────────────

@app.route("/salon/<int:salon_id>")
@login_required
def detalle_salon(salon_id):
    db, cursor = get_cursor()
    cursor.execute("SELECT s.*, u.nombre as dueno_nombre FROM salon s LEFT JOIN usuarios u ON s.dueno_id=u.id WHERE s.id=%s", (salon_id,))
    salon = cursor.fetchone()
    if not salon: cursor.close(); db.close(); abort(404)
    cursor.execute("SELECT AVG(calificacion) as promedio, COUNT(*) as total FROM resenas WHERE salon_id=%s", (salon_id,))
    info = cursor.fetchone()
    cursor.execute("SELECT r.calificacion, r.comentario, r.fecha, u.nombre as autor FROM resenas r JOIN usuarios u ON r.usuario_id=u.id WHERE r.salon_id=%s ORDER BY r.fecha DESC LIMIT 10", (salon_id,))
    resenas = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("salon_detalle.html", salon=salon,
        promedio=round(info["promedio"] or 0, 1), total_resenas=info["total"], resenas=resenas)


# ── CALIFICACIONES ────────────────────────────────────────────────────────────

@app.route("/salon/<int:salon_id>/calificar", methods=["POST"])
@roles_permitidos("cliente")
def calificar_salon(salon_id):
    db, cursor = get_cursor()
    cursor.execute("""
        INSERT INTO resenas (salon_id, usuario_id, calificacion, comentario)
        VALUES (%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE calificacion=%s, comentario=%s
    """, (salon_id, session["user_id"],
          request.form["calificacion"], request.form.get("comentario",""),
          request.form["calificacion"], request.form.get("comentario","")))
    db.commit(); cursor.close(); db.close()
    flash("Calificación guardada. ¡Gracias!", "success")
    return redirect(f"/salon/{salon_id}")


# ── CALENDARIO ────────────────────────────────────────────────────────────────

@app.route("/calendario")
@roles_permitidos("admin", "dueno")
def calendario():
    COLORES = {"boda":"#3498db","cumpleaños":"#e67e22","conferencia":"#9b59b6","graduacion":"#27ae60","corporativo":"#e74c3c"}
    db, cursor = get_cursor()
    if session["rol"] == "dueno":
        cursor.execute("""
            SELECT r.cliente, r.fecha, r.tipo FROM reservaciones r
            JOIN salon s ON r.salon_id=s.id WHERE s.dueno_id=%s
        """, (session["user_id"],))
    else:
        cursor.execute("SELECT cliente, fecha, tipo FROM reservaciones")
    eventos = []
    for e in cursor.fetchall():
        color = COLORES.get((e["tipo"] or "").lower(), "#1abc9c")
        eventos.append({"title":f"{e['cliente']} — {e['tipo']}","start":str(e["fecha"]),"backgroundColor":color,"borderColor":color})
    cursor.close(); db.close()
    return render_template("calendario.html", eventos=eventos)


# ── MENSAJERÍA ────────────────────────────────────────────────────────────────
# Conversaciones 1-a-1: 'directo' (dueño <-> cliente, opcionalmente ligada a
# un salón) y 'soporte' (cualquier usuario <-> cualquier admin, cola
# compartida). Reemplaza el chat viejo de salas planas por salón.

def _autorizado_conversacion(conv, user_id, rol):
    if conv["tipo"] == "soporte":
        return rol == "admin" or conv["usuario1_id"] == user_id
    return user_id in (conv["usuario1_id"], conv["usuario2_id"])

def _marcar_leida(cursor, conversacion_id, user_id):
    """Guarda el id del último mensaje visto por este usuario en esta conversación
    (no un timestamp: dos mensajes en el mismo segundo no deben poder empatar)."""
    cursor.execute("""
        INSERT INTO conversacion_lecturas (conversacion_id, usuario_id, ultimo_mensaje_id)
        SELECT %s, %s, IFNULL(MAX(id), 0) FROM mensajes WHERE conversacion_id=%s
        ON DUPLICATE KEY UPDATE ultimo_mensaje_id = VALUES(ultimo_mensaje_id)
    """, (conversacion_id, user_id, conversacion_id))

def _conversacion_directa(cursor, db, user_a, user_b, salon_id=None):
    """Busca la conversación directa entre dos usuarios (mismo salón); si no existe, la crea."""
    u1, u2 = min(user_a, user_b), max(user_a, user_b)
    cursor.execute("""
        SELECT id FROM conversaciones
        WHERE tipo='directo' AND usuario1_id=%s AND usuario2_id=%s AND salon_id <=> %s
    """, (u1, u2, salon_id))
    row = cursor.fetchone()
    if row: return row["id"]
    cursor.execute("""
        INSERT INTO conversaciones (tipo, usuario1_id, usuario2_id, salon_id)
        VALUES ('directo', %s, %s, %s)
    """, (u1, u2, salon_id))
    db.commit()
    return cursor.lastrowid

def _conversacion_soporte(cursor, db, user_id):
    """Busca la conversación de soporte abierta del usuario; si no existe, la crea."""
    cursor.execute("""
        SELECT id FROM conversaciones
        WHERE tipo='soporte' AND usuario1_id=%s AND estado='abierta'
        ORDER BY fecha_creacion DESC LIMIT 1
    """, (user_id,))
    row = cursor.fetchone()
    if row: return row["id"]
    cursor.execute("INSERT INTO conversaciones (tipo, usuario1_id) VALUES ('soporte', %s)", (user_id,))
    db.commit()
    return cursor.lastrowid


@app.route("/mensajes")
@login_required
def mensajes_bandeja():
    db, cursor = get_cursor()
    uid = session["user_id"]
    if session["rol"] == "admin":
        cursor.execute("""
            SELECT c.*, u1.nombre as otro_nombre,
                   (SELECT mensaje FROM mensajes m WHERE m.conversacion_id=c.id ORDER BY m.fecha DESC LIMIT 1) as ultimo_mensaje,
                   (SELECT fecha   FROM mensajes m WHERE m.conversacion_id=c.id ORDER BY m.fecha DESC LIMIT 1) as ultima_fecha,
                   (SELECT MAX(m.id) FROM mensajes m WHERE m.conversacion_id=c.id) > COALESCE(cl.ultimo_mensaje_id, 0) as no_leido
            FROM conversaciones c
            JOIN usuarios u1 ON c.usuario1_id = u1.id
            LEFT JOIN conversacion_lecturas cl ON cl.conversacion_id=c.id AND cl.usuario_id=%s
            WHERE c.tipo='soporte'
            ORDER BY c.estado ASC, ultima_fecha DESC
        """, (uid,))
    else:
        cursor.execute("""
            SELECT c.*, s.nombre as salon_nombre,
                   CASE WHEN c.tipo='soporte' THEN 'Soporte'
                        WHEN c.usuario1_id=%s THEN u2.nombre
                        ELSE u1.nombre END as otro_nombre,
                   (SELECT mensaje FROM mensajes m WHERE m.conversacion_id=c.id ORDER BY m.fecha DESC LIMIT 1) as ultimo_mensaje,
                   (SELECT fecha   FROM mensajes m WHERE m.conversacion_id=c.id ORDER BY m.fecha DESC LIMIT 1) as ultima_fecha,
                   (SELECT MAX(m.id) FROM mensajes m WHERE m.conversacion_id=c.id) > COALESCE(cl.ultimo_mensaje_id, 0) as no_leido
            FROM conversaciones c
            LEFT JOIN usuarios u1 ON c.usuario1_id = u1.id
            LEFT JOIN usuarios u2 ON c.usuario2_id = u2.id
            LEFT JOIN salon s ON c.salon_id = s.id
            LEFT JOIN conversacion_lecturas cl ON cl.conversacion_id=c.id AND cl.usuario_id=%s
            WHERE (c.tipo='directo' AND (c.usuario1_id=%s OR c.usuario2_id=%s))
               OR (c.tipo='soporte' AND c.usuario1_id=%s)
            ORDER BY ultima_fecha DESC
        """, (uid, uid, uid, uid, uid))
    conversaciones = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("mensajes.html", conversaciones=conversaciones)


@app.route("/mensajes/<int:conversacion_id>", methods=["GET", "POST"])
@login_required
def conversacion_detalle(conversacion_id):
    db, cursor = get_cursor()
    cursor.execute("SELECT * FROM conversaciones WHERE id=%s", (conversacion_id,))
    conv = cursor.fetchone()
    if not conv: cursor.close(); db.close(); abort(404)
    if not _autorizado_conversacion(conv, session["user_id"], session["rol"]):
        cursor.close(); db.close(); abort(403)

    if request.method == "POST":
        mensaje = request.form.get("mensaje", "").strip()
        if mensaje:
            cursor.execute("INSERT INTO mensajes (conversacion_id, emisor_id, mensaje) VALUES (%s,%s,%s)",
                           (conversacion_id, session["user_id"], mensaje))
        # El upsert de lectura va DESPUÉS del insert — si fuera antes, el
        # mensaje que acabas de mandar contaría como "no leído" para ti mismo.
        _marcar_leida(cursor, conversacion_id, session["user_id"])
        db.commit()
        cursor.close(); db.close()
        return redirect(f"/mensajes/{conversacion_id}")

    _marcar_leida(cursor, conversacion_id, session["user_id"])
    db.commit()

    if conv["tipo"] == "soporte":
        if session["rol"] == "admin":
            cursor.execute("SELECT nombre FROM usuarios WHERE id=%s", (conv["usuario1_id"],))
            titulo = cursor.fetchone()["nombre"]
        else:
            titulo = "Soporte"
    else:
        otro_id = conv["usuario2_id"] if conv["usuario1_id"] == session["user_id"] else conv["usuario1_id"]
        cursor.execute("SELECT nombre FROM usuarios WHERE id=%s", (otro_id,))
        titulo = cursor.fetchone()["nombre"]

    salon = None
    if conv["salon_id"]:
        cursor.execute("SELECT id, nombre FROM salon WHERE id=%s", (conv["salon_id"],))
        salon = cursor.fetchone()

    cursor.execute("""
        SELECT m.*, u.nombre as emisor_nombre FROM mensajes m
        JOIN usuarios u ON m.emisor_id=u.id
        WHERE m.conversacion_id=%s ORDER BY m.fecha ASC
    """, (conversacion_id,))
    lista_mensajes = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("conversacion.html", conversacion=conv, titulo=titulo, salon=salon,
        mensajes=lista_mensajes, user_id=session["user_id"])


@app.route("/mensajes/soporte", methods=["POST"])
@login_required
def mensajes_soporte():
    db, cursor = get_cursor()
    conv_id = _conversacion_soporte(cursor, db, session["user_id"])
    cursor.close(); db.close()
    return redirect(f"/mensajes/{conv_id}")


@app.route("/mensajes/<int:conversacion_id>/cerrar", methods=["POST"])
@roles_permitidos("admin")
def mensajes_cerrar(conversacion_id):
    db, cursor = get_cursor()
    cursor.execute("SELECT tipo FROM conversaciones WHERE id=%s", (conversacion_id,))
    conv = cursor.fetchone()
    if not conv or conv["tipo"] != "soporte":
        cursor.close(); db.close(); abort(404)
    cursor.execute("UPDATE conversaciones SET estado='cerrada' WHERE id=%s", (conversacion_id,))
    db.commit(); cursor.close(); db.close()
    flash("Conversación cerrada.", "success")
    return redirect("/mensajes")


@app.route("/salon/<int:salon_id>/contactar", methods=["POST"])
@roles_permitidos("cliente")
def contactar_dueno(salon_id):
    db, cursor = get_cursor()
    cursor.execute("SELECT dueno_id FROM salon WHERE id=%s", (salon_id,))
    salon = cursor.fetchone()
    if not salon: cursor.close(); db.close(); abort(404)
    conv_id = _conversacion_directa(cursor, db, session["user_id"], salon["dueno_id"], salon_id)
    cursor.close(); db.close()
    return redirect(f"/mensajes/{conv_id}")


# Compatibilidad con enlaces viejos (/chat quedó reemplazado por /mensajes)
@app.route("/chat")
@login_required
def chat_redirect():
    return redirect("/mensajes")

@app.route("/chat/<int:salon_id>")
@login_required
def chat_salon_redirect(salon_id):
    return redirect(f"/salon/{salon_id}")


# ── SOCKETIO ──────────────────────────────────────────────────────────────────

def _room_conversacion(conversacion_id):
    return f"conv_{conversacion_id}"

@socketio.on("unirse_conversacion")
def manejar_union_conversacion(data):
    user_id = session.get("user_id")
    if not user_id: return
    db, cursor = get_cursor()
    cursor.execute("SELECT * FROM conversaciones WHERE id=%s", (data.get("conversacion_id"),))
    conv = cursor.fetchone()
    cursor.close(); db.close()
    if not conv or not _autorizado_conversacion(conv, user_id, session.get("rol")):
        return
    join_room(_room_conversacion(conv["id"]))

@socketio.on("enviar_mensaje")
def manejar_mensaje(data):
    user_id = session.get("user_id")
    if not user_id: return
    conv_id = data.get("conversacion_id")
    mensaje = (data.get("mensaje") or "").strip()
    if not mensaje or not conv_id: return
    db, cursor = get_cursor()
    try:
        cursor.execute("SELECT * FROM conversaciones WHERE id=%s", (conv_id,))
        conv = cursor.fetchone()
        if not conv or not _autorizado_conversacion(conv, user_id, session.get("rol")):
            return
        cursor.execute("INSERT INTO mensajes (conversacion_id, emisor_id, mensaje) VALUES (%s,%s,%s)",
                       (conv_id, user_id, mensaje))
        db.commit()
        cursor.execute("SELECT nombre FROM usuarios WHERE id=%s", (user_id,))
        usuario = cursor.fetchone()["nombre"]
        emit("recibir_mensaje", {"usuario":usuario,"mensaje":mensaje,"user_id":user_id},
             room=_room_conversacion(conv_id))
    finally:
        cursor.close(); db.close()


# ════════════════════════════════════════════════════════════
#  API REST — /api/v1/
#  Protegida por sesión activa o header X-API-Token
# ════════════════════════════════════════════════════════════

@app.route("/api/v1/salones")
@api_login_required
def api_salones():
    """GET /api/v1/salones — Lista todos los salones con su promedio de calificación."""
    db, cursor = get_cursor()
    cursor.execute("""
        SELECT s.id, s.nombre, s.precio, s.latitud, s.longitud,
               s.capacidad, s.descripcion, s.imagen,
               ROUND(AVG(r.calificacion),1) as promedio,
               COUNT(r.id) as total_resenas
        FROM salon s
        LEFT JOIN resenas r ON r.salon_id = s.id
        GROUP BY s.id
        ORDER BY s.nombre
    """)
    salones = cursor.fetchall()
    cursor.close(); db.close()
    return jsonify({"ok": True, "total": len(salones), "salones": salones})


@app.route("/api/v1/salones/<int:salon_id>")
@api_login_required
def api_salon_detalle(salon_id):
    """GET /api/v1/salones/<id> — Detalle de un salón específico."""
    db, cursor = get_cursor()
    cursor.execute("""
        SELECT s.*, u.nombre as dueno_nombre,
               ROUND(AVG(r.calificacion),1) as promedio
        FROM salon s
        LEFT JOIN usuarios u ON s.dueno_id = u.id
        LEFT JOIN resenas r  ON r.salon_id  = s.id
        WHERE s.id = %s
        GROUP BY s.id
    """, (salon_id,))
    salon = cursor.fetchone()
    cursor.close(); db.close()
    if not salon:
        return jsonify({"ok": False, "error": "Salón no encontrado"}), 404
    return jsonify({"ok": True, "salon": salon})


@app.route("/api/v1/reservaciones")
@api_login_required
def api_reservaciones():
    """GET /api/v1/reservaciones — Lista reservaciones (filtrable por ?salon_id=).
    Con token de API: acceso completo. Con sesión de dueño/cliente: solo lo suyo."""
    token = request.headers.get("X-API-Token")
    api_token = os.getenv("API_TOKEN", "")
    acceso_completo = bool(token and api_token and token == api_token) or session.get("rol") == "admin"

    condiciones, params = [], []
    salon_id = request.args.get("salon_id")
    if salon_id:
        condiciones.append("r.salon_id = %s"); params.append(salon_id)
    if not acceso_completo:
        if session.get("rol") == "dueno":
            condiciones.append("s.dueno_id = %s"); params.append(session["user_id"])
        elif session.get("rol") == "cliente":
            condiciones.append("r.cliente = %s"); params.append(session.get("usuario"))
        else:
            condiciones.append("1 = 0")  # sin sesión válida ni token: nada
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    db, cursor = get_cursor()
    cursor.execute(f"""
        SELECT r.*, s.nombre as salon_nombre
        FROM reservaciones r JOIN salon s ON r.salon_id = s.id
        {where} ORDER BY r.fecha DESC
    """, tuple(params))
    reservaciones = cursor.fetchall()
    cursor.close(); db.close()
    # Convertir fechas a string para serialización JSON
    for r in reservaciones:
        if r.get("fecha"):
            r["fecha"] = str(r["fecha"])
    return jsonify({"ok": True, "total": len(reservaciones), "reservaciones": reservaciones})


@app.route("/api/v1/estado")
def api_estado():
    """GET /api/v1/estado — Health check de la API."""
    db, cursor = get_cursor()
    cursor.execute("SELECT COUNT(*) as salones FROM salon")
    s = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) as reservaciones FROM reservaciones")
    r = cursor.fetchone()
    cursor.close(); db.close()
    return jsonify({
        "ok":           True,
        "version":      "1.0",
        "app":          "EventoSuite",
        "salones":      s["salones"],
        "reservaciones":r["reservaciones"]
    })


@app.route("/api/docs")
@login_required
def api_docs():
    """Documentación visual de la API."""
    return render_template("api_docs.html")


# ── RUN ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    socketio.run(app, debug=True)