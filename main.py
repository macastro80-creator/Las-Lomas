import time
import json
import sqlite3
import urllib.request
import os
import shutil
from datetime import datetime, timedelta
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
        assigned_agent_id INTEGER,
        language TEXT DEFAULT 'es',
        lead_temperature TEXT DEFAULT 'frio',
        deal_value REAL DEFAULT 0.0,
        origin_custom_label TEXT,
        contacted_at TEXT,
        meeting_at TEXT,
        visited_at TEXT,
        reserved_at TEXT,
        closed_at TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (assigned_agent_id) REFERENCES collaborators(id) ON DELETE SET NULL
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
        budget_usd REAL DEFAULT 0.0,
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
        marketing_channel TEXT,
        task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 4. Collaborators Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS collaborators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        role TEXT,
        languages TEXT DEFAULT 'es,en',
        is_active_round_robin INTEGER DEFAULT 1,
        last_assigned_at TEXT,
        commission_rate REAL DEFAULT 3.0,
        zoom_link TEXT DEFAULT 'https://zoom.us/j/laslomas'
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

    # 8. Decision Options Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS decision_options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        decision_key TEXT NOT NULL,
        option_code TEXT NOT NULL,
        option_label TEXT NOT NULL,
        description TEXT,
        pros TEXT, -- Newline-separated pros
        cons TEXT, -- Newline-separated cons
        notes TEXT, -- User annotations
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (decision_key) REFERENCES decisions (key) ON DELETE CASCADE,
        UNIQUE(decision_key, option_code)
    )
    """)

    # 9. Decision History Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS decision_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        decision_key TEXT NOT NULL,
        option_code TEXT NOT NULL,
        option_label TEXT NOT NULL,
        justification TEXT,
        changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (decision_key) REFERENCES decisions (key) ON DELETE CASCADE
    )
    """)

    # 10. Meetings Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER NOT NULL,
        title TEXT,
        scheduled_at TEXT NOT NULL,
        language TEXT NOT NULL,
        zoom_link TEXT,
        status TEXT DEFAULT 'scheduled',
        notes TEXT,
        summary TEXT,
        follow_up_plan TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (lead_id) REFERENCES leads (id) ON DELETE CASCADE
    )
    """)

    # 9. Page Views Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS page_views (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        page_path TEXT NOT NULL,
        viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 10. Assignment Rules Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS assignment_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        field_name TEXT NOT NULL,
        field_value TEXT NOT NULL,
        agent_id INTEGER NOT NULL,
        is_active INTEGER DEFAULT 1,
        priority INTEGER DEFAULT 0,
        FOREIGN KEY (agent_id) REFERENCES collaborators (id) ON DELETE CASCADE
    )
    """)

    # 11. Decision Options Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS decision_options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        decision_key TEXT NOT NULL,
        option_code TEXT NOT NULL,
        option_label TEXT NOT NULL,
        description TEXT,
        pros TEXT, -- Newline-separated pros
        cons TEXT, -- Newline-separated cons
        notes TEXT, -- User annotations
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (decision_key) REFERENCES decisions (key) ON DELETE CASCADE,
        UNIQUE(decision_key, option_code)
    )
    """)

    # 12. Decision History Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS decision_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        decision_key TEXT NOT NULL,
        option_code TEXT NOT NULL,
        option_label TEXT NOT NULL,
        justification TEXT,
        changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (decision_key) REFERENCES decisions (key) ON DELETE CASCADE
    )
    """)

    # Migrations: Add new columns if they do not exist
    cursor.execute("PRAGMA table_info(tasks)")
    tasks_cols = [row["name"] for row in cursor.fetchall()]
    if "collaborator_id" not in tasks_cols:
        cursor.execute("ALTER TABLE tasks ADD COLUMN collaborator_id INTEGER REFERENCES collaborators(id) ON DELETE SET NULL")
    if "decision_path" not in tasks_cols:
        cursor.execute("ALTER TABLE tasks ADD COLUMN decision_path TEXT DEFAULT 'core'")
    if "budget_usd" not in tasks_cols:
        cursor.execute("ALTER TABLE tasks ADD COLUMN budget_usd REAL DEFAULT 0.0")

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
    if "marketing_channel" not in finances_cols:
        cursor.execute("ALTER TABLE finances ADD COLUMN marketing_channel TEXT")
    if "task_id" not in finances_cols:
        cursor.execute("ALTER TABLE finances ADD COLUMN task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL")

    # Leads table migrations
    cursor.execute("PRAGMA table_info(leads)")
    leads_cols = [row["name"] for row in cursor.fetchall()]
    leads_new_cols = {
        "assigned_agent_id": "INTEGER REFERENCES collaborators(id) ON DELETE SET NULL",
        "language": "TEXT DEFAULT 'es'",
        "lead_temperature": "TEXT DEFAULT 'frio'",
        "deal_value": "REAL DEFAULT 0.0",
        "origin_custom_label": "TEXT",
        "contacted_at": "TEXT",
        "meeting_at": "TEXT",
        "visited_at": "TEXT",
        "reserved_at": "TEXT",
        "closed_at": "TEXT"
    }
    for col_name, col_type in leads_new_cols.items():
        if col_name not in leads_cols:
            cursor.execute(f"ALTER TABLE leads ADD COLUMN {col_name} {col_type}")

    # Collaborators table migrations
    cursor.execute("PRAGMA table_info(collaborators)")
    collaborators_cols = [row["name"] for row in cursor.fetchall()]
    collaborators_new_cols = {
        "languages": "TEXT DEFAULT 'es,en'",
        "is_active_round_robin": "INTEGER DEFAULT 1",
        "last_assigned_at": "TEXT",
        "commission_rate": "REAL DEFAULT 3.0",
        "zoom_link": "TEXT DEFAULT 'https://zoom.us/j/laslomas'"
    }
    for col_name, col_type in collaborators_new_cols.items():
        if col_name not in collaborators_cols:
            cursor.execute(f"ALTER TABLE collaborators ADD COLUMN {col_name} {col_type}")

    # Decisions table migrations
    cursor.execute("PRAGMA table_info(decisions)")
    decisions_cols = [row["name"] for row in cursor.fetchall()]
    if "lane" not in decisions_cols:
        cursor.execute("ALTER TABLE decisions ADD COLUMN lane TEXT DEFAULT 'Identificadas'")
    if "sort_order" not in decisions_cols:
        cursor.execute("ALTER TABLE decisions ADD COLUMN sort_order INTEGER DEFAULT 0")

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

    # Pre-populate default values for collaborators
    cursor.execute("UPDATE collaborators SET languages = 'es,en', is_active_round_robin = 1, commission_rate = 3.0, zoom_link = 'https://zoom.us/j/favio-ventas' WHERE name = 'Favio'")
    cursor.execute("UPDATE collaborators SET languages = 'es,en', is_active_round_robin = 1, commission_rate = 3.0, zoom_link = 'https://zoom.us/j/santiago-ventas' WHERE name = 'Santiago'")
    cursor.execute("UPDATE collaborators SET languages = 'es,en', is_active_round_robin = 1, commission_rate = 3.0, zoom_link = 'https://zoom.us/j/alejandra-castro' WHERE name = 'Alejandra Castro'")
    cursor.execute("UPDATE collaborators SET languages = 'es,en', is_active_round_robin = 1, commission_rate = 3.0, zoom_link = 'https://zoom.us/j/sebastian-rodriguez' WHERE name = 'Sebastián Rodríguez'")
    cursor.execute("UPDATE collaborators SET is_active_round_robin = 0, commission_rate = 0.0 WHERE name IN ('Allan', 'Gonzalo', 'Daniela')")
    conn.commit()

    # Pre-populate assignment rules
    cursor.execute("SELECT COUNT(*) as count FROM assignment_rules")
    if cursor.fetchone()["count"] == 0:
        cursor.execute("SELECT id FROM collaborators WHERE name = 'Alejandra Castro'")
        row = cursor.fetchone()
        if row:
            alejandra_id = row["id"]
            cursor.execute("""
                INSERT INTO assignment_rules (field_name, field_value, agent_id, priority)
                VALUES ('origin', 'F&F', ?, 1)
            """, (alejandra_id,))
            conn.commit()

    # Pre-populate decisions
    cursor.execute("SELECT COUNT(*) as count FROM decisions")
    if cursor.fetchone()["count"] == 0:
        decs = [
            ("water_option", "Abastecimiento de Agua", "asada", "Conexión a la red de la ASADA local."),
            ("internet_option", "Conectividad Digital", "fibra", "Conexión terrestre de alta estabilidad.")
        ]
        for key, title, opt, notes in decs:
            cursor.execute("INSERT INTO decisions (key, title, selected_option, notes) VALUES (?, ?, ?, ?)", (key, title, opt, notes))
        conn.commit()
    else:
        # Migration: ensure existing selected_option values are lowercase for compatibility
        cursor.execute("UPDATE decisions SET selected_option = 'asada' WHERE key = 'water_option' AND selected_option IN ('ASADA', 'asada')")
        cursor.execute("UPDATE decisions SET selected_option = 'pozo' WHERE key = 'water_option' AND selected_option IN ('Pozo', 'pozo')")
        cursor.execute("UPDATE decisions SET selected_option = 'fibra' WHERE key = 'internet_option' AND selected_option IN ('Fibra', 'fibra')")
        cursor.execute("UPDATE decisions SET selected_option = 'starlink' WHERE key = 'internet_option' AND selected_option IN ('Starlink', 'starlink')")
        conn.commit()

    # Pre-populate decision options
    cursor.execute("SELECT COUNT(*) as count FROM decision_options")
    if cursor.fetchone()["count"] == 0:
        opts = [
            ("water_option", "asada", "ASADA", 
             "Red comunal: Menor inversión inicial, sujeta a trámites de disponibilidad con la junta local.",
             "Menor inversión inicial\nRed comunal ya existente",
             "Sujeta a trámites de disponibilidad con la junta local\nPosible desabastecimiento en temporada alta",
             "Opción convencional y recomendada inicialmente."),
            ("water_option", "pozo", "Pozo Propio", 
             "Pozo Propio: Mayor inversión (perforación/caudal), valor de activo directo y control del recurso.",
             "Control total del recurso hídrico\nValor de activo directo para el proyecto\nIndependencia de la red comunal",
             "Mayor inversión inicial (perforación/estudios/caudal)\nRiesgo de no encontrar suficiente caudal\nTrámites de concesión largos",
             "Excelente para valorizar la tierra, pero requiere estudios hidrogeológicos."),
            ("internet_option", "fibra", "Fibra Óptica", 
             "Fibra Óptica: Conexión terrestre de alta estabilidad, requiere cotización de tendido por postes.",
             "Conexión terrestre de alta estabilidad\nVelocidad simétrica constante",
             "Requiere cotización de tendido por postes\nMayor tiempo de instalación si no hay posteo",
             "Ideal para residentes de largo plazo."),
            ("internet_option", "starlink", "Starlink (Sat)", 
             "Starlink (Elon Musk): Conexión satelital inmediata, sin tendidos, pero con costos de antena fijos.",
             "Conexión satelital inmediata\nSin necesidad de tendido de postes",
             "Costos de antena y equipos fijos iniciales\nSusceptibilidad a tormentas fuertes\nLatencia ligeramente mayor que fibra",
             "Excelente alternativa rápida mientras se coordina la fibra.")
        ]
        for key, code, label, desc, pros, cons, notes in opts:
            cursor.execute("""
            INSERT INTO decision_options (decision_key, option_code, option_label, description, pros, cons, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (key, code, label, desc, pros, cons, notes))
        conn.commit()

    # Pre-populate decision options
    cursor.execute("SELECT COUNT(*) as count FROM decision_options")
    if cursor.fetchone()["count"] == 0:
        opts = [
            ("water_option", "asada", "ASADA", 
             "Red comunal: Menor inversión inicial, sujeta a trámites de disponibilidad con la junta local.",
             "Menor inversión inicial\nRed comunal ya existente",
             "Sujeta a trámites de disponibilidad con la junta local\nPosible desabastecimiento en temporada alta",
             "Opción convencional y recomendada inicialmente."),
            ("water_option", "pozo", "Pozo Propio", 
             "Pozo Propio: Mayor inversión (perforación/caudal), valor de activo directo y control del recurso.",
             "Control total del recurso hídrico\nValor de activo directo para el proyecto\nIndependencia de la red comunal",
             "Mayor inversión inicial (perforación/estudios/caudal)\nRiesgo de no encontrar suficiente caudal\nTrámites de concesión largos",
             "Excelente para valorizar la tierra, pero requiere estudios hidrogeológicos."),
            ("internet_option", "fibra", "Fibra Óptica", 
             "Fibra Óptica: Conexión terrestre de alta estabilidad, requiere cotización de tendido por postes.",
             "Conexión terrestre de alta estabilidad\nVelocidad simétrica constante",
             "Requiere cotización de tendido por postes\nMayor tiempo de instalación si no hay posteo",
             "Ideal para residentes de largo plazo."),
            ("internet_option", "starlink", "Starlink (Sat)", 
             "Starlink (Elon Musk): Conexión satelital inmediata, sin tendidos, pero con costos de antena fijos.",
             "Conexión satelital inmediata\nSin necesidad de tendido de postes",
             "Costos de antena y equipos fijos iniciales\nSusceptibilidad a tormentas fuertes\nLatencia ligeramente mayor que fibra",
             "Excelente alternativa rápida mientras se coordina la fibra.")
        ]
        for key, code, label, desc, pros, cons, notes in opts:
            cursor.execute("""
            INSERT INTO decision_options (decision_key, option_code, option_label, description, pros, cons, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (key, code, label, desc, pros, cons, notes))
        conn.commit()

    # Pre-populate default Gantt tasks if empty (Las Lomas Development Roadmap)
    cursor.execute("SELECT COUNT(*) as count FROM tasks")
    if cursor.fetchone()["count"] == 0:
        default_tasks = [
            ("Compra del Lote", "Firma de Opción de Compra", "Firma de contrato de opción de compra-venta del terreno del proyecto.", "2026-05-01", "2026-05-30", "Completado", 100, None),
            ("Regulación y Permisos", "Viabilidad Ambiental SETENA D1", "Viabilidad ambiental D1 ya aprobada y en firme.", "2026-06-01", "2026-08-01", "Completado", 100, None),
            ("Regulación y Permisos", "Concesión de Agua y Pozos Aprobados", "Trámite e inscripción de pozos y concesiones hídricas.", "2026-07-01", "2026-08-20", "En Proceso", 90, None),
            ("Planificación y Diseño", "Diseño Conceptual del Masterplan (150 lotes)", "Plan maestro preliminar de distribución de lotes.", "2026-08-01", "2026-09-15", "En Proceso", 45, None),
            ("Regulación y Permisos", "Planos de Catastro e Inscripción de Segregaciones", "Visado catastral de segregaciones de los lotes.", "2026-09-16", "2026-11-15", "Pendiente", 0, 4),
            ("Planificación y Diseño", "Diseño Técnico de Vialidad e Hidráulica", "Ingeniería de escorrentías e infraestructura vial.", "2026-09-16", "2026-10-31", "Pendiente", 0, 4),
            ("Planificación y Diseño", "Diseño de Áreas Comunes y Amenidades (Club del Río)", "Concepto de casa club y miradores de montaña.", "2026-10-01", "2026-11-30", "Pendiente", 0, 4),
            ("Regulación y Permisos", "Permisos Municipales de Construcción", "Obtención de licencia municipal para movimiento de tierra y calles.", "2026-11-16", "2027-01-15", "Pendiente", 0, 5),
            ("Mercadeo y Ventas", "Publicación de Landing Page & Brochure de Inversión", "Sitio web de aterrizaje y brochure de pre-venta digital.", "2026-08-10", "2026-08-25", "En Proceso", 80, None),
            ("Mercadeo y Ventas", "Configuración del CRM y Canales de Captación", "Integración de leads y control de prospectos sin contestar.", "2026-08-14", "2026-08-31", "En Proceso", 20, None),
            ("Mercadeo y Ventas", "Lanzamiento Oficial de Pre-venta (Fase 1: 30 lotes)", "Inicio formal de colocación de lotes a clientes VIP.", "2026-09-01", "2026-12-31", "Pendiente", 0, 9),
            ("Construcción e Infraestructura", "Movimiento de Tierras y Caminos Internos", "Excavación y conformación de calles internas.", "2027-01-20", "2027-04-30", "Pendiente", 0, 8),
            ("Construcción e Infraestructura", "Red de Agua Potable y Conexiones", "Instalación de tuberías subterráneas y tomas.", "2027-03-01", "2027-05-31", "Pendiente", 0, 12),
            ("Construcción e Infraestructura", "Canalización Eléctrica y Alumbrado", "Posteado y tendido de líneas eléctricas secundarias.", "2027-04-01", "2027-06-30", "Pendiente", 0, 12),
            ("Construcción e Infraestructura", "Construcción de Amenidades (Club y Senderos)", "Edificación de la zona social y senderismo.", "2027-05-01", "2027-08-31", "Pendiente", 0, 12),
            ("Entrega y Cierre", "Firma de Escrituras y Cierre de Ventas (Fase 1)", "Traspaso notarial oficial de los primeros lotes a compradores.", "2027-06-01", "2027-09-30", "Pendiente", 0, 13),
            ("Entrega y Cierre", "Entrega Física de Lotes a Propietarios", "Handover de lotes listos para construir.", "2027-10-01", "2027-11-30", "Pendiente", 0, 16)
        ]
        for phase, title, desc, start_date, due_date, status, progress, pred in default_tasks:
            cursor.execute("""
            INSERT INTO tasks (phase, title, description, start_date, due_date, status, progress, predecessor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (phase, title, desc, start_date, due_date, status, progress, pred))
        conn.commit()
        
    conn.close()

def get_decisions_data(cursor):
    cursor.execute("SELECT * FROM decisions ORDER BY sort_order ASC")
    decisions_list = []
    for d_row in cursor.fetchall():
        key = d_row["key"]
        cursor.execute("SELECT * FROM decision_options WHERE decision_key = ? ORDER BY id ASC", (key,))
        options = [dict(row) for row in cursor.fetchall()]
        decisions_list.append({
            "id": d_row["id"],
            "key": key,
            "title": d_row["title"],
            "selected_option": d_row["selected_option"],
            "notes": d_row["notes"],
            "lane": d_row["lane"],
            "sort_order": d_row["sort_order"],
            "options": options
        })
    return decisions_list

def filter_tasks_by_decisions(tasks_list, cursor):
    cursor.execute("SELECT option_code, decision_key FROM decision_options")
    option_to_decision = {row["option_code"].lower(): row["decision_key"] for row in cursor.fetchall()}
    
    cursor.execute("SELECT key, selected_option FROM decisions")
    selected_decisions = {row["key"]: (row["selected_option"].lower() if row["selected_option"] else "") for row in cursor.fetchall()}
    
    filtered = []
    for t in tasks_list:
        dpath = t.get("decision_path")
        if dpath and dpath != "core":
            dpath_lower = dpath.lower()
            dkey = option_to_decision.get(dpath_lower)
            if dkey:
                selected_val = selected_decisions.get(dkey)
                if selected_val and dpath_lower != selected_val:
                    continue
        filtered.append(t)
    return filtered

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

# Helper to record page views
def record_page_view(page_path: str):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO page_views (page_path) VALUES (?)", (page_path,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error recording page view: {e}")

# ----------------- PUBLIC WEBSITE ROUTES -----------------

@app.get("/")
def read_root():
    record_page_view("home")
    return FileResponse("website/index.html")

@app.get("/residential")
def read_residential():
    record_page_view("residential")
    return FileResponse("website/residential.html")

@app.get("/agrihood")
def read_agrihood():
    record_page_view("agrihood")
    return FileResponse("website/agrihood.html")

@app.get("/zone")
def read_zone():
    record_page_view("zone")
    return FileResponse("website/zone.html")

@app.get("/contact")
def read_contact():
    record_page_view("contact")
    return FileResponse("website/contact.html")

@app.get("/styles.css")
def read_styles():
    return FileResponse("website/styles.css")

@app.get("/robots.txt")
def read_robots():
    return FileResponse("website/robots.txt")

@app.get("/sitemap.xml")
def read_sitemap():
    return FileResponse("website/sitemap.xml", media_type="application/xml")


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
    all_tasks = [dict(row) for row in cursor.fetchall()]
    filtered_all_tasks = filter_tasks_by_decisions(all_tasks, cursor)
    
    total_tasks = len(filtered_all_tasks)
    project_progress = 0.0
    if total_tasks > 0:
        total_progress = sum(t["progress"] for t in filtered_all_tasks)
        project_progress = total_progress / total_tasks
        
    cursor.execute("""
    SELECT * FROM tasks 
    WHERE status IN ('Pendiente', 'En Proceso') 
    ORDER BY due_date ASC
    """)
    upcoming_tasks_raw = [dict(row) for row in cursor.fetchall()]
    upcoming_tasks = filter_tasks_by_decisions(upcoming_tasks_raw, cursor)[:5]
    
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
    decisions_list = get_decisions_data(cursor)
    
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
    
    return templates.TemplateResponse(request, "dashboard.html", {
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
        "decisions": decisions,
        "decisions_list": decisions_list
    })

@app.get("/admin/crm")
def admin_crm(request: Request, filter: str = "all"):
    conn = get_db()
    cursor = conn.cursor()
    
    # Get pending count for topbar
    cursor.execute("SELECT COUNT(*) as pending FROM leads WHERE replied = 0")
    pending_leads_count = cursor.fetchone()["pending"]
    
    # Query leads
    cursor.execute("""
        SELECT l.*, c.name as agent_name 
        FROM leads l
        LEFT JOIN collaborators c ON l.assigned_agent_id = c.id
        ORDER BY l.id DESC
    """)
    leads_rows = cursor.fetchall()
    leads_list = [dict(row) for row in leads_rows]
    leads_json = json.dumps(leads_list, default=str)
    
    # Query collaborators
    cursor.execute("SELECT * FROM collaborators ORDER BY name ASC")
    collaborators = [dict(row) for row in cursor.fetchall()]
    
    # Query assignment rules
    cursor.execute("""
        SELECT r.*, c.name as agent_name 
        FROM assignment_rules r
        LEFT JOIN collaborators c ON r.agent_id = c.id
        ORDER BY r.priority ASC, r.id ASC
    """)
    rules = [dict(row) for row in cursor.fetchall()]
    
    # Query meetings
    cursor.execute("""
        SELECT m.*, l.name as lead_name, l.phone as lead_phone, l.email as lead_email, c.name as agent_name
        FROM meetings m
        JOIN leads l ON m.lead_id = l.id
        LEFT JOIN collaborators c ON l.assigned_agent_id = c.id
        ORDER BY m.scheduled_at DESC
    """)
    meetings = [dict(row) for row in cursor.fetchall()]
    meetings_json = json.dumps(meetings, default=str)
    
    conn.close()
    
    exchange_rate = get_exchange_rate()
    
    return templates.TemplateResponse(request, "crm.html", {
        "active_page": "crm",
        "filter_mode": filter,
        "pending_leads_count": pending_leads_count,
        "leads": leads_list,
        "leads_json": leads_json,
        "collaborators": collaborators,
        "rules": rules,
        "meetings": meetings,
        "meetings_json": meetings_json,
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
            # Replace ":" with "-", "," with space, "&" with "y", and double quotes with single quotes
            clean_title = t["title"].replace(":", "-").replace(",", " ").replace("&", "y").replace('"', "'")
            
            status_tag = ""
            if t["status"] == "Completado":
                status_tag = "done"
            elif t["status"] == "En Proceso":
                status_tag = "active"
                
            tag = f"t{t['id']}"
            start = t["start_date"] or "2026-08-14"
            due = t["due_date"] or "2026-09-14"
            
            tag_section = f"{status_tag}, {tag}" if status_tag else tag
            
            # Predecessors logic in Mermaid
            if t["predecessor"]:
                line = f'        "{clean_title}" :{tag_section}, after t{t["predecessor"]}, {due}'
            else:
                line = f'        "{clean_title}" :{tag_section}, {start}, {due}'
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
    decisions_list = get_decisions_data(cursor)
    
    # Fetch all tasks and filter by active decisions
    cursor.execute("SELECT * FROM tasks ORDER BY start_date ASC")
    all_tasks = [dict(row) for row in cursor.fetchall()]
    tasks_list = filter_tasks_by_decisions(all_tasks, cursor)

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
    
    return templates.TemplateResponse(request, "gantt.html", {
        "active_page": "gantt",
        "pending_leads_count": pending_leads_count,
        "grouped_tasks": grouped_tasks,
        "mermaid_code": mermaid_code,
        "all_tasks_list": all_tasks_list,
        "tasks_json": tasks_json,
        "exchange_rate": exchange_rate,
        "collaborators": collaborators_list,
        "decisions": decisions,
        "decisions_list": decisions_list
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
    
    return templates.TemplateResponse(request, "finance.html", {
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

@app.get("/admin/decisions")
def admin_decisions(request: Request):
    conn = get_db()
    cursor = conn.cursor()
    
    # Get pending leads count
    cursor.execute("SELECT COUNT(*) as pending FROM leads WHERE replied = 0")
    pending_leads_count = cursor.fetchone()["pending"]
    
    # Get decisions list
    decisions_list = get_decisions_data(cursor)
    
    conn.close()
    
    exchange_rate = get_exchange_rate()
    
    return templates.TemplateResponse(request, "decisions.html", {
        "active_page": "decisions",
        "pending_leads_count": pending_leads_count,
        "decisions_list": decisions_list,
        "exchange_rate": exchange_rate
    })

# ----------------- API / POST ENDPOINTS -----------------

# Helper to set transition timestamps based on lead status
def set_transition_timestamp(cursor, lead_id, status):
    if not status:
        return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status_lower = status.strip().lower()
    
    if status_lower in ["contactado", "contacted"]:
        cursor.execute("UPDATE leads SET contacted_at = COALESCE(contacted_at, ?) WHERE id = ?", (now_str, lead_id))
    elif status_lower in ["meeting", "reunión", "reunion"]:
        cursor.execute("UPDATE leads SET meeting_at = COALESCE(meeting_at, ?) WHERE id = ?", (now_str, lead_id))
    elif status_lower in ["visita", "visit"]:
        cursor.execute("UPDATE leads SET visited_at = COALESCE(visited_at, ?) WHERE id = ?", (now_str, lead_id))
    elif status_lower in ["hot lead", "caliente", "reservado", "reserva"]:
        cursor.execute("UPDATE leads SET reserved_at = COALESCE(reserved_at, ?) WHERE id = ?", (now_str, lead_id))
    elif status_lower in ["ganado", "sold", "cierre", "cerrado"]:
        cursor.execute("UPDATE leads SET closed_at = COALESCE(closed_at, ?) WHERE id = ?", (now_str, lead_id))

# Assignment Rules & Round Robin Engine
def run_lead_assignment(cursor, lead_id, origin, language, budget="DONT_KNOW"):
    # 1. Custom assignment rules checking
    # Check origin rule
    cursor.execute("""
        SELECT agent_id FROM assignment_rules 
        WHERE is_active = 1 AND field_name = 'origin' AND LOWER(field_value) = LOWER(?)
        ORDER BY priority ASC, id ASC
    """, (origin,))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE leads SET assigned_agent_id = ? WHERE id = ?", (row["agent_id"], lead_id))
        return row["agent_id"]
        
    # Check language rule
    cursor.execute("""
        SELECT agent_id FROM assignment_rules 
        WHERE is_active = 1 AND field_name = 'language' AND LOWER(field_value) = LOWER(?)
        ORDER BY priority ASC, id ASC
    """, (language,))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE leads SET assigned_agent_id = ? WHERE id = ?", (row["agent_id"], lead_id))
        return row["agent_id"]
        
    # Check budget rule
    cursor.execute("""
        SELECT agent_id FROM assignment_rules 
        WHERE is_active = 1 AND field_name = 'budget' AND LOWER(field_value) = LOWER(?)
        ORDER BY priority ASC, id ASC
    """, (budget,))
    row = cursor.fetchone()
    if row:
        cursor.execute("UPDATE leads SET assigned_agent_id = ? WHERE id = ?", (row["agent_id"], lead_id))
        return row["agent_id"]

    # 2. Fall back to Round-Robin by language
    cursor.execute("""
        SELECT id, name FROM collaborators
        WHERE is_active_round_robin = 1 AND (languages LIKE ? OR languages LIKE ?)
        ORDER BY last_assigned_at ASC, id ASC
    """, (f"%{language}%", f"%{language}%"))
    agents = cursor.fetchall()
    
    if not agents:
        # Fallback to any active round-robin agent
        cursor.execute("""
            SELECT id, name FROM collaborators
            WHERE is_active_round_robin = 1
            ORDER BY last_assigned_at ASC, id ASC
        """)
        agents = cursor.fetchall()
        
    if agents:
        selected_agent = agents[0]
        agent_id = selected_agent["id"]
        now_str = datetime.now().isoformat()
        cursor.execute("UPDATE collaborators SET last_assigned_at = ? WHERE id = ?", (now_str, agent_id))
        cursor.execute("UPDATE leads SET assigned_agent_id = ? WHERE id = ?", (agent_id, lead_id))
        return agent_id
        
    return None

# ----------------- API / POST ENDPOINTS -----------------

class LeadPayload(BaseModel):
    name: str = "Interesado Web"
    email: str
    phone: str = ""
    origin: str = "Website Landing"
    details: str = ""
    status: str = "Nuevo"
    language: str = "es"
    origin_custom_label: str = ""

@app.post("/api/leads")
def api_create_lead(payload: LeadPayload):
    # This endpoint receives leads from the public landing page via Fetch
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
    INSERT INTO leads (name, email, phone, origin, details, status, replied, language, origin_custom_label)
    VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
    """, (payload.name, payload.email, payload.phone, payload.origin, payload.details, payload.status, payload.language, payload.origin_custom_label))
    
    lead_id = cursor.lastrowid
    run_lead_assignment(cursor, lead_id, payload.origin, payload.language)
    set_transition_timestamp(cursor, lead_id, payload.status)
    
    conn.commit()
    conn.close()
    print(f"[MOCK EMAIL] Confirmation email successfully sent to {payload.email} for Send Message")
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
    origin: str = Form("directo"),
    origin_custom_label: str = Form(None),
    status_val: str = Form("Nuevo", alias="status"),
    replied: int = Form(0),
    assigned_agent_id: str = Form("auto"),
    language: str = Form("es"),
    lead_temperature: str = Form("frio"),
    deal_value: float = Form(0.0),
    details: str = Form(None)
):
    conn = get_db()
    cursor = conn.cursor()
    
    agent_id = None
    if assigned_agent_id != "auto" and assigned_agent_id:
        agent_id = int(assigned_agent_id)
        
    cursor.execute("""
    INSERT INTO leads (name, email, phone, origin, origin_custom_label, status, replied, assigned_agent_id, language, lead_temperature, deal_value, details)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (name, email, phone, origin, origin_custom_label, status_val, replied, agent_id, language, lead_temperature, deal_value, details))
    
    lead_id = cursor.lastrowid
    
    if assigned_agent_id == "auto":
        run_lead_assignment(cursor, lead_id, origin, language)
        
    set_transition_timestamp(cursor, lead_id, status_val)
    
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
    origin_custom_label: str = Form(None),
    status_val: str = Form(..., alias="status"),
    replied: int = Form(0),
    assigned_agent_id: str = Form("auto"),
    language: str = Form("es"),
    lead_temperature: str = Form("frio"),
    deal_value: float = Form(0.0),
    details: str = Form(None)
):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT status, assigned_agent_id FROM leads WHERE id = ?", (id,))
    curr = cursor.fetchone()
    old_status = curr["status"] if curr else None
    
    agent_id = None
    if assigned_agent_id != "auto" and assigned_agent_id != "" and assigned_agent_id is not None:
        agent_id = int(assigned_agent_id)
    elif assigned_agent_id == "auto":
        agent_id = run_lead_assignment(cursor, id, origin, language)
    else:
        agent_id = curr["assigned_agent_id"] if curr else None

    cursor.execute("""
    UPDATE leads 
    SET name = ?, email = ?, phone = ?, origin = ?, origin_custom_label = ?, status = ?, replied = ?, 
        assigned_agent_id = ?, language = ?, lead_temperature = ?, deal_value = ?, details = ?
    WHERE id = ?
    """, (name, email, phone, origin, origin_custom_label, status_val, replied, agent_id, language, lead_temperature, deal_value, details, id))
    
    if old_status != status_val:
        set_transition_timestamp(cursor, id, status_val)
        
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/crm", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/leads/{id}/update-status")
def api_update_lead_status(id: int, status_val: str = Form(...), deal_value: float = Form(0.0)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM leads WHERE id = ?", (id,))
    row = cursor.fetchone()
    old_status = row["status"] if row else None
    
    cursor.execute("UPDATE leads SET status = ?, deal_value = ? WHERE id = ?", (status_val, deal_value, id))
    
    if old_status != status_val:
        set_transition_timestamp(cursor, id, status_val)
        
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Estado del lead {id} actualizado a {status_val}."}

@app.post("/api/leads/{id}/delete")
def delete_lead(id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM leads WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/crm", status_code=status.HTTP_303_SEE_OTHER)

# --- Meetings Booking & Management ---

class BookMeetingPayload(BaseModel):
    name: str
    email: str
    phone: str = ""
    language: str = "es"
    datetime: str
    topic: str = ""
    budget_prequalification: str
    origin: str = "directo"

@app.post("/api/meetings/book")
def api_book_meeting(payload: BookMeetingPayload):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, status, details FROM leads WHERE email = ?", (payload.email,))
    lead_row = cursor.fetchone()
    
    prequal_text = {
        "YES": "SÍ (Lotes de $190k dentro del presupuesto)",
        "FINANCING": "Necesitaré financiación",
        "DONT_KNOW": "No lo sé / Por conversar"
    }.get(payload.budget_prequalification, payload.budget_prequalification)
    
    details_str = f"Reunión agendada vía web.\n¿Qué quiere conversar?: {payload.topic}\nPrecalificación Presupuesto: {prequal_text}"
    
    if lead_row:
        lead_id = lead_row["id"]
        updated_details = (lead_row["details"] or "") + "\n\n" + details_str
        cursor.execute("""
            UPDATE leads 
            SET name = ?, phone = ?, language = ?, details = ?, status = 'Meeting'
            WHERE id = ?
        """, (payload.name, payload.phone, payload.language, updated_details, lead_id))
    else:
        cursor.execute("""
            INSERT INTO leads (name, email, phone, origin, status, language, details, lead_temperature)
            VALUES (?, ?, ?, ?, 'Meeting', ?, ?, 'tibio')
        """, (payload.name, payload.email, payload.phone, payload.origin, payload.language, details_str))
        lead_id = cursor.lastrowid
        
    agent_id = run_lead_assignment(cursor, lead_id, payload.origin, payload.language, payload.budget_prequalification)
    
    zoom_link = "https://zoom.us/j/laslomas"
    agent_name = "Ventas Las Lomas"
    if agent_id:
        cursor.execute("SELECT name, zoom_link FROM collaborators WHERE id = ?", (agent_id,))
        agent_row = cursor.fetchone()
        if agent_row:
            agent_name = agent_row["name"]
            zoom_link = agent_row["zoom_link"] or zoom_link
            
    cursor.execute("""
        INSERT INTO meetings (lead_id, title, scheduled_at, language, zoom_link, status)
        VALUES (?, ?, ?, ?, ?, 'scheduled')
    """, (lead_id, f"Reunión Inicial con {payload.name}", payload.datetime, payload.language, zoom_link))
    
    set_transition_timestamp(cursor, lead_id, "Meeting")
    
    conn.commit()
    conn.close()
    print(f"[MOCK EMAIL] Confirmation email successfully sent to {payload.email} for Meeting Booking on {payload.datetime}")
    
    return {
        "status": "success",
        "message": "Reunión agendada exitosamente.",
        "agent_name": agent_name,
        "zoom_link": zoom_link,
        "datetime": payload.datetime
    }

@app.post("/api/meetings/{meeting_id}/summarize")
def api_summarize_meeting(meeting_id: int, notes: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    
    gemini_key = os.getenv("GEMINI_API_KEY")
    summary = ""
    follow_up_plan = ""
    
    summary_fallback = f"Resumen generado a partir de las notas de la reunión:\n"
    notes_lower = notes.lower()
    budget_found = "No especificado"
    if "presupuesto" in notes_lower or "budget" in notes_lower or "$" in notes_lower:
        budget_found = "Mencionado en la nota"
    lote_found = "No especificado"
    if "lote" in notes_lower or "lot" in notes_lower:
        lote_found = "Interesado en terrenos"
        
    summary_fallback += f"- Presupuesto: {budget_found}\n- Interés: {lote_found}\n- Notas crudas: {notes[:200]}..."
    follow_up_plan_fallback = "- Contactar al cliente en 3 días para dar seguimiento.\n- Compartir los planos de segregación."

    if gemini_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
            prompt = (
                f"Analiza la siguiente nota de reunión de un cliente de bienes raíces (Las Lomas) y genera dos campos en formato JSON:\n"
                f"1. 'summary': Un resumen de 2-3 oraciones que contenga qué quiere el cliente, presupuesto y lote de interés.\n"
                f"2. 'follow_up': Un plan de seguimiento con 2-3 puntos accionables.\n\n"
                f"Notas de la reunión:\n{notes}\n\n"
                f"Devuelve SOLO el JSON sin etiquetas de markdown."
            )
            req_data = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
            req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as response:
                res_body = json.loads(response.read().decode())
                text_out = res_body["candidates"][0]["content"]["parts"][0]["text"].strip()
                if text_out.startswith("```json"):
                    text_out = text_out.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(text_out)
                summary = parsed.get("summary", summary_fallback)
                follow_up_plan = parsed.get("follow_up", follow_up_plan_fallback)
                if isinstance(follow_up_plan, list):
                    follow_up_plan = "\n".join([f"- {item}" for item in follow_up_plan])
        except Exception as e:
            print(f"Failed to use Gemini API for meeting summary: {e}")
            summary = summary_fallback
            follow_up_plan = follow_up_plan_fallback
    else:
        summary = summary_fallback
        follow_up_plan = follow_up_plan_fallback

    cursor.execute("""
        UPDATE meetings 
        SET notes = ?, summary = ?, follow_up_plan = ?, status = 'completed'
        WHERE id = ?
    """, (notes, summary, follow_up_plan, meeting_id))
    
    cursor.execute("SELECT lead_id FROM meetings WHERE id = ?", (meeting_id,))
    m_row = cursor.fetchone()
    lead_id = m_row["lead_id"] if m_row else None
    
    conn.commit()
    conn.close()
    
    return {
        "status": "success",
        "summary": summary,
        "follow_up_plan": follow_up_plan,
        "lead_id": lead_id
    }

@app.post("/api/meetings/new")
def api_create_meeting_manual(
    lead_id: int = Form(...),
    title: str = Form(...),
    scheduled_at: str = Form(...),
    language: str = Form("es"),
    zoom_link: str = Form(None)
):
    conn = get_db()
    cursor = conn.cursor()
    if not zoom_link:
        cursor.execute("SELECT zoom_link FROM collaborators WHERE id = (SELECT assigned_agent_id FROM leads WHERE id = ?)", (lead_id,))
        row = cursor.fetchone()
        zoom_link = row["zoom_link"] if row else "https://zoom.us/j/laslomas"
        
    cursor.execute("""
        INSERT INTO meetings (lead_id, title, scheduled_at, language, zoom_link, status)
        VALUES (?, ?, ?, ?, ?, 'scheduled')
    """, (lead_id, title, scheduled_at, language, zoom_link))
    
    set_transition_timestamp(cursor, lead_id, "Meeting")
    
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/crm", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/meetings/{id}/edit")
def api_edit_meeting(
    id: int,
    title: str = Form(...),
    scheduled_at: str = Form(...),
    status_val: str = Form(..., alias="status"),
    zoom_link: str = Form(...),
    notes: str = Form(None),
    summary: str = Form(None),
    follow_up_plan: str = Form(None)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE meetings
        SET title = ?, scheduled_at = ?, status = ?, zoom_link = ?, notes = ?, summary = ?, follow_up_plan = ?
        WHERE id = ?
    """, (title, scheduled_at, status_val, zoom_link, notes, summary, follow_up_plan, id))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/crm", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/meetings/{id}/delete")
def api_delete_meeting(id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM meetings WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/crm", status_code=status.HTTP_303_SEE_OTHER)

# --- Assignment Rules & Agents Config API ---

@app.post("/api/assignment-rules/new")
def create_assignment_rule(
    field_name: str = Form(...),
    field_value: str = Form(...),
    agent_id: int = Form(...),
    priority: int = Form(0)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO assignment_rules (field_name, field_value, agent_id, priority, is_active)
        VALUES (?, ?, ?, ?, 1)
    """, (field_name, field_value, agent_id, priority))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/crm", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/assignment-rules/{id}/delete")
def delete_assignment_rule(id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM assignment_rules WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/crm", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/crm/collaborators/new")
def create_collaborator_crm(
    name: str = Form(...),
    role: str = Form(...),
    languages: str = Form("es,en"),
    is_active_round_robin: int = Form(1),
    commission_rate: float = Form(3.0),
    zoom_link: str = Form(None)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO collaborators (name, role, languages, is_active_round_robin, commission_rate, zoom_link)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (name, role, languages, is_active_round_robin, commission_rate, zoom_link))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/crm", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/collaborators/{id}/edit")
def edit_collaborator_crm(
    id: int,
    name: str = Form(...),
    role: str = Form(...),
    languages: str = Form("es,en"),
    is_active_round_robin: int = Form(1),
    commission_rate: float = Form(3.0),
    zoom_link: str = Form(None)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE collaborators
        SET name = ?, role = ?, languages = ?, is_active_round_robin = ?, commission_rate = ?, zoom_link = ?
        WHERE id = ?
    """, (name, role, languages, is_active_round_robin, commission_rate, zoom_link, id))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/crm", status_code=status.HTTP_303_SEE_OTHER)

# --- CRM Stats API ---

@app.get("/api/crm/stats")
def api_crm_stats():
    conn = get_db()
    cursor = conn.cursor()
    
    now = datetime.now()
    
    # Esta semana (Lunes 00:00:00 a hoy)
    start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Semana anterior (Lunes anterior 00:00:00 al Lunes actual 00:00:00)
    start_of_prev_week = start_of_week - timedelta(days=7)
    end_of_prev_week = start_of_week
    end_day_prev_week = start_of_week - timedelta(days=1)
    
    # Este mes (1° del mes actual 00:00:00 a hoy)
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Mes anterior (1° del mes pasado al 1° del mes actual)
    last_day_prev_month = start_of_month - timedelta(days=1)
    start_of_prev_month = last_day_prev_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_of_prev_month = start_of_month
    
    # Este año / YTD (1 de Enero 00:00:00 a hoy)
    start_of_year = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Año anterior (1 Ene año anterior al 1 Ene año actual)
    start_of_prev_year = datetime(now.year - 1, 1, 1, 0, 0, 0, 0)
    end_of_prev_year = start_of_year
    
    # Últimos 12 meses (móvil)
    start_of_annual = now - timedelta(days=365)
    
    month_names = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    month_short = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    
    periods = {
        "week": {
            "label": "Esta Semana",
            "date_range": f"{start_of_week.strftime('%d')} {month_short[start_of_week.month-1]} - {now.strftime('%d')} {month_short[now.month-1]} {now.year}",
            "start": start_of_week,
            "end": None
        },
        "prev_week": {
            "label": "Semana Anterior",
            "date_range": f"{start_of_prev_week.strftime('%d')} {month_short[start_of_prev_week.month-1]} - {end_day_prev_week.strftime('%d')} {month_short[end_day_prev_week.month-1]} {end_day_prev_week.year}",
            "start": start_of_prev_week,
            "end": end_of_prev_week
        },
        "month": {
            "label": "Este Mes",
            "date_range": f"{month_names[now.month-1]} {now.year}",
            "start": start_of_month,
            "end": None
        },
        "prev_month": {
            "label": "Mes Anterior",
            "date_range": f"{month_names[start_of_prev_month.month-1]} {start_of_prev_month.year}",
            "start": start_of_prev_month,
            "end": end_of_prev_month
        },
        "ytd": {
            "label": "Este Año (YTD)",
            "date_range": f"1 Ene - {now.strftime('%d')} {month_short[now.month-1]} {now.year}",
            "start": start_of_year,
            "end": None
        },
        "prev_year": {
            "label": "Año Anterior",
            "date_range": f"Año {now.year - 1}",
            "start": start_of_prev_year,
            "end": end_of_prev_year
        },
        "annual": {
            "label": "Últimos 12 Meses",
            "date_range": f"{start_of_annual.strftime('%d/%m/%Y')} - {now.strftime('%d/%m/%Y')}",
            "start": start_of_annual,
            "end": None
        },
        "all": {
            "label": "Histórico Total",
            "date_range": "Todo el registro",
            "start": None,
            "end": None
        }
    }
    
    stats = {}
    
    for period_name, p_info in periods.items():
        start_date = p_info["start"]
        end_date = p_info["end"]
        
        def build_clause(col, extra=""):
            clauses = []
            params = []
            if start_date:
                clauses.append(f"{col} >= ?")
                params.append(start_date.strftime("%Y-%m-%d %H:%M:%S"))
            if end_date:
                clauses.append(f"{col} < ?")
                params.append(end_date.strftime("%Y-%m-%d %H:%M:%S"))
            if extra:
                clauses.append(extra)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            return where, params
        
        w_views, p_views = build_clause("viewed_at")
        cursor.execute(f"SELECT COUNT(*) as count FROM page_views{w_views}", p_views)
        views = cursor.fetchone()["count"]
        
        w_leads, p_leads = build_clause("created_at")
        cursor.execute(f"SELECT COUNT(*) as count FROM leads{w_leads}", p_leads)
        leads = cursor.fetchone()["count"]
        
        w_m_comp, p_m_comp = build_clause("scheduled_at", "status = 'completed'")
        cursor.execute(f"SELECT COUNT(*) as count FROM meetings{w_m_comp}", p_m_comp)
        meetings_realizadas = cursor.fetchone()["count"]
        
        w_m_canc, p_m_canc = build_clause("scheduled_at", "status = 'cancelled'")
        cursor.execute(f"SELECT COUNT(*) as count FROM meetings{w_m_canc}", p_m_canc)
        meetings_canceladas = cursor.fetchone()["count"]
        
        w_vis, p_vis = build_clause("visited_at", "visited_at IS NOT NULL")
        cursor.execute(f"SELECT COUNT(*) as count FROM leads{w_vis}", p_vis)
        visitas = cursor.fetchone()["count"]
        
        w_res, p_res = build_clause("reserved_at", "reserved_at IS NOT NULL")
        cursor.execute(f"SELECT COUNT(*) as count FROM leads{w_res}", p_res)
        reservas = cursor.fetchone()["count"]
        
        w_cls, p_cls = build_clause("closed_at", "closed_at IS NOT NULL AND status = 'Ganado'")
        cursor.execute(f"SELECT COUNT(*) as count, SUM(deal_value) as total_value FROM leads{w_cls}", p_cls)
        closure_row = cursor.fetchone()
        cierres_count = closure_row["count"] or 0
        cierres_value = closure_row["total_value"] or 0.0
        
        stats[period_name] = {
            "label": p_info["label"],
            "date_range": p_info["date_range"],
            "views": views,
            "leads": leads,
            "meetings_realizadas": meetings_realizadas,
            "meetings_canceladas": meetings_canceladas,
            "visitas": visitas,
            "reservas": reservas,
            "cierres_count": cierres_count,
            "cierres_value": cierres_value
        }
        
    cursor.execute("""
        SELECT created_at, contacted_at, meeting_at, visited_at, reserved_at, closed_at 
        FROM leads
    """)
    leads_rows = cursor.fetchall()
    
    velocity = {
        "to_contacted": 0.0,
        "to_meeting": 0.0,
        "to_visited": 0.0,
        "to_reserved": 0.0,
        "to_closed": 0.0
    }
    
    counts = {k: 0 for k in velocity.keys()}
    sums = {k: 0.0 for k in velocity.keys()}
    
    for row in leads_rows:
        try:
            created = datetime.strptime(row["created_at"].split(".")[0], "%Y-%m-%d %H:%M:%S")
        except:
            continue
            
        for stage, col in [("to_contacted", "contacted_at"), ("to_meeting", "meeting_at"), 
                           ("to_visited", "visited_at"), ("to_reserved", "reserved_at"), ("to_closed", "closed_at")]:
            val = row[col]
            if val:
                try:
                    ts = datetime.strptime(val.split(".")[0], "%Y-%m-%d %H:%M:%S")
                    diff_days = (ts - created).total_seconds() / 86400.0
                    if diff_days >= 0:
                        sums[stage] += diff_days
                        counts[stage] += 1
                except:
                    pass
                    
    for k in velocity.keys():
        if counts[k] > 0:
            velocity[k] = round(sums[k] / counts[k], 2)
            
    current_year = now.year
    monthly_funnel = []
    month_names = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    
    for m in range(1, 13):
        m_start = datetime(current_year, m, 1)
        if m == 12:
            m_end = datetime(current_year + 1, 1, 1)
        else:
            m_end = datetime(current_year, m + 1, 1)
            
        start_str = m_start.strftime("%Y-%m-%d 00:00:00")
        end_str = m_end.strftime("%Y-%m-%d 00:00:00")
        
        cursor.execute("SELECT COUNT(*) as count FROM leads WHERE created_at >= ? AND created_at < ?", (start_str, end_str))
        leads_created = cursor.fetchone()["count"]
        
        cursor.execute("SELECT COUNT(*) as count FROM leads WHERE contacted_at >= ? AND contacted_at < ?", (start_str, end_str))
        leads_contacted = cursor.fetchone()["count"]
        
        cursor.execute("SELECT COUNT(*) as count FROM meetings WHERE scheduled_at >= ? AND scheduled_at < ?", (start_str, end_str))
        meetings_count = cursor.fetchone()["count"]
        
        cursor.execute("SELECT COUNT(*) as count FROM leads WHERE visited_at >= ? AND visited_at < ?", (start_str, end_str))
        visits_count = cursor.fetchone()["count"]
        
        cursor.execute("SELECT COUNT(*) as count FROM leads WHERE reserved_at >= ? AND reserved_at < ?", (start_str, end_str))
        reservations_count = cursor.fetchone()["count"]
        
        cursor.execute("SELECT COUNT(*) as count, SUM(deal_value) as value FROM leads WHERE closed_at >= ? AND closed_at < ?", (start_str, end_str))
        closed_row = cursor.fetchone()
        closures_count = closed_row["count"] or 0
        closures_value = closed_row["value"] or 0.0
        
        monthly_funnel.append({
            "month_num": m,
            "month_name": month_names[m - 1],
            "created": leads_created,
            "contacted": leads_contacted,
            "meetings": meetings_count,
            "visits": visits_count,
            "reservas": reservations_count,
            "closures_count": closures_count,
            "closures_value": closures_value
        })

    cursor.execute("""
        SELECT marketing_channel, SUM(amount_usd) as total_spend
        FROM finances
        WHERE type = 'Gasto' AND category = 'Marketing' AND marketing_channel IS NOT NULL
        GROUP BY marketing_channel
    """)
    spend_rows = cursor.fetchall()
    channel_spends = {r["marketing_channel"]: r["total_spend"] for r in spend_rows}
    
    standard_origins = [
        "Meta organico", "Meta Pago",
        "Google organico", "Google pago",
        "Youtube organico", "Youtube pago",
        "Motores de IA", "Base de datos",
        "F&F", "directo", "Otros"
    ]
    
    cursor.execute("""
        SELECT origin, COUNT(*) as count,
               SUM(CASE WHEN status = 'Ganado' THEN 1 ELSE 0 END) as closures
        FROM leads
        GROUP BY origin
    """)
    origin_rows = cursor.fetchall()
    
    marketing_report = []
    processed_origins = set()
    
    for row in origin_rows:
        orig = row["origin"] or "Otros"
        count = row["count"]
        closures = row["closures"] or 0
        spend = channel_spends.get(orig, 0.0)
        
        cpl = spend / count if count > 0 else 0.0
        cac = spend / closures if closures > 0 else 0.0
        conv_rate = (closures / count * 100) if count > 0 else 0.0
        
        marketing_report.append({
            "origin": orig,
            "leads": count,
            "closures": closures,
            "conversion_rate": round(conv_rate, 2),
            "spend": round(spend, 2),
            "cpl": round(cpl, 2),
            "cac": round(cac, 2)
        })
        processed_origins.add(orig)
        
    for orig in standard_origins:
        if orig not in processed_origins:
            spend = channel_spends.get(orig, 0.0)
            marketing_report.append({
                "origin": orig,
                "leads": 0,
                "closures": 0,
                "conversion_rate": 0.0,
                "spend": round(spend, 2),
                "cpl": 0.0,
                "cac": 0.0
            })

    cursor.execute("""
        SELECT c.id as agent_id, c.name as agent_name, c.commission_rate,
               COUNT(l.id) as deals_count, SUM(l.deal_value) as total_value
        FROM collaborators c
        LEFT JOIN leads l ON l.assigned_agent_id = c.id AND l.status = 'Ganado'
        GROUP BY c.id
    """)
    agent_rows = cursor.fetchall()
    agent_payouts = []
    
    for row in agent_rows:
        rate = row["commission_rate"] or 0.0
        val = row["total_value"] or 0.0
        payout = val * (rate / 100.0)
        agent_payouts.append({
            "agent_id": row["agent_id"],
            "agent_name": row["agent_name"],
            "commission_rate": rate,
            "deals_count": row["deals_count"],
            "total_value": round(val, 2),
            "payout": round(payout, 2)
        })
        
    conn.close()
    
    return JSONResponse(content={
        "stats": stats,
        "velocity": velocity,
        "monthly_funnel": monthly_funnel,
        "marketing_report": marketing_report,
        "agent_payouts": agent_payouts
    })

# Add website public meeting booking page
@app.get("/book")
def read_book():
    record_page_view("book")
    return FileResponse("website/book.html")

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
    decision_path: str = Form("core"),
    budget_usd: float = Form(0.0)
):
    pred = int(predecessor) if predecessor else None
    collab = int(collaborator_id) if collaborator_id else None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO tasks (phase, title, description, start_date, due_date, progress, status, predecessor, collaborator_id, decision_path, budget_usd)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (phase, title, description, start_date, due_date, progress, status_val, pred, collab, decision_path, budget_usd))
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
    decision_path: str = Form("core"),
    budget_usd: float = Form(0.0)
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
    SET phase = ?, title = ?, description = ?, start_date = ?, due_date = ?, progress = ?, status = ?, predecessor = ?, collaborator_id = ?, decision_path = ?, budget_usd = ?
    WHERE id = ?
    """, (phase, title, description, start_date, due_date, progress, status_val, pred, collab, decision_path, budget_usd, id))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/gantt", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/tasks/{id}/budget")
def edit_task_budget(id: int, budget_usd: float = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET budget_usd = ? WHERE id = ?", (budget_usd, id))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/budget", status_code=status.HTTP_303_SEE_OTHER)

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

@app.post("/api/collaborators/new")
def api_create_collaborator(name: str = Form(...), role: str = Form(None)):
    name = name.strip()
    if not name:
        return RedirectResponse(url="/admin/gantt?error=name_empty", status_code=status.HTTP_303_SEE_OTHER)
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO collaborators (name, role) VALUES (?, ?)", (name, role))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return RedirectResponse(url="/admin/gantt?error=name_exists", status_code=status.HTTP_303_SEE_OTHER)
    conn.close()
    return RedirectResponse(url="/admin/gantt", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/collaborators/{id}/delete")
def api_delete_collaborator(id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM collaborators WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/gantt", status_code=status.HTTP_303_SEE_OTHER)

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
            INSERT INTO finances (type, category, concept, amount_usd, amount_crc, currency, exchange_rate, date, invoice_path, category_type, base_amount, tax_amount, iva_rate, task_id)
            VALUES ('Gasto', 'Construcción', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (concept_finance, amount_usd, amount_crc, currency, exchange_rate, payment_date, target_path, category_type, base_amount, tax_amount, iva_rate, schedule["task_id"]))

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

class ReorderPayload(BaseModel):
    lane: str
    keys: list[str]

@app.post("/api/decisions/reorder")
def reorder_decisions(payload: ReorderPayload):
    conn = get_db()
    cursor = conn.cursor()
    try:
        for index, key in enumerate(payload.keys):
            cursor.execute("""
            UPDATE decisions 
            SET lane = ?, sort_order = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE key = ?
            """, (payload.lane, index, key))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
    conn.close()
    return {"status": "success", "message": "Orden de decisiones actualizado."}

@app.post("/api/decisions")
def create_decision(key: str = Form(...), title: str = Form(...)):
    import re
    clean_key = re.sub(r'[^a-zA-Z0-9_]', '', key.lower())
    if not clean_key:
        raise HTTPException(status_code=400, detail="Identificador de decisión no válido")
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO decisions (key, title, selected_option, notes)
        VALUES (?, ?, ?, ?)
        """, (clean_key, title, "", ""))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Ya existe una decisión con la clave '{clean_key}'")
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
    conn.close()
    return {"status": "success", "message": f"Decisión '{title}' creada.", "key": clean_key}

@app.delete("/api/decisions/{key}")
def delete_decision(key: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM decisions WHERE key = ?", (key,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Categoría {key} eliminada"}

@app.patch("/api/decisions/{key}")
def update_decision(key: str, selected_option: str = Form(...), justification: str = Form(None)):
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if option exists to get label
    cursor.execute("SELECT option_label FROM decision_options WHERE decision_key = ? AND option_code = ?", (key, selected_option))
    row = cursor.fetchone()
    if row:
        option_label = row["option_label"]
    else:
        option_label = selected_option.upper()
        
    # Update decisions table
    cursor.execute("""
    UPDATE decisions 
    SET selected_option = ?, notes = ?, updated_at = CURRENT_TIMESTAMP 
    WHERE key = ?
    """, (selected_option, justification, key))
    
    # Record in history log
    cursor.execute("""
    INSERT INTO decision_history (decision_key, option_code, option_label, justification)
    VALUES (?, ?, ?, ?)
    """, (key, selected_option, option_label, justification))
    
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Decisión {key} actualizada a {selected_option}"}

@app.get("/api/decisions/{key}/history")
def get_decision_history(key: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM decision_history WHERE decision_key = ? ORDER BY changed_at DESC", (key,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

@app.post("/api/decisions/{key}/options")
def create_decision_option(
    key: str,
    option_code: str = Form(...),
    option_label: str = Form(...),
    description: str = Form(None),
    pros: str = Form(None),
    cons: str = Form(None),
    notes: str = Form(None)
):
    import re
    clean_code = re.sub(r'[^a-zA-Z0-9_]', '', option_code.lower())
    if not clean_code:
        raise HTTPException(status_code=400, detail="Código de opción no válido")
        
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO decision_options (decision_key, option_code, option_label, description, pros, cons, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (key, clean_code, option_label, description, pros, cons, notes))
        
        # If this is the only option, or the decision's selected_option is empty, select it by default!
        cursor.execute("SELECT selected_option FROM decisions WHERE key = ?", (key,))
        dec_row = cursor.fetchone()
        if dec_row and (not dec_row["selected_option"]):
            cursor.execute("UPDATE decisions SET selected_option = ? WHERE key = ?", (clean_code, key))
            
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail=f"La opción con código '{clean_code}' ya existe para esta decisión")
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
    conn.close()
    return {"status": "success", "message": f"Opción '{option_label}' creada"}

@app.patch("/api/decisions/{key}/options/{option_code}")
def update_decision_option(
    key: str,
    option_code: str,
    option_label: str = Form(...),
    description: str = Form(None),
    pros: str = Form(None),
    cons: str = Form(None),
    notes: str = Form(None)
):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE decision_options
        SET option_label = ?, description = ?, pros = ?, cons = ?, notes = ?
        WHERE decision_key = ? AND option_code = ?
        """, (option_label, description, pros, cons, notes, key, option_code))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
    conn.close()
    return {"status": "success", "message": f"Opción '{option_label}' actualizada"}

@app.delete("/api/decisions/{key}/options/{option_code}")
def delete_decision_option(key: str, option_code: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM decision_options WHERE decision_key = ? AND option_code = ?", (key, option_code))
    
    # If the deleted option was selected_option, clear it or select another one
    cursor.execute("SELECT selected_option FROM decisions WHERE key = ?", (key,))
    dec_row = cursor.fetchone()
    if dec_row and dec_row["selected_option"] == option_code:
        # Get another available option
        cursor.execute("SELECT option_code FROM decision_options WHERE decision_key = ? LIMIT 1", (key,))
        next_opt = cursor.fetchone()
        next_code = next_opt["option_code"] if next_opt else ""
        cursor.execute("UPDATE decisions SET selected_option = ? WHERE key = ?", (next_code, key))
        
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Opción '{option_code}' eliminada"}

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
    invoice_file: UploadFile = File(None),
    task_id: str = Form(None)
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

    t_id = int(task_id) if (task_id and task_id.strip() != "") else None

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO finances (date, type, category, concept, amount_usd, amount_crc, currency, exchange_rate, invoice_path, category_type, base_amount, tax_amount, iva_rate, task_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (date, type, category, concept, amount_usd, amount_crc, currency, exchange_rate, invoice_path, category_type, base_amount, tax_amount, iva_rate, t_id))
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
    
    # Fetch tasks
    cursor.execute("SELECT * FROM tasks")
    all_tasks = [dict(row) for row in cursor.fetchall()]
    tasks = filter_tasks_by_decisions(all_tasks, cursor)
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
