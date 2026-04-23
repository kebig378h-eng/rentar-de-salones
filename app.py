from flask import (Flask, render_template, request, redirect,
                   session, flash, abort, send_file, jsonify)
from flask_socketio import SocketIO, emit
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from dotenv import load_dotenv
import mysql.connector
import uuid
import os

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

EXTENSIONES_PERMITIDAS = {"jpg", "jpeg", "png", "webp"}
TAMANO_MAXIMO_MB       = 5


# ── BD ───────────────────────────────────────────────────────────────────────
# Lee primero las variables de Railway (MYSQLHOST, etc.)
# Si no existen, usa las del .env local (DB_HOST, etc.)

def get_db():
    import os

    host = os.getenv("MYSQLHOST") or "gondola.proxy.rlwy.net"
    port = os.getenv("MYSQLPORT") or "33459"
    user = os.getenv("MYSQLUSER") or "root"
    password = os.getenv("MYSQLPASSWORD") or "jRyXYLhIrbBTUZGjGkwamxMocgeZOgst"
    database = os.getenv("MYSQLDATABASE") or "eventos_db"

    return mysql.connector.connect(
        host=host,
        port=int(port),
        user=user,
        password=password,
        database=database
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


# ── PÁGINAS DE ERROR ──────────────────────────────────────────────────────────

@app.errorhandler(403)
def error_403(e):
    return render_template("403.html"), 403

@app.errorhandler(404)
def error_404(e):
    return render_template("404.html"), 404


# ── AUTH ─────────────────────────────────────────────────────────────────────

@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        db, cursor = get_cursor()
        try:
            password_hash = generate_password_hash(request.form["password"])
            cursor.execute("""
                INSERT INTO usuarios (nombre, correo, password, rol)
                VALUES (%s,%s,%s,%s)
            """, (request.form["nombre"].strip(),
                  request.form["correo"].strip().lower(),
                  password_hash, request.form["rol"]))
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
    if rol == "admin": return redirect("/")
    if rol == "dueno": return redirect("/mis_salones")
    return redirect("/mapa")

@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada.", "success")
    return redirect("/login")


# ── DASHBOARD ─────────────────────────────────────────────────────────────────

@app.route("/")
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


# ── CLIENTES ─────────────────────────────────────────────────────────────────

@app.route("/clientes", methods=["GET", "POST"])
@roles_permitidos("admin")
def clientes():
    db, cursor = get_cursor()
    if request.method == "POST":
        cursor.execute("INSERT INTO clientes (nombre, telefono, correo) VALUES (%s,%s,%s)",
                       (request.form["nombre"], request.form["telefono"], request.form["correo"]))
        db.commit()
        flash("Cliente registrado.", "success")
        return redirect("/clientes")
    cursor.execute("SELECT * FROM clientes ORDER BY nombre")
    lista = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("clientes.html", clientes=lista)

@app.route("/clientes/eliminar/<int:cliente_id>", methods=["POST"])
@roles_permitidos("admin")
def eliminar_cliente(cliente_id):
    db, cursor = get_cursor()
    cursor.execute("DELETE FROM clientes WHERE id=%s", (cliente_id,))
    db.commit(); cursor.close(); db.close()
    flash("Cliente eliminado.", "success")
    return redirect("/clientes")

@app.route("/clientes/editar/<int:cliente_id>", methods=["GET", "POST"])
@roles_permitidos("admin")
def editar_cliente(cliente_id):
    db, cursor = get_cursor()
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
@roles_permitidos("admin", "dueno", "cliente")
def reservaciones():
    db, cursor = get_cursor()

    if request.method == "POST":
        cliente_nombre = request.form["cliente"]
        fecha = request.form["fecha"]
        tipo = request.form["tipo"]
        salon = request.form.get("salon")

        # 🔥 Buscar cliente_id
        cursor.execute("SELECT id FROM clientes WHERE nombre=%s LIMIT 1", (cliente_nombre,))
        c = cursor.fetchone()
        cliente_id = c["id"] if c else None

        # 🔥 Validar duplicado
        cursor.execute(
            "SELECT id FROM reservaciones WHERE fecha=%s AND salon_id=%s",
            (fecha, salon)
        )
        if cursor.fetchone():
            flash("Ese salón ya está reservado en esa fecha.", "error")
        el 
    if session["rol"] == "dueno":
        query = """
            SELECT r.*, c.nombre as cliente_nombre, s.nombre as salon_nombre
            FROM reservaciones r
            LEFT JOIN clientes c ON r.cliente_id = c.id
            JOIN salon s ON r.salon_id = s.id
            WHERE s.dueno_id=%s
        """
        params = [session["user_id"]]

        if salon_filtro:
            query += " AND r.salon_id=%s"
            params.append(salon_filtro)

        query += " ORDER BY r.fecha DESC"
        cursor.execute(query, tuple(params))

    else:
        query = """
            SELECT r.*, c.nombre as cliente_nombre, s.nombre as salon_nombre
            FROM reservaciones r
            LEFT JOIN clientes c ON r.cliente_id = c.id
            JOIN salon s ON r.salon_id = s.id
        """

        if salon_filtro:
            query += " WHERE r.salon_id=%s"
            cursor.execute(query + " ORDER BY r.fecha DESC", (salon_filtro,))
        else:
            cursor.execute(query + " ORDER BY r.fecha DESC")

    reservaciones = cursor.fetchall()

    cursor.execute("SELECT * FROM clientes ORDER BY nombre")
    clientes = cursor.fetchall()

    if session["rol"] == "dueno":
        cursor.execute("SELECT * FROM salon WHERE dueno_id=%s", (session["user_id"],))
    else:
        cursor.execute("SELECT * FROM salon")

    salones = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template(
        "reservaciones.html",
        reservaciones=reservaciones,
        clientes=clientes,
        salones=salones
    )

@app.route("/reservaciones/eliminar/<int:res_id>", methods=["POST"])
@roles_permitidos("admin", "dueno", "cliente")
def eliminar_reservacion(res_id):
    db, cursor = get_cursor()
    cursor.execute("DELETE FROM reservaciones WHERE id=%s", (res_id,))
    db.commit(); cursor.close(); db.close()
    flash("Reservación eliminada.", "success")
    return redirect("/reservaciones")


# ── PAGOS ─────────────────────────────────────────────────────────────────────

@app.route("/pagos", methods=["GET", "POST"])
@roles_permitidos("admin")
def pagos():
    db, cursor = get_cursor()
    if request.method == "POST":
        cursor.execute("INSERT INTO pagos (reservacion_id, monto, tipo_pago, fecha_pago) VALUES (%s,%s,%s,%s)",
                       (request.form["reservacion"], request.form["monto"],
                        request.form["tipo"],        request.form["fecha"]))
        db.commit()
        flash("Pago registrado.", "success")
        return redirect("/pagos")
    cursor.execute("SELECT p.*, r.cliente, r.fecha as fecha_reservacion FROM pagos p JOIN reservaciones r ON p.reservacion_id=r.id ORDER BY p.fecha_pago DESC")
    pagos = cursor.fetchall()
    cursor.execute("SELECT * FROM reservaciones ORDER BY fecha DESC")
    reservaciones = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("pagos.html", pagos=pagos, reservaciones=reservaciones)


# ── CONTRATOS ─────────────────────────────────────────────────────────────────

@app.route("/contratos", methods=["GET", "POST"])
@roles_permitidos("admin", "dueno")
def contratos():
    db, cursor = get_cursor()
    if request.method == "POST":
        cursor.execute("SELECT id FROM contratos WHERE reservacion_id=%s", (request.form["reservacion"],))
        if cursor.fetchone():
            flash("Ya existe un contrato para esa reservación.", "error")
        else:
            cursor.execute("INSERT INTO contratos (reservacion_id, fecha_contrato, condiciones) VALUES (%s,%s,%s)",
                           (request.form["reservacion"], request.form["fecha"], request.form["condiciones"]))
            db.commit()
            flash("Contrato generado.", "success")
        return redirect("/contratos")
    cursor.execute("SELECT c.*, r.cliente, r.fecha as fecha_reservacion, r.tipo FROM contratos c JOIN reservaciones r ON c.reservacion_id=r.id ORDER BY c.fecha_contrato DESC")
    contratos = cursor.fetchall()
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
               u.nombre as dueno_nombre
        FROM contratos c JOIN reservaciones r ON c.reservacion_id=r.id
        JOIN salon s ON r.salon_id=s.id LEFT JOIN usuarios u ON s.dueno_id=u.id
        WHERE c.id=%s
    """, (contrato_id,))
    ct = cursor.fetchone()
    cursor.close(); db.close()
    if not ct: abort(404)
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
@roles_permitidos("admin")
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
    cursor.execute("SELECT MONTHNAME(fecha_pago) as mes, SUM(monto) as total, COUNT(*) as cantidad FROM pagos GROUP BY MONTH(fecha_pago) ORDER BY MONTH(fecha_pago)")
    por_mes = cursor.fetchall()
    cursor.execute("SELECT s.nombre, SUM(p.monto) as total, COUNT(p.id) as pagos FROM pagos p JOIN reservaciones r ON p.reservacion_id=r.id JOIN salon s ON r.salon_id=s.id GROUP BY s.id ORDER BY total DESC")
    por_salon = cursor.fetchall()
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


# ── MAPA ──────────────────────────────────────────────────────────────────────

@app.route("/mapa")
@login_required
def mapa():
    db, cursor = get_cursor()
    cursor.execute("SELECT s.*, ROUND(AVG(r.calificacion),1) as promedio FROM salon s LEFT JOIN resenas r ON r.salon_id=s.id GROUP BY s.id")
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
    cursor.execute("SELECT cliente, fecha, tipo FROM reservaciones")
    eventos = []
    for e in cursor.fetchall():
        color = COLORES.get((e["tipo"] or "").lower(), "#1abc9c")
        eventos.append({"title":f"{e['cliente']} — {e['tipo']}","start":str(e["fecha"]),"backgroundColor":color,"borderColor":color})
    cursor.close(); db.close()
    return render_template("calendario.html", eventos=eventos)


# ── CHAT ──────────────────────────────────────────────────────────────────────

@app.route("/chat", methods=["GET", "POST"])
@login_required
def chat():
    db, cursor = get_cursor()
    if request.method == "POST":
        cursor.execute("INSERT INTO mensajes (emisor_id, mensaje) VALUES (%s,%s)",
                       (session["user_id"], request.form["mensaje"]))
        db.commit()
    cursor.execute("SELECT m.*, u.nombre as emisor_nombre FROM mensajes m JOIN usuarios u ON m.emisor_id=u.id WHERE m.salon_id IS NULL ORDER BY m.fecha ASC")
    mensajes = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("chat.html", mensajes=mensajes, user_id=session["user_id"])

@app.route("/chat/<int:salon_id>", methods=["GET", "POST"])
@login_required
def chat_salon(salon_id):
    db, cursor = get_cursor()
    if request.method == "POST":
        cursor.execute("INSERT INTO mensajes (emisor_id, salon_id, mensaje) VALUES (%s,%s,%s)",
                       (session["user_id"], salon_id, request.form["mensaje"]))
        db.commit()
    cursor.execute("SELECT * FROM salon WHERE id=%s", (salon_id,))
    salon = cursor.fetchone()
    if not salon: cursor.close(); db.close(); abort(404)
    cursor.execute("SELECT m.*, u.nombre as emisor_nombre FROM mensajes m JOIN usuarios u ON m.emisor_id=u.id WHERE m.salon_id=%s ORDER BY m.fecha ASC", (salon_id,))
    mensajes = cursor.fetchall()
    cursor.close(); db.close()
    return render_template("chat_salon.html", salon=salon, mensajes=mensajes, user_id=session["user_id"])


# ── SOCKETIO ──────────────────────────────────────────────────────────────────

@socketio.on("enviar_mensaje")
def manejar_mensaje(data):
    user_id = session.get("user_id")
    if not user_id: return
    mensaje  = data.get("mensaje","").strip()
    salon_id = data.get("salon_id")
    if not mensaje: return
    db, cursor = get_cursor()
    try:
        cursor.execute("INSERT INTO mensajes (emisor_id, salon_id, mensaje) VALUES (%s,%s,%s)",
                       (user_id, salon_id, mensaje))
        db.commit()
        cursor.execute("SELECT nombre FROM usuarios WHERE id=%s", (user_id,))
        usuario = cursor.fetchone()["nombre"]
        emit("recibir_mensaje", {"usuario":usuario,"mensaje":mensaje,"user_id":user_id}, broadcast=True)
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
    """GET /api/v1/reservaciones — Lista reservaciones (filtrable por ?salon_id=)."""
    db, cursor = get_cursor()
    salon_id = request.args.get("salon_id")
    if salon_id:
        cursor.execute("""
            SELECT r.*, s.nombre as salon_nombre
            FROM reservaciones r JOIN salon s ON r.salon_id = s.id
            WHERE r.salon_id = %s ORDER BY r.fecha DESC
        """, (salon_id,))
    else:
        cursor.execute("""
            SELECT r.*, s.nombre as salon_nombre
            FROM reservaciones r JOIN salon s ON r.salon_id = s.id
            ORDER BY r.fecha DESC
        """)
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
    # En Railway: PORT viene como variable de entorno
    # En local: usa el puerto 5000 por defecto
    port  = int(os.getenv("PORT", 5000))
    debug = os.getenv("RAILWAY_ENVIRONMENT") is None  # False en Railway, True en local
    socketio.run(app, host="0.0.0.0", port=port, debug=debug)