import os
import shutil
import sqlite3
import pytest
import time
from fastapi.testclient import TestClient
from main import app, get_db
import main

TEST_DB = "test_lomas.db"
main.DATABASE_NAME = TEST_DB


@pytest.fixture(autouse=True)
def setup_test_db():
    main.DATABASE_NAME = TEST_DB
    # Sobrescribir get_db para usar la base de datos de pruebas
    def get_test_db():
        conn = sqlite3.connect(TEST_DB)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn
        
    app.dependency_overrides[get_db] = get_test_db
    
    # Inicializar el esquema en la base de datos de prueba
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except OSError:
            pass
    main.init_db()
    
    yield
    
    # Teardown: borrar la base de datos de pruebas y archivos de carga
    if os.path.exists(TEST_DB):
        try:
            os.remove(TEST_DB)
        except OSError:
            pass
    if os.path.exists("uploads"):
        shutil.rmtree("uploads", ignore_errors=True)
        os.makedirs("uploads", exist_ok=True)

client = TestClient(app)

def test_subtask_progress_calculation():
    """
    Verifica que el progreso de una tarea se calcule automáticamente en base a
    las sub-tareas y su estado de completitud.
    """
    # 1. Crear una tarea
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (title, start_date, due_date, phase) VALUES ('Planos Hidráulicos', '2026-08-14', '2026-08-30', 'Planificación')")
    task_id = cursor.lastrowid
    conn.commit()
    
    # 2. Agregar sub-tareas y verificar cálculo automático
    response = client.post(f"/api/tasks/{task_id}/subtasks/new", data={"title": "Levantamiento topográfico"}, follow_redirects=False)
    assert response.status_code == 303 # Redirección OK
    
    client.post(f"/api/tasks/{task_id}/subtasks/new", data={"title": "Diseño de curvas de nivel"})
    
    # Consultar base de datos: progreso debe ser 0%
    cursor.execute("SELECT progress, status FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    assert task[0] == 0
    assert task[1] == "Pendiente"
    
    # 3. Completar una sub-tarea
    cursor.execute("SELECT id FROM subtasks WHERE task_id = ?", (task_id,))
    subtasks = cursor.fetchall()
    subtask_1_id = subtasks[0][0]
    subtask_2_id = subtasks[1][0]
    
    client.post(f"/api/subtasks/{subtask_1_id}/toggle")
    
    # Progreso debe actualizarse automáticamente al 50% y estado "En Proceso"
    cursor.execute("SELECT progress, status FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    assert task[0] == 50
    assert task[1] == "En Proceso"
    
    # 4. Completar la segunda sub-tarea
    client.post(f"/api/subtasks/{subtask_2_id}/toggle")
    
    # Progreso al 100% y estado "Completado"
    cursor.execute("SELECT progress, status FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    assert task[0] == 100
    assert task[1] == "Completado"
    
    # 5. Borrar una sub-tarea
    client.post(f"/api/subtasks/{subtask_1_id}/delete")
    
    # Al quedar 1 sola tarea completada de 1 total, el progreso sigue siendo 100%
    cursor.execute("SELECT progress, status FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    assert task[0] == 100
    conn.close()


def test_atomic_pay_transaction_and_iva_calculation():
    """
    Verifica que la liquidación de un hito de pago sea una transacción atómica, 
    registre la factura en disco, calcule el desglose de IVA y cree el Gasto financiero.
    """
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    
    # 1. Crear tarea y un cronograma de pago asociado de $1,130 USD (Bruto, incluye 13% IVA)
    cursor.execute("INSERT INTO tasks (title) VALUES ('Entregable Planos')")
    task_id = cursor.lastrowid
    
    cursor.execute("""
        INSERT INTO payment_schedules (task_id, concept, amount_gross, currency, due_date, status)
        VALUES (?, 'Entrega de Planos Catastrados', 1130.0, 'USD', '2026-09-01', 'Pendiente')
    """, (task_id,))
    schedule_id = cursor.lastrowid
    conn.commit()
    
    # 2. Liquidar el pago subiendo un archivo falso (Mock PDF)
    mock_pdf_content = b"%PDF-1.4 mock content"
    files = {"invoice_file": ("factura.pdf", mock_pdf_content, "application/pdf")}
    data = {"exchange_rate": 518.5, "payment_date": "2026-08-14"}
    
    response = client.post(f"/api/payment-schedules/{schedule_id}/pay", files=files, data=data, follow_redirects=False)
    assert response.status_code == 303
    
    # 3. Comprobar actualización del estado del pago y su ruta de archivo
    cursor.execute("SELECT status, invoice_file, paid_at FROM payment_schedules WHERE id = ?", (schedule_id,))
    schedule = cursor.fetchone()
    assert schedule[0] == "Pagado"
    assert "uploads/schedule_" in schedule[1]
    assert schedule[2] == "2026-08-14"
    
    # Verificar que el archivo realmente se guardó en disco
    assert os.path.exists(schedule[1])
    
    # 4. Comprobar que se insertó el Gasto en Finanzas con la tasa de cambio aplicada
    cursor.execute("SELECT type, amount_usd, amount_crc, currency, exchange_rate, base_amount, tax_amount FROM finances WHERE concept LIKE '%Entrega de Planos%'")
    finance = cursor.fetchone()
    assert finance is not None
    assert finance[0] == "Gasto"
    assert finance[1] == 1130.0                # USD bruto
    assert finance[2] == 1130.0 * 518.5        # CRC calculado
    assert finance[3] == "USD"
    assert finance[4] == 518.5
    # Verificaciones de IVA
    assert abs(finance[5] - (1130.0 / 1.13)) < 0.01  # base_amount
    assert abs(finance[6] - (1130.0 - 1130.0 / 1.13)) < 0.01  # tax_amount
    
    conn.close()


def test_atomic_rollback_on_failure():
    """
    Verifica que ante una falla en la base de datos durante el proceso de pago,
    la base de datos haga rollback del estado y elimine el archivo del disco.
    """
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tasks (title) VALUES ('Tarea de Falla')")
    task_id = cursor.lastrowid
    cursor.execute("""
        INSERT INTO payment_schedules (task_id, concept, amount_gross, currency, due_date, status)
        VALUES (?, 'Pago Test Fallido', 500.0, 'USD', '2026-09-01', 'Pendiente')
    """, (task_id,))
    schedule_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    # Provocar un error forzado agregando una columna no nula sin valor por defecto en finances
    conn = sqlite3.connect(TEST_DB)
    conn.execute("ALTER TABLE finances ADD COLUMN required_val TEXT NOT NULL")
    conn.commit()
    conn.close()
    
    # Intentamos pagar
    mock_pdf_content = b"%PDF-1.4 crash content"
    files = {"invoice_file": ("factura_error.pdf", mock_pdf_content, "application/pdf")}
    data = {"exchange_rate": 518.5, "payment_date": "2026-08-14"}
    
    # Esto arrojará un error 500 debido a la restricción NOT NULL en la transacción
    response = client.post(f"/api/payment-schedules/{schedule_id}/pay", files=files, data=data)
    assert response.status_code == 500
    
    # Comprobar que no hay archivos guardados en la carpeta uploads/
    uploaded_files = os.listdir("uploads") if os.path.exists("uploads") else []
    # Filtrar solo archivos creados para esta prueba (que comienzan con schedule_{schedule_id})
    relevant_files = [f for f in uploaded_files if f.startswith(f"schedule_{schedule_id}")]
    assert len(relevant_files) == 0
    
    # Comprobar que la base de datos se mantiene intacta en 'Pendiente'
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT status, invoice_file FROM payment_schedules WHERE id = ?", (schedule_id,))
    schedule = cursor.fetchone()
    assert schedule[0] == "Pendiente"
    assert schedule[1] is None
    conn.close()
