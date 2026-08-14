import time
import json
import sqlite3
import urllib.request
import os
import shutil
from datetime import datetime
from fastapi import FastAPI, Request, Form, HTTPException, status, UploadFile, File, Response
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

DATABASE_NAME = "lomas.db"

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Las Lomas Local Management App")

# Configure CORS for subdomains and local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://las-lomas.vercel.app",
        "https://laslomas.cr",
        "https://www.laslomas.cr",
        "http://localhost:8000",
        "http://127.0.0.1:8000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates Setup
templates = Jinja2Templates(directory="admin/templates")

# Mount public website images
app.mount("/images", StaticFiles(directory="website/images"), name="images")

# Ensure uploads directory exists
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# ----------------- DATABASE MANAGEMENT -----------------

def get_db():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.execute("PRAGMA foreign_keys = ON;") # Enforce foreign key constraints
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Leads Table (CRM)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT,
        phone TEXT,
        origin TEXT,
        details TEXT,
        status TEXT,
        replied INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # 2. Tasks Table (Gantt)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        status TEXT DEFAULT 'Pendiente',
        due_date TEXT,
        start_date TEXT,
        progress INTEGER DEFAULT 0,
        predecessor INTEGER,
        phase TEXT,
        collaborator_id INTEGER,
        decision_path TEXT DEFAULT 'core',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (collaborator_id) REFERENCES collaborators(id) ON DELETE SET NULL
    )
    """)
    
    # 3. Finances Table (Cashflow)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS finances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        category TEXT,
        concept TEXT,
        amount_usd REAL,
        amount_crc REAL,
        currency TEXT,
        exchange_rate REAL,
        date TEXT,
        invoice_path TEXT,
        category_type TEXT,
        base_amount REAL,
        tax_amount REAL,
        iva_rate REAL DEFAULT 0.13,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 4. Collaborators Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS collaborators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        role TEXT
    )
    """)

    # 5. Subtasks Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subtasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        completed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
    )
    """)

    # 6. Payment Schedules Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payment_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        concept TEXT NOT NULL,
        amount_gross REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        due_date TEXT NOT NULL,
        status TEXT DEFAULT 'Pendiente',
        invoice_file TEXT,
        paid_at TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
    )
    """)

    # 7. Decisions Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        selected_option TEXT NOT NULL,
        notes TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Migrations: Add new columns if they do not exist
    cursor.execute("PRAGMA table_info(tasks)")
    tasks_cols = [row["name"] for row in cursor.fetchall()]
    if "collaborator_id" not in tasks_cols:
        cursor.execute("ALTER TABLE tasks ADD COLUMN collaborator_id INTEGER REFERENCES collaborators(id) ON DELETE SET NULL")
    if "decision_path" not in tasks_cols:
        cursor.execute("ALTER TABLE tasks ADD COLUMN decision_path TEXT DEFAULT 'core'")

    cursor.execute("PRAGMA table_info(finances)")
    finances_cols = [row["name"] for row in cursor.fetchall()]
    if "invoice_path" not in finances_cols:
        cursor.execute("ALTER TABLE finances ADD COLUMN invoice_path TEXT")
    if "category_type" not in finances_cols:
        cursor.execute("ALTER TABLE finances ADD COLUMN category_type TEXT")
    if "base_amount" not in finances_cols:
        cursor.execute("ALTER TABLE finances ADD COLUMN base_amount REAL")
    if "tax_amount" not in finances_cols:
        cursor.execute("ALTER TABLE finances ADD COLUMN tax_amount REAL")
    if "iva_rate" not in finances_cols:
        cursor.execute("ALTER TABLE finances ADD COLUMN iva_rate REAL DEFAULT 0.13")

    # Pre-populate collaborators
    cursor.execute("SELECT COUNT(*) as count FROM collaborators")
    if cursor.fetchone()["count"] == 0:
        collabs = [
            ("Alejandra Castro", "Directora de Proyecto"),
            ("Sebastián Rodríguez", "Director de Operaciones"),
            ("Allan", "Topógrafo"),
            ("Gonzalo", "Abogado"),
            ("Daniela", "Gestión de Servicios"),
            ("Favio", "Ventas"),
            ("Santiago", "Ventas")
        ]
        for name, role in collabs:
            cursor.execute("INSERT INTO collaborators (name, role) VALUES (?, ?)", (name, role))
        conn.commit()

    # Pre-populate decisions
    cursor.execute("SELECT COUNT(*) as count FROM decisions")
    if cursor.fetchone()["count"] == 0:
        decs = [
            ("water_option", "Abastecimiento de Agua", "ASADA", "Conexión a la red de la ASADA local."),
            ("internet_option", "Conexión a Internet", "Fibra", "Conectividad física por Fibra Óptica.")
        ]
        for key, title, opt, notes in decs:
            cursor.execute("INSERT INTO decisions (key, title, selected_option, notes) VALUES (?, ?, ?, ?)", (key, title, opt, notes))
        conn.commit()

    # Pre-populate default Gantt tasks if empty (Las Lomas Development Roadmap)
    cursor.execute("SELECT COUNT(*) as count FROM tasks")
    if cursor.fetchone()["count"] == 0:
        default_tasks = [
            ("Regulación y Permisos", "Viabilidad Ambiental SETENA D1", "Viabilidad ambiental D1 ya aprobada y en firme.", "2026-06-01", "2026-08-01", "Completado", 100, None),
            ("Regulación y Permisos", "Concesión de Agua y Pozos Aprobados", "Trámite e inscripción de pozos y concesiones hídricas.", "2026-07-01", "2026-08-20", "En Proceso", 90, None),
            ("Planificación y Diseño", "Diseño Conceptual del Masterplan (150 lotes)", "Plan maestro preliminar de distribución de lotes.", "2026-08-01", "2026-09-15", "En Proceso", 45, None),
            ("Regulación y Permisos", "Planos de Catastro e Inscripción de Segregaciones", "Visado catastral de segregaciones de los lotes.", "2026-09-16", "2026-11-15", "Pendiente", 0, 3),
            ("Planificación y Diseño", "Diseño Técnico de Vialidad e Hidráulica", "Ingeniería de escorrentías e infraestructura vial.", "2026-09-16", "2026-10-31", "Pendiente", 0, 3),
            ("Planificación y Diseño", "Diseño de Áreas Comunes y Amenidades (Club del Río)", "Concepto de casa club y miradores de montaña.", "2026-10-01", "2026-11-30", "Pendiente", 0, 3),
            ("Regulación y Permisos", "Permisos Municipales de Construcción", "Obtención de licencia municipal para movimiento de tierra y calles.", "2026-11-16", "2027-01-15", "Pendiente", 0, 4),
            ("Mercadeo y Ventas", "Publicación de Landing Page & Brochure de Inversión", "Sitio web de aterrizaje y brochure de pre-venta digital.", "2026-08-10", "2026-08-25", "En Proceso", 80, None),
            ("Mercadeo y Ventas", "Configuración del CRM y Canales de Captación", "Integración de leads y control de prospectos sin contestar.", "2026-08-14", "2026-08-31", "En Proceso", 20, None),
            ("Mercadeo y Ventas", "Lanzamiento Oficial de Pre-venta (Fase 1: 30 lotes)", "Inicio formal de colocación de lotes a clientes VIP.", "2026-09-01", "2026-12-31", "Pendiente", 0, 8),
            ("Construcción e Infraestructura", "Movimiento de Tierras y Caminos Internos", "Excavación y conformación de calles internas.", "2027-01-20", "2027-04-30", "Pendiente", 0, 7),
            ("Construcción e Infraestructura", "Red de Agua Potable y Conexiones", "Instalación de tuberías subterráneas y tomas.", "2027-03-01", "2027-05-31", "Pendiente", 0, 11),
            ("Construcción e Infraestructura", "Canalización Eléctrica y Alumbrado", "Posteado y tendido de líneas eléctricas secundarias.", "2027-04-01", "2027-06-30", "Pendiente", 0, 11),
            ("Construcción e Infraestructura", "Construcción de Amenidades (Club y Senderos)", "Edificación de la zona social y senderismo.", "2027-05-01", "2027-08-31", "Pendiente", 0, 11),
            ("Entrega y Cierre", "Firma de Escrituras y Cierre de Ventas (Fase 1)", "Traspaso notarial oficial de los primeros lotes a compradores.", "2027-06-01", "2027-09-30", "Pendiente", 0, 12),
            ("Entrega y Cierre", "Entrega Física de Lotes a Propietarios", "Handover de lotes listos para construir.", "2027-10-01", "2027-11-30", "Pendiente", 0, 15)
        ]
        for phase, title, desc, start_date, due_date, status, progress, pred in default_tasks:
            cursor.execute("""
            INSERT INTO tasks (phase, title, description, start_date, due_date, status, progress, predecessor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (phase, title, desc, start_date, due_date, status, progress, pred))
        conn.commit()
        
    conn.close()

@app.on_event("startup")
def startup_event():
    init_db()

# ----------------- EXCHANGE RATE CACHING -----------------

EXCHANGE_RATE_CACHE = {
    "rate": 518.5,
    "last_fetched": 0
}

def get_exchange_rate():
    current_time = time.time()
    # Cache for 1 hour (3600 seconds)
    if current_time - EXCHANGE_RATE_CACHE["last_fetched"] < 3600:
        return EXCHANGE_RATE_CACHE["rate"]
        
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            rate = data["rates"].get("CRC", 518.5)
            EXCHANGE_RATE_CACHE["rate"] = rate
            EXCHANGE_RATE_CACHE["last_fetched"] = current_time
            print(f"Exchange rate updated automatically: 1 USD = {rate} CRC")
            return rate
    except Exception as e:
        print("Failed to fetch exchange rate dynamically, using fallback:", e)
        return EXCHANGE_RATE_CACHE["rate"]

# ----------------- PUBLIC WEBSITE ROUTES -----------------

@app.get("/")
def read_root():
    return FileResponse("website/index.html")

@app.get("/styles.css")
def read_styles():
    return FileResponse("website/styles.css")

# ----------------- BACK-OFFICE RENDER ROUTES -----------------

@app.get("/admin")
def admin_home():
    return RedirectResponse(url="/admin/dashboard", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/admin/dashboard")
def admin_dashboard(request: Request):
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Total Leads vs Pending Leads
    cursor.execute("SELECT COUNT(*) as total FROM leads")
    total_leads = cursor.fetchone()["total"]
    
    cursor.execute("SELECT COUNT(*) as pending FROM leads WHERE replied = 0")
    pending_leads_count = cursor.fetchone()["pending"]
    
    # 2. Project Progress & Upcoming Tasks
    cursor.execute("SELECT * FROM tasks")
    all_tasks = cursor.fetchall()
    total_tasks = len(all_tasks)
    project_progress = 0.0
    if total_tasks > 0:
        total_progress = sum(t["progress"] for t in all_tasks)
        project_progress = total_progress / total_tasks
        
    cursor.execute("""
    SELECT * FROM tasks 
    WHERE status IN ('Pendiente', 'En Proceso') 
    ORDER BY due_date ASC LIMIT 5
    """)
    upcoming_tasks = [dict(row) for row in cursor.fetchall()]
    
    # 3. Financial Totals
    cursor.execute("SELECT * FROM finances")
    finances = cursor.fetchall()
    
    total_income_usd = 0.0
    total_income_crc = 0.0
    total_expense_usd = 0.0
    total_expense_crc = 0.0
    
    for tx in finances:
        amount_usd = tx["amount_usd"] or 0.0
        amount_crc = tx["amount_crc"] or 0.0
        if tx["type"] == "Ingreso":
            total_income_usd += amount_usd
            total_income_crc += amount_crc
        else:
            total_expense_usd += amount_usd
            total_expense_crc += amount_crc
            
    # 4. Recent Leads
    cursor.execute("SELECT * FROM leads ORDER BY id DESC LIMIT 5")
    recent_leads = [dict(row) for row in cursor.fetchall()]

    # 5. Fetch Decisions
    cursor.execute("SELECT * FROM decisions")
    decisions_rows = cursor.fetchall()
    decisions = {row["key"]: row["selected_option"] for row in decisions_rows}
    
    # 6. Calculate Net Balance, 15% Capital Gains Tax, and Net Utility
    net_balance_usd = total_income_usd - total_expense_usd
    net_balance_crc = total_income_crc - total_expense_crc

    if net_balance_usd > 0:
        capital_gains_tax_usd = net_balance_usd * 0.15
        capital_gains_tax_crc = net_balance_crc * 0.15
        net_utility_usd = net_balance_usd - capital_gains_tax_usd
        net_utility_crc = net_balance_crc - capital_gains_tax_crc
    else:
        capital_gains_tax_usd = 0.0
        capital_gains_tax_crc = 0.0
        net_utility_usd = net_balance_usd
        net_utility_crc = net_balance_crc
        
    conn.close()
    
    exchange_rate = get_exchange_rate()
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active_page": "dashboard",
        "total_leads": total_leads,
        "pending_leads_count": pending_leads_count,
        "project_progress": project_progress,
        "upcoming_tasks": upcoming_tasks,
        "total_income_usd": total_income_usd,
        "total_income_crc": total_income_crc,
        "total_expense_usd": total_expense_usd,
        "total_expense_crc": total_expense_crc,
        "net_balance_usd": net_balance_usd,
        "net_balance_crc": net_balance_crc,
        "capital_gains_tax_usd": capital_gains_tax_usd,
        "capital_gains_tax_crc": capital_gains_tax_crc,
        "net_utility_usd": net_utility_usd,
        "net_utility_crc": net_utility_crc,
        "recent_leads": recent_leads,
        "exchange_rate": exchange_rate,
        "decisions": decisions
    })

@app.get("/admin/crm")
def admin_crm(request: Request, filter: str = "all"):
    conn = get_db()
    cursor = conn.cursor()
    
    # Get pending count for topbar
    cursor.execute("SELECT COUNT(*) as pending FROM leads WHERE replied = 0")
    pending_leads_count = cursor.fetchone()["pending"]
    
    # Query leads
    if filter == "pending":
        cursor.execute("SELECT * FROM leads WHERE replied = 0 ORDER BY id DESC")
    else:
        cursor.execute("SELECT * FROM leads ORDER BY id DESC")
        
    leads_rows = cursor.fetchall()
    leads_list = [dict(row) for row in leads_rows]
    leads_json = json.dumps(leads_list, default=str)
    
    conn.close()
    
    exchange_rate = get_exchange_rate()
    
    return templates.TemplateResponse("crm.html", {
        "request": request,
        "active_page": "crm",
        "filter_mode": filter,
        "pending_leads_count": pending_leads_count,
        "leads": leads_list,
        "leads_json": leads_json,
        "exchange_rate": exchange_rate
    })

def generate_mermaid_gantt(tasks):
    lines = [
        "gantt",
        "    title Cronograma del Proyecto - LAS LOMAS",
        "    dateFormat YYYY-MM-DD",
        "    axisFormat %m-%Y",
        "    todayMarker stroke-width:2px,stroke:#2D5A3F,opacity:0.6"
    ]
    
    # Group tasks by phase
    phases = {}
    for task in tasks:
        phase = task["phase"] or "Otros"
        phases.setdefault(phase, []).append(task)
        
    for phase, phase_tasks in phases.items():
        lines.append(f"    section {phase}")
        for t in phase_tasks:
            # Clean title of forbidden Mermaid symbols
            title = t["title"].replace(":", "-").replace(",", " ")
            status_tag = ""
            if t["status"] == "Completado":
                status_tag = "done"
            elif t["status"] == "En Proceso":
                status_tag = "active"
                
            tag = f"t{t['id']}"
            start = t["start_date"] or "2026-08-14"
            due = t["due_date"] or "2026-09-14"
            
            # Predecessors logic in Mermaid
            if t["predecessor"]:
                line = f"        {title} :{status_tag}, {tag}, after t{t['predecessor']}, {due}"
            else:
                line = f"        {title} :{status_tag}, {tag}, {start}, {due}"
            lines.append(line)
            
    return "\n".join(lines)

@app.get("/admin/gantt")
def admin_gantt(request: Request):
    conn = get_db()
    cursor = conn.cursor()
    
    # Get pending leads count
    cursor.execute("SELECT COUNT(*) as pending FROM leads WHERE replied = 0")
    pending_leads_count = cursor.fetchone()["pending"]

    # Fetch active path decisions
    cursor.execute("SELECT key, selected_option FROM decisions")
    decisions = {row["key"]: row["selected_option"] for row in cursor.fetchall()}
    water_path = decisions.get("water_option", "ASADA").lower()
    internet_path = decisions.get("internet_option", "Fibra").lower()
    
    # Fetch all tasks and filter by active decisions
    cursor.execute("SELECT * FROM tasks ORDER BY start_date ASC")
    all_tasks = cursor.fetchall()
    tasks_list = []
    for row in all_tasks:
        t = dict(row)
        dpath = t.get("decision_path")
        if dpath and dpath != "core":
            if dpath in ["asada", "pozo"] and dpath != water_path:
                continue
            if dpath in ["fibra", "starlink"] and dpath != internet_path:
                continue
        tasks_list.append(t)

    tasks_json = json.dumps(tasks_list, default=str)
    
    # Group tasks by phase in Python
    grouped_tasks = {}
    for t in tasks_list:
        grouped_tasks.setdefault(t["phase"], []).append(t)
        
    # Generate Mermaid Gantt Code
    mermaid_code = generate_mermaid_gantt(tasks_list)
    
    # Predecessor dropdown list (id and title)
    cursor.execute("SELECT id, title FROM tasks ORDER BY title ASC")
    all_tasks_list = [dict(row) for row in cursor.fetchall()]

    # Fetch collaborators
    cursor.execute("SELECT * FROM collaborators ORDER BY name ASC")
    collaborators_list = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    exchange_rate = get_exchange_rate()
    
    return templates.TemplateResponse("gantt.html", {
        "request": request,
        "active_page": "gantt",
        "pending_leads_count": pending_leads_count,
        "grouped_tasks": grouped_tasks,
        "mermaid_code": mermaid_code,
        "all_tasks_list": all_tasks_list,
        "tasks_json": tasks_json,
        "exchange_rate": exchange_rate,
        "collaborators": collaborators_list,
        "decisions": decisions
    })

@app.get("/admin/finance")
def admin_finance(request: Request):
    conn = get_db()
    cursor = conn.cursor()
    
    # Get pending leads count
    cursor.execute("SELECT COUNT(*) as pending FROM leads WHERE replied = 0")
    pending_leads_count = cursor.fetchone()["pending"]
    
    # Get all transactions
    cursor.execute("SELECT * FROM finances ORDER BY date DESC, id DESC")
    tx_rows = cursor.fetchall()
    transactions = [dict(row) for row in tx_rows]
    
    total_income_usd = 0.0
    total_income_crc = 0.0
    total_expense_usd = 0.0
    total_expense_crc = 0.0
    
    for tx in transactions:
        amount_usd = tx["amount_usd"] or 0.0
        amount_crc = tx["amount_crc"] or 0.0
        if tx["type"] == "Ingreso":
            total_income_usd += amount_usd
            total_income_crc += amount_crc
        else:
            total_expense_usd += amount_usd
            total_expense_crc += amount_crc
            
    net_balance_usd = total_income_usd - total_expense_usd
    net_balance_crc = total_income_crc - total_expense_crc

    if net_balance_usd > 0:
        capital_gains_tax_usd = net_balance_usd * 0.15
        capital_gains_tax_crc = net_balance_crc * 0.15
        net_utility_usd = net_balance_usd - capital_gains_tax_usd
        net_utility_crc = net_balance_crc - capital_gains_tax_crc
    else:
        capital_gains_tax_usd = 0.0
        capital_gains_tax_crc = 0.0
        net_utility_usd = net_balance_usd
        net_utility_crc = net_balance_crc
    
    conn.close()
    
    exchange_rate = get_exchange_rate()
    
    return templates.TemplateResponse("finance.html", {
        "request": request,
        "active_page": "finance",
        "pending_leads_count": pending_leads_count,
        "transactions": transactions,
        "total_income_usd": total_income_usd,
        "total_income_crc": total_income_crc,
        "total_expense_usd": total_expense_usd,
        "total_expense_crc": total_expense_crc,
        "net_balance_usd": net_balance_usd,
        "net_balance_crc": net_balance_crc,
        "capital_gains_tax_usd": capital_gains_tax_usd,
        "capital_gains_tax_crc": capital_gains_tax_crc,
        "net_utility_usd": net_utility_usd,
        "net_utility_crc": net_utility_crc,
        "exchange_rate": exchange_rate
    })

# ----------------- API / POST ENDPOINTS -----------------

class LeadPayload(BaseModel):
    name: str = "Interesado Web"
    email: str
    phone: str = ""
    origin: str = "Website Landing"
    details: str = ""
    status: str = "New Lead"

@app.post("/api/leads")
def api_create_lead(payload: LeadPayload):
    # This endpoint receives leads from the public landing page via Fetch
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO leads (name, email, phone, origin, details, status, replied)
    VALUES (?, ?, ?, ?, ?, ?, 0)
    """, (payload.name, payload.email, payload.phone, payload.origin, payload.details, payload.status))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Lead registrado en el CRM de Las Lomas."}

@app.post("/api/leads/{id}/toggle-replied")
def toggle_lead_replied(id: int, redirect_to: str = "crm", filter: str = "all"):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT replied FROM leads WHERE id = ?", (id,))
    row = cursor.fetchone()
    if row:
        new_val = 1 if row["replied"] == 0 else 0
        cursor.execute("UPDATE leads SET replied = ? WHERE id = ?", (new_val, id))
        conn.commit()
    conn.close()
    
    url = "/admin/crm" if redirect_to == "crm" else "/admin/dashboard"
    if redirect_to == "crm":
        url += f"?filter={filter}"
        
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/leads/new")
def crm_create_lead_manual(
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(None),
    origin: str = Form("Website Landing"),
    status_val: str = Form("New Lead", alias="status"),
    replied: int = Form(0),
    details: str = Form(None)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO leads (name, email, phone, origin, status, replied, details)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (name, email, phone, origin, status_val, replied, details))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/crm", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/leads/{id}/edit")
def crm_edit_lead(
    id: int,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(None),
    origin: str = Form(...),
    status_val: str = Form(..., alias="status"),
    replied: int = Form(0),
    details: str = Form(None)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE leads 
    SET name = ?, email = ?, phone = ?, origin = ?, status = ?, replied = ?, details = ?
    WHERE id = ?
    """, (name, email, phone, origin, status_val, replied, details, id))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/crm", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/leads/{id}/delete")
def delete_lead(id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM leads WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/crm", status_code=status.HTTP_303_SEE_OTHER)

# --- Tasks API ---

def recalculate_task_progress(conn, task_id: int):
    cursor = conn.cursor()
    # Count subtasks and calculate completed percentage
    cursor.execute("SELECT COUNT(*) as total, SUM(completed) as completed FROM subtasks WHERE task_id = ?", (task_id,))
    row = cursor.fetchone()
    total = row["total"] or 0
    completed = row["completed"] or 0

    if total > 0:
        progress = int((completed / total) * 100)
        if progress == 100:
            status_val = "Completado"
        elif progress > 0:
            status_val = "En Proceso"
        else:
            status_val = "Pendiente"

        cursor.execute("""
            UPDATE tasks 
            SET progress = ?, status = ?
            WHERE id = ?
        """, (progress, status_val, task_id))
        conn.commit()

@app.post("/api/tasks/new")
def create_task(
    phase: str = Form(...),
    title: str = Form(...),
    description: str = Form(None),
    start_date: str = Form(...),
    due_date: str = Form(...),
    progress: int = Form(0),
    status_val: str = Form("Pendiente", alias="status"),
    predecessor: str = Form(None),
    collaborator_id: str = Form(None),
    decision_path: str = Form("core")
):
    pred = int(predecessor) if predecessor else None
    collab = int(collaborator_id) if collaborator_id else None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO tasks (phase, title, description, start_date, due_date, progress, status, predecessor, collaborator_id, decision_path)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (phase, title, description, start_date, due_date, progress, status_val, pred, collab, decision_path))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/gantt", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/tasks/{id}/edit")
def edit_task(
    id: int,
    phase: str = Form(...),
    title: str = Form(...),
    description: str = Form(None),
    start_date: str = Form(...),
    due_date: str = Form(...),
    progress: int = Form(...),
    status_val: str = Form(..., alias="status"),
    predecessor: str = Form(None),
    collaborator_id: str = Form(None),
    decision_path: str = Form("core")
):
    pred = int(predecessor) if predecessor else None
    collab = int(collaborator_id) if collaborator_id else None
    
    # Auto-complete status logic
    if progress == 100:
        status_val = "Completado"
    elif progress > 0 and status_val == "Pendiente":
        status_val = "En Proceso"
        
    conn = get_db()
    cursor = conn.cursor()
    
    # Override progress if subtasks exist
    cursor.execute("SELECT COUNT(*) as total, SUM(completed) as completed FROM subtasks WHERE task_id = ?", (id,))
    row = cursor.fetchone()
    total = row["total"] or 0
    completed = row["completed"] or 0
    if total > 0:
        progress = int((completed / total) * 100)
        if progress == 100:
            status_val = "Completado"
        elif progress > 0:
            status_val = "En Proceso"
        else:
            status_val = "Pendiente"

    cursor.execute("""
    UPDATE tasks 
    SET phase = ?, title = ?, description = ?, start_date = ?, due_date = ?, progress = ?, status = ?, predecessor = ?, collaborator_id = ?, decision_path = ?
    WHERE id = ?
    """, (phase, title, description, start_date, due_date, progress, status_val, pred, collab, decision_path, id))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/gantt", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/tasks/{id}/delete")
def delete_task(id: int):
    conn = get_db()
    cursor = conn.cursor()
    # Remove predecessor link on children
    cursor.execute("UPDATE tasks SET predecessor = NULL WHERE predecessor = ?", (id,))
    # Delete task (cascade will handle subtasks and payment schedules)
    cursor.execute("DELETE FROM tasks WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/gantt", status_code=status.HTTP_303_SEE_OTHER)

@app.patch("/api/tasks/{id}/assign")
def assign_task(id: int, assigned_to: int = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET collaborator_id = ? WHERE id = ?", (assigned_to, id))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Tarea {id} asignada."}

# --- Subtasks API ---

@app.get("/api/tasks/{task_id}/subtasks")
def api_get_subtasks(task_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM subtasks WHERE task_id = ?", (task_id,))
    rows = cursor.fetchall()
    conn.close()
    return JSONResponse(content=[dict(r) for r in rows])

@app.post("/api/tasks/{task_id}/subtasks/new")
def api_create_subtask(task_id: int, title: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO subtasks (task_id, title, completed) VALUES (?, ?, 0)", (task_id, title))
    conn.commit()
    recalculate_task_progress(conn, task_id)
    conn.close()
    return RedirectResponse(url="/admin/gantt", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/subtasks/{subtask_id}/toggle")
def api_toggle_subtask(subtask_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT task_id, completed FROM subtasks WHERE id = ?", (subtask_id,))
    row = cursor.fetchone()
    if row:
        new_status = 1 if row["completed"] == 0 else 0
        cursor.execute("UPDATE subtasks SET completed = ? WHERE id = ?", (new_status, subtask_id))
        conn.commit()
        recalculate_task_progress(conn, row["task_id"])
    conn.close()
    return RedirectResponse(url="/admin/gantt", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/subtasks/{subtask_id}/delete")
def api_delete_subtask(subtask_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT task_id FROM subtasks WHERE id = ?", (subtask_id,))
    row = cursor.fetchone()
    if row:
        task_id = row["task_id"]
        cursor.execute("DELETE FROM subtasks WHERE id = ?", (subtask_id,))
        conn.commit()
        recalculate_task_progress(conn, task_id)
    conn.close()
    return RedirectResponse(url="/admin/gantt", status_code=status.HTTP_303_SEE_OTHER)

# --- Payment Schedules API ---

@app.get("/api/tasks/{task_id}/payment-schedules")
def api_get_payment_schedules(task_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM payment_schedules WHERE task_id = ?", (task_id,))
    rows = cursor.fetchall()
    conn.close()
    return JSONResponse(content=[dict(r) for r in rows])

@app.post("/api/tasks/{task_id}/payment-schedules/new")
def api_create_payment_schedule(
    task_id: int,
    concept: str = Form(...),
    amount_gross: float = Form(...),
    currency: str = Form("USD"),
    due_date: str = Form(...)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO payment_schedules (task_id, concept, amount_gross, currency, due_date, status)
        VALUES (?, ?, ?, ?, ?, 'Pendiente')
    """, (task_id, concept, amount_gross, currency, due_date))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/gantt", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/payment-schedules/{id}/pay")
def api_pay_payment_schedule(
    id: int,
    invoice_file: UploadFile = File(...),
    exchange_rate: float = Form(...),
    payment_date: str = Form(None)
):
    if not payment_date:
        payment_date = datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    cursor = conn.cursor()

    # 1. Fetch payment schedule details
    cursor.execute("SELECT * FROM payment_schedules WHERE id = ?", (id,))
    schedule = cursor.fetchone()
    if not schedule:
        conn.close()
        raise HTTPException(status_code=404, detail="Hito de pago no encontrado")

    if schedule["status"] == "Pagado":
        conn.close()
        raise HTTPException(status_code=400, detail="Este hito de pago ya fue liquidado")

    # 2. Save invoice file locally
    file_extension = os.path.splitext(invoice_file.filename)[1]
    unique_filename = f"schedule_{id}_{int(time.time())}{file_extension}"
    target_path = os.path.join(UPLOAD_DIR, unique_filename)

    try:
        with open(target_path, "wb") as buffer:
            shutil.copyfileobj(invoice_file.file, buffer)
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=f"Error al escribir archivo en disco: {e}")

    # 3. Calculate currencies and Costa Rican 13% IVA desegregation
    amount_gross = schedule["amount_gross"]
    currency = schedule["currency"]
    concept_finance = f"Pago Hito Gantt: {schedule['concept']} (Tarea t{schedule['task_id']})"

    # Desegregate IVA 13%
    iva_rate = 0.13
    base_amount = amount_gross / (1.0 + iva_rate)
    tax_amount = amount_gross - base_amount

    if currency == "USD":
        amount_usd = amount_gross
        amount_crc = amount_gross * exchange_rate
    else: # CRC
        amount_crc = amount_gross
        amount_usd = amount_gross / exchange_rate

    # 4. Atomic Database Transaction
    try:
        conn.execute("BEGIN TRANSACTION;")

        # A. Update payment schedule status and attach file
        cursor.execute("""
            UPDATE payment_schedules
            SET status = 'Pagado', invoice_file = ?, paid_at = ?
            WHERE id = ?
        """, (target_path, payment_date, id))

        # B. Insert Gasto into finances ledger (OPEX / CAPEX can be assigned based on task or default to CAPEX)
        category_type = "CAPEX"
        cursor.execute("""
            INSERT INTO finances (type, category, concept, amount_usd, amount_crc, currency, exchange_rate, date, invoice_path, category_type, base_amount, tax_amount, iva_rate)
            VALUES ('Gasto', 'Construcción', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (concept_finance, amount_usd, amount_crc, currency, exchange_rate, payment_date, target_path, category_type, base_amount, tax_amount, iva_rate))

        conn.commit()
    except Exception as e:
        conn.rollback()
        # Clean up file on DB failure
        if os.path.exists(target_path):
            os.remove(target_path)
        conn.close()
        raise HTTPException(status_code=500, detail=f"Transacción abortada: {e}")

    conn.close()
    return RedirectResponse(url="/admin/gantt", status_code=status.HTTP_303_SEE_OTHER)

# --- Decisions API ---

@app.patch("/api/decisions/{key}")
def update_decision(key: str, selected_option: str = Form(...), notes: str = Form(None)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE decisions 
    SET selected_option = ?, notes = ?, updated_at = CURRENT_TIMESTAMP 
    WHERE key = ?
    """, (selected_option, notes, key))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Decisión {key} actualizada a {selected_option}"}

# --- Finances API ---

@app.post("/api/finances/new")
async def create_transaction(
    date: str = Form(...),
    type: str = Form(...),
    category: str = Form(...),
    concept: str = Form(...),
    currency: str = Form(...),
    amount: float = Form(...),  # Base amount
    exchange_rate: float = Form(...),
    category_type: str = Form("OPEX"),
    apply_tax: bool = Form(False),  # Toggle 13% IVA
    invoice_file: UploadFile = File(None)
):
    # Costa Rican 13% IVA calculation
    iva_rate = 0.13
    if apply_tax:
        base_amount = amount
        tax_amount = base_amount * iva_rate
        total_amount = base_amount + tax_amount
    else:
        base_amount = amount
        tax_amount = 0.0
        total_amount = amount

    # Currency conversion
    if currency == "USD":
        amount_usd = total_amount
        amount_crc = total_amount * exchange_rate
    else:  # CRC
        amount_crc = total_amount
        amount_usd = total_amount / exchange_rate
        
    invoice_path = None
    if invoice_file and invoice_file.filename:
        safe_filename = f"{int(time.time())}_{os.path.basename(invoice_file.filename)}"
        file_path = os.path.join(UPLOAD_DIR, safe_filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(invoice_file.file, buffer)
        invoice_path = file_path

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO finances (date, type, category, concept, amount_usd, amount_crc, currency, exchange_rate, invoice_path, category_type, base_amount, tax_amount, iva_rate)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (date, type, category, concept, amount_usd, amount_crc, currency, exchange_rate, invoice_path, category_type, base_amount, tax_amount, iva_rate))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/finance", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/finances/{id}/delete")
def delete_transaction(id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM finances WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/finance", status_code=status.HTTP_303_SEE_OTHER)

# --- iCal Feed API ---

@app.get("/api/tasks/calendar/feed.ics")
def get_calendar_feed():
    conn = get_db()
    cursor = conn.cursor()
    
    # Fetch active path decisions
    cursor.execute("SELECT key, selected_option FROM decisions")
    decisions = {row["key"]: row["selected_option"] for row in cursor.fetchall()}
    water_path = decisions.get("water_option", "ASADA").lower()
    internet_path = decisions.get("internet_option", "Fibra").lower()
    
    # Fetch tasks
    cursor.execute("SELECT * FROM tasks")
    tasks = cursor.fetchall()
    conn.close()
    
    # Standard iCalendar formatting (RFC 5545)
    calendar_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Las Lomas//Gantt App//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Las Lomas - Tareas",
        "REFRESH-INTERVAL;VALUE=DURATION:PT2H"
    ]
    
    for task in tasks:
        # Filter decision paths
        dpath = task["decision_path"]
        if dpath and dpath != "core":
            if dpath in ["asada", "pozo"] and dpath != water_path:
                continue
            if dpath in ["fibra", "starlink"] and dpath != internet_path:
                continue

        start_date = task["start_date"].replace("-", "") if task["start_date"] else ""
        due_date = task["due_date"].replace("-", "") if task["due_date"] else ""
        
        if not start_date or not due_date:
            continue
            
        calendar_lines.extend([
            "BEGIN:VEVENT",
            f"UID:task_{task['id']}@laslomas.com",
            f"DTSTART;VALUE=DATE:{start_date}",
            f"DTEND;VALUE=DATE:{due_date}",
            f"SUMMARY:{task['title']}",
            f"DESCRIPTION:Fase: {task['phase']}\\nProgreso: {task['progress']}%\\n{task['description'] or ''}",
            "STATUS:CONFIRMED",
            "END:VEVENT"
        ])
        
    calendar_lines.append("END:VCALENDAR")
    ical_content = "\r\n".join(calendar_lines)
    
    return Response(content=ical_content, media_type="text/calendar")
