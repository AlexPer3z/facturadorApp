from flask import Flask, request, render_template, send_file
from twilio.twiml.messaging_response import MessagingResponse
from reportlab.pdfgen import canvas
from datetime import datetime
import os
from dotenv import load_dotenv
from supabase import create_client, Client
import uuid
import smtplib
from email.message import EmailMessage
import openpyxl
import io

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)

@app.route("/")
def index():
    return "✅ App de facturación corriendo. Accedé a /dashboard para ver las facturas."

@app.route("/whatsapp", methods=["POST"])
def whatsapp_reply():
    sender = request.form.get("From")
    body = request.form.get("Body").strip()
    resp = MessagingResponse()
    msg = resp.message()

    if body.lower() in ["hola", "factura", "inicio", "empezar"]:
        msg.body("🧾 Para generar tu factura, enviame todos los datos con este formato:\n\n"
                 "Nombre: Juan Pérez\n"
                 "CUIT: 20-12345678-9\n"
                 "Email: juan@example.com\n"
                 "Descripción: Zapatos\n"
                 "Importe: 12345.67\n"
                 "Pago: Transferencia")
        return str(resp)

    try:
        lines = body.split("\n")
        data = {}
        for line in lines:
            if ":" in line:
                key, value = line.split(":", 1)
                data[key.strip().lower()] = value.strip()

        required_fields = ["nombre", "cuit", "email", "descripción", "importe", "pago"]
        if not all(k in data for k in required_fields):
            msg.body("⚠️ Faltan datos. Asegurate de enviar todos los campos como en el ejemplo.")
            return str(resp)

        data["importe"] = float(data["importe"])

        filename = generate_arca_invoice(data)
        file_url = request.url_root + "static/" + filename

        supabase.table("facturas").insert({
            "cliente_nombre": data["nombre"],
            "cliente_cuit": data["cuit"],
            "email": data["email"],
            "descripcion": data["descripción"],
            "importe": data["importe"],
            "medio_pago": data["pago"],
            "archivo_url": file_url,
            "whatsapp": sender,
        }).execute()

        send_email_with_pdf(data["email"], filename)

        msg.body("✅ Factura tipo C generada. Aquí tenés el PDF:")
        msg.media(file_url)
        msg.body("📧 También se envió una copia al email del cliente.")

    except Exception as e:
        print("ERROR:", e)
        msg.body("❌ Hubo un error al procesar los datos. Asegurate de enviarlos bien formateados.")

    return str(resp)

@app.route("/dashboard")
def dashboard():
    q = request.args.get("q", "").lower()
    start = request.args.get("start")
    end = request.args.get("end")

    result = supabase.table("facturas").select("*").order("created_at", desc=True).execute()
    facturas = result.data if result.data else []

    if q:
        facturas = [f for f in facturas if q in f["cliente_nombre"].lower() or q in f["descripcion"].lower()]
    if start:
        facturas = [f for f in facturas if f["created_at"][:10] >= start]
    if end:
        facturas = [f for f in facturas if f["created_at"][:10] <= end]

    return render_template("dashboard.html", facturas=facturas)

@app.route("/dashboard/export")
def export_excel():
    result = supabase.table("facturas").select("*").order("created_at", desc=True).execute()
    facturas = result.data if result.data else []

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Facturas"

    headers = ["Fecha", "Cliente", "CUIT/DNI", "Descripción", "Importe", "Pago", "Archivo PDF"]
    ws.append(headers)

    for f in facturas:
        ws.append([
            f["created_at"][:16].replace("T", " "),
            f["cliente_nombre"],
            f["cliente_cuit"],
            f["descripcion"],
            f["importe"],
            f["medio_pago"],
            f["archivo_url"]
        ])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        download_name="facturas.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

def generate_arca_invoice(data):
    os.makedirs("static", exist_ok=True)
    filename = f"factura_arca_{uuid.uuid4().hex}.pdf"
    filepath = os.path.join("static", filename)

    c = canvas.Canvas(filepath)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(200, 800, "Factura C (Simulada)")
    c.setFont("Helvetica", 12)
    c.drawString(50, 770, "Emisor: Mia-Shoes")
    c.drawString(50, 755, "CUIT: 20-45154254-1")
    c.drawString(50, 740, "Condición frente al IVA: Monotributista")
    c.drawString(50, 725, "Punto de Venta: Mia-Shoes")

    c.drawString(50, 700, f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    c.drawString(50, 680, f"Cliente: {data['nombre']}")
    c.drawString(50, 665, f"CUIT/DNI: {data['cuit']}")
    c.drawString(50, 650, f"Email: {data['email']}")
    c.drawString(50, 635, f"Detalle: {data['descripción']}")
    c.drawString(50, 620, f"Importe Total: ${data['importe']:.2f}")
    c.drawString(50, 605, f"Medio de Pago: {data['pago']}")
    c.drawString(50, 580, "Factura generada automáticamente. No válida como comprobante fiscal real.")
    c.save()

    return filename

def send_email_with_pdf(email, filename):
    filepath = os.path.join("static", filename)

    msg = EmailMessage()
    msg["Subject"] = "Factura ARCA - Mia-Shoes"
    msg["From"] = EMAIL_SENDER
    msg["To"] = email
    msg.set_content("Adjunto encontrarás tu factura emitida por Mia-Shoes. Gracias por tu compra.")

    with open(filepath, "rb") as f:
        pdf_data = f.read()
        msg.add_attachment(pdf_data, maintype="application", subtype="pdf", filename=filename)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_SENDER, EMAIL_PASS)
        smtp.send_message(msg)

if __name__ == "__main__":
    app.run(debug=True)
