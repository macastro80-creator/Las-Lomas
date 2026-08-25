import os
import shutil
import sqlite3
import pytest
from fastapi.testclient import TestClient
from main import app, get_db, filter_tasks_by_decisions
import main

TEST_DB = "test_lomas.db"


@pytest.fixture(autouse=True)
def setup_test_db():
    main.DATABASE_NAME = TEST_DB
    def get_test_db():
        conn = sqlite3.connect(TEST_DB)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn
        
    app.dependency_overrides[get_db] = get_test_db
    
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except OSError:
            pass
    main.init_db()
    
    yield
    
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except OSError:
            pass
    if os.path.exists("uploads"):
        shutil.rmtree("uploads", ignore_errors=True)
        os.makedirs("uploads", exist_ok=True)

client = TestClient(app)

def test_create_decision_and_option():
    # 1. Crear una nueva categoría de decisión
    response = client.post("/api/decisions", data={
        "key": "maintenance_option",
        "title": "Mantenimiento de Finca"
    })
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["key"] == "maintenance_option"

    # Verificar inserción en DB
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM decisions WHERE key = 'maintenance_option'")
    dec = cursor.fetchone()
    assert dec is not None
    assert dec[1] == "maintenance_option" # key (key is column 1)
    assert dec[2] == "Mantenimiento de Finca" # title (title is column 2)

    # 2. Agregar una opción
    response = client.post("/api/decisions/maintenance_option/options", data={
        "option_code": "hire_full_time",
        "option_label": "Contratar Tiempo Completo",
        "description": "Contratar a alguien de mantenimiento fijo",
        "pros": "Control total\nMás dedicación",
        "cons": "Mayor costo fijo",
        "notes": "Gonzalo sugiere revisar contrato laboral estándar"
    })
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verificar opción en DB
    cursor.execute("SELECT * FROM decision_options WHERE decision_key = 'maintenance_option' AND option_code = 'hire_full_time'")
    opt = cursor.fetchone()
    assert opt is not None
    assert opt[3] == "Contratar Tiempo Completo" # label
    assert opt[4] == "Contratar a alguien de mantenimiento fijo" # description
    assert opt[5] == "Control total\nMás dedicación" # pros
    assert opt[6] == "Mayor costo fijo" # cons
    assert opt[7] == "Gonzalo sugiere revisar contrato laboral estándar" # notes

    # Al ser la primera opción, se selecciona por defecto
    cursor.execute("SELECT selected_option FROM decisions WHERE key = 'maintenance_option'")
    assert cursor.fetchone()[0] == "hire_full_time"
    conn.close()

def test_select_decision_with_justification():
    # 1. Crear decisión y opciones
    client.post("/api/decisions", data={"key": "assistant_option", "title": "Asistente"})
    client.post("/api/decisions/assistant_option/options", data={
        "option_code": "outsource",
        "option_label": "Tercerizar",
        "description": "Contratar servicios externos por horas"
    })
    client.post("/api/decisions/assistant_option/options", data={
        "option_code": "in_house",
        "option_label": "Asistente Interno",
        "description": "Contratar asistente de tiempo completo"
    })

    # 2. Seleccionar una opción con justificación
    response = client.patch("/api/decisions/assistant_option", data={
        "selected_option": "outsource",
        "justification": "Se decide tercerizar para evaluar volumen de trabajo inicial."
    })
    assert response.status_code == 200

    # 3. Verificar estado en tabla decisions
    conn = sqlite3.connect(TEST_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT selected_option, notes FROM decisions WHERE key = 'assistant_option'")
    dec = cursor.fetchone()
    assert dec["selected_option"] == "outsource"
    assert dec["notes"] == "Se decide tercerizar para evaluar volumen de trabajo inicial."

    # 4. Verificar registro en la bitácora (history)
    cursor.execute("SELECT * FROM decision_history WHERE decision_key = 'assistant_option' ORDER BY id DESC LIMIT 1")
    hist = cursor.fetchone()
    assert hist is not None
    assert hist["option_code"] == "outsource"
    assert hist["option_label"] == "Tercerizar"
    assert hist["justification"] == "Se decide tercerizar para evaluar volumen de trabajo inicial."
    conn.close()

def test_task_filtering_by_decisions():
    # 1. Crear categoría y opciones
    client.post("/api/decisions", data={"key": "water_option", "title": "Agua"})
    client.post("/api/decisions/water_option/options", data={"option_code": "asada", "option_label": "ASADA"})
    client.post("/api/decisions/water_option/options", data={"option_code": "pozo", "option_label": "Pozo"})

    # Seleccionar 'asada'
    client.patch("/api/decisions/water_option", data={"selected_option": "asada", "justification": "Elegido por bajo costo"})

    # 2. Crear tareas asociadas a las opciones y generales
    tasks_to_insert = [
        {"title": "Trámite de disponibilidad ASADA", "decision_path": "asada"},
        {"title": "Perforación de pozo mecánico", "decision_path": "pozo"},
        {"title": "Compra de terreno base", "decision_path": "core"}
    ]
    
    conn = sqlite3.connect(TEST_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks")
    for t in tasks_to_insert:
        cursor.execute("INSERT INTO tasks (title, decision_path) VALUES (?, ?)", (t["title"], t["decision_path"]))
    conn.commit()

    # 3. Leer tareas y filtrar
    cursor.execute("SELECT * FROM tasks")
    tasks_list = [dict(row) for row in cursor.fetchall()]
    filtered = filter_tasks_by_decisions(tasks_list, cursor)

    # Solo deben estar "Trámite de disponibilidad ASADA" y "Compra de terreno base"
    assert len(filtered) == 2
    titles = [t["title"] for t in filtered]
    assert "Trámite de disponibilidad ASADA" in titles
    assert "Compra de terreno base" in titles
    assert "Perforación de pozo mecánico" not in titles

    # 4. Cambiar decisión a 'pozo' y verificar cambio de filtro
    client.patch("/api/decisions/water_option", data={"selected_option": "pozo", "justification": "ASADA no dio disponibilidad"})
    cursor.execute("SELECT * FROM tasks")
    tasks_list = [dict(row) for row in cursor.fetchall()]
    filtered_pozo = filter_tasks_by_decisions(tasks_list, cursor)

    # Ahora solo deben estar "Perforación de pozo mecánico" y "Compra de terreno base"
    assert len(filtered_pozo) == 2
    titles_pozo = [t["title"] for t in filtered_pozo]
    assert "Perforación de pozo mecánico" in titles_pozo
    assert "Compra de terreno base" in titles_pozo
    assert "Trámite de disponibilidad ASADA" not in titles_pozo
    conn.close()

def test_delete_decision_option():
    # 1. Crear
    client.post("/api/decisions", data={"key": "test_del", "title": "Test Delete"})
    client.post("/api/decisions/test_del/options", data={"option_code": "opt1", "option_label": "Opción 1"})
    client.post("/api/decisions/test_del/options", data={"option_code": "opt2", "option_label": "Opción 2"})

    # Asegurar que opt1 está seleccionada
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT selected_option FROM decisions WHERE key = 'test_del'")
    assert cursor.fetchone()[0] == "opt1"

    # 2. Eliminar opt1
    response = client.delete("/api/decisions/test_del/options/opt1")
    assert response.status_code == 200

    # 3. Verificar que opt2 ahora sea seleccionada automáticamente
    cursor.execute("SELECT selected_option FROM decisions WHERE key = 'test_del'")
    assert cursor.fetchone()[0] == "opt2"

    # 4. Eliminar opt2
    client.delete("/api/decisions/test_del/options/opt2")
    cursor.execute("SELECT selected_option FROM decisions WHERE key = 'test_del'")
    assert cursor.fetchone()[0] == ""
    conn.close()

def test_admin_decisions_route():
    # Probar que la nueva ruta /admin/decisions cargue correctamente
    response = client.get("/admin/decisions")
    assert response.status_code == 200
    assert "Centro de Decisiones" in response.text

def test_reorder_decisions():
    # 1. Crear dos decisiones
    client.post("/api/decisions", data={"key": "dec_a", "title": "Decisión A"})
    client.post("/api/decisions", data={"key": "dec_b", "title": "Decisión B"})

    # Por defecto deben estar en 'Identificadas' y con sort_order = 0
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT lane, sort_order FROM decisions WHERE key = 'dec_a'")
    row_a = cursor.fetchone()
    assert row_a[0] == "Identificadas"
    
    # 2. Enviar solicitud de reordenación
    # Mover dec_b y dec_a al carril 'En Análisis' en ese orden específico (dec_b primero, dec_a segundo)
    response = client.post("/api/decisions/reorder", json={
        "lane": "En Análisis",
        "keys": ["dec_b", "dec_a"]
    })
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verificar que el orden y el carril se actualizaron correctamente
    cursor.execute("SELECT lane, sort_order FROM decisions WHERE key = 'dec_b'")
    row_b = cursor.fetchone()
    assert row_b[0] == "En Análisis"
    assert row_b[1] == 0

    cursor.execute("SELECT lane, sort_order FROM decisions WHERE key = 'dec_a'")
    row_a = cursor.fetchone()
    assert row_a[0] == "En Análisis"
    assert row_a[1] == 1
    
    conn.close()
