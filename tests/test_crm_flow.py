import os
import sqlite3
import pytest
from fastapi.testclient import TestClient
from main import app, get_db
import main

TEST_DB = "test_lomas_crm.db"
main.DATABASE_NAME = TEST_DB

@pytest.fixture(autouse=True)
def setup_test_db():
    main.DATABASE_NAME = TEST_DB
    # Override get_db to use test database
    def get_test_db():
        conn = sqlite3.connect(TEST_DB)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn
        
    app.dependency_overrides[get_db] = get_test_db
    
    # Initialize the test database schema
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except OSError:
            pass
    main.init_db()
    
    yield
    
    # Teardown
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except OSError:
            pass

client = TestClient(app)

def test_page_view_logging():
    # Call public endpoints to generate page views
    client.get("/")
    client.get("/residential")
    client.get("/book")
    
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM page_views")
    count = cursor.fetchone()[0]
    assert count == 3
    
    cursor.execute("SELECT page_path FROM page_views ORDER BY id ASC")
    paths = [row[0] for row in cursor.fetchall()]
    assert "home" in paths
    assert "residential" in paths
    assert "book" in paths
    conn.close()

def test_lead_assignment_rules_and_round_robin():
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    # Clear rules and collaborators to avoid foreign key issues with seeded database rows
    cursor.execute("DELETE FROM assignment_rules")
    cursor.execute("DELETE FROM collaborators")
    cursor.execute("""
        INSERT INTO collaborators (name, role, languages, is_active_round_robin, last_assigned_at, commission_rate, zoom_link)
        VALUES ('Agent English', 'Ventas', 'en', 1, NULL, 3.0, 'https://zoom.us/j/english')
    """)
    agent_en_id = cursor.lastrowid
    
    cursor.execute("""
        INSERT INTO collaborators (name, role, languages, is_active_round_robin, last_assigned_at, commission_rate, zoom_link)
        VALUES ('Agent Spanish', 'Ventas', 'es', 1, NULL, 3.0, 'https://zoom.us/j/spanish')
    """)
    agent_es_id = cursor.lastrowid
    
    cursor.execute("""
        INSERT INTO collaborators (name, role, languages, is_active_round_robin, last_assigned_at, commission_rate, zoom_link)
        VALUES ('Agent Admin', 'Director', 'es,en', 0, NULL, 0.0, 'https://zoom.us/j/admin')
    """)
    conn.commit()

    # Case 1: Booking an English meeting (Round Robin checks language)
    payload_en = {
        "name": "Jane English",
        "email": "jane@english.com",
        "phone": "12345",
        "language": "en",
        "datetime": "2026-08-25 10:00",
        "topic": "Interested in Lot 5",
        "budget_prequalification": "YES",
        "origin": "directo"
    }
    response_en = client.post("/api/meetings/book", json=payload_en)
    assert response_en.status_code == 200
    res_data_en = response_en.json()
    assert res_data_en["agent_name"] == "Agent English"
    assert res_data_en["zoom_link"] == "https://zoom.us/j/english"

    # Case 2: Booking a Spanish meeting (Round Robin checks language)
    payload_es = {
        "name": "Juan Español",
        "email": "juan@espanol.com",
        "phone": "54321",
        "language": "es",
        "datetime": "2026-08-25 11:00",
        "topic": "Quiero comprar lote",
        "budget_prequalification": "DONT_KNOW",
        "origin": "directo"
    }
    response_es = client.post("/api/meetings/book", json=payload_es)
    assert response_es.status_code == 200
    res_data_es = response_es.json()
    assert res_data_es["agent_name"] == "Agent Spanish"
    assert res_data_es["zoom_link"] == "https://zoom.us/j/spanish"

    # Case 3: Custom Rule Override (Assign F&F to Admin agent)
    cursor.execute("""
        INSERT INTO assignment_rules (field_name, field_value, agent_id, priority, is_active)
        VALUES ('origin', 'F&F', ?, 1, 1)
    """, (agent_es_id,)) # Rule maps F&F leads to Agent Spanish
    conn.commit()

    payload_rule = {
        "name": "Amigo de la casa",
        "email": "amigo@lomas.com",
        "phone": "9999",
        "language": "en", # Even though language is English, rule should override!
        "datetime": "2026-08-26 15:00",
        "topic": "Pre-venta vip",
        "budget_prequalification": "YES",
        "origin": "F&F"
    }
    response_rule = client.post("/api/meetings/book", json=payload_rule)
    assert response_rule.status_code == 200
    res_data_rule = response_rule.json()
    # It must assign to Agent Spanish because of the F&F rule!
    assert res_data_rule["agent_name"] == "Agent Spanish"
    conn.close()

def test_transition_timestamps():
    # 1. Create a lead manually in "Nuevo" stage
    response = client.post("/api/leads/new", data={
        "name": "Test Timestamps",
        "email": "ts@test.com",
        "phone": "123",
        "origin": "directo",
        "status": "Nuevo",
        "replied": 0,
        "assigned_agent_id": "auto",
        "language": "es",
        "lead_temperature": "frio",
        "deal_value": 0.0
    }, follow_redirects=False)
    assert response.status_code == 303
    
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT id, contacted_at, closed_at FROM leads WHERE email = 'ts@test.com'")
    row = cursor.fetchone()
    lead_id = row[0]
    assert row[1] is None # contacted_at not set
    assert row[2] is None # closed_at not set

    # 2. Transition lead to "Contactado"
    client.post(f"/api/leads/{lead_id}/edit", data={
        "name": "Test Timestamps",
        "email": "ts@test.com",
        "phone": "123",
        "origin": "directo",
        "status": "Contactado",
        "replied": 1,
        "assigned_agent_id": "",
        "language": "es",
        "lead_temperature": "tibio",
        "deal_value": 0.0
    })
    
    cursor.execute("SELECT contacted_at, closed_at FROM leads WHERE id = ?", (lead_id,))
    row2 = cursor.fetchone()
    assert row2[0] is not None # contacted_at IS set!
    assert row2[1] is None     # closed_at still not set

    # 3. Transition lead to "Ganado"
    client.post(f"/api/leads/{lead_id}/edit", data={
        "name": "Test Timestamps",
        "email": "ts@test.com",
        "phone": "123",
        "origin": "directo",
        "status": "Ganado",
        "replied": 1,
        "assigned_agent_id": "",
        "language": "es",
        "lead_temperature": "caliente",
        "deal_value": 190000.0
    })
    
    cursor.execute("SELECT closed_at, deal_value FROM leads WHERE id = ?", (lead_id,))
    row3 = cursor.fetchone()
    assert row3[0] is not None # closed_at IS set!
    assert row3[1] == 190000.0 # deal_value set!
    conn.close()

def test_marketing_reports_and_cac():
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    
    # Seed a marketing expense in finance
    # $1000 spent on Meta Pago
    cursor.execute("""
        INSERT INTO finances (type, category, concept, amount_usd, date, marketing_channel)
        VALUES ('Gasto', 'Marketing', 'Meta Ads spend July', 1000.0, '2026-08-01', 'Meta Pago')
    """)
    
    # Create two leads with origin 'Meta Pago', and close one of them
    cursor.execute("""
        INSERT INTO leads (name, email, origin, status, lead_temperature, deal_value, closed_at)
        VALUES ('Lead 1 Meta', 'meta1@test.com', 'Meta Pago', 'Ganado', 'caliente', 190000.0, '2026-08-10 12:00:00')
    """)
    cursor.execute("""
        INSERT INTO leads (name, email, origin, status, lead_temperature, deal_value)
        VALUES ('Lead 2 Meta', 'meta2@test.com', 'Meta Pago', 'Contactado', 'tibio', 0.0)
    """)
    conn.commit()
    conn.close()
    
    # Request stats
    response = client.get("/api/crm/stats")
    assert response.status_code == 200
    data = response.json()
    
    # Find Meta Pago in marketing report
    mkt_list = data["marketing_report"]
    meta_pago_stat = next(x for x in mkt_list if x["origin"] == "Meta Pago")
    
    assert meta_pago_stat["leads"] == 2
    assert meta_pago_stat["closures"] == 1
    assert meta_pago_stat["spend"] == 1000.0
    assert meta_pago_stat["cpl"] == 500.0  # Spend $1000 / 2 leads
    assert meta_pago_stat["cac"] == 1000.0 # Spend $1000 / 1 closure
    assert meta_pago_stat["conversion_rate"] == 50.0
