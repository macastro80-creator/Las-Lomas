import os
import sqlite3
import pytest
from fastapi.testclient import TestClient
from main import app, get_db
import main

TEST_DB = "test_lomas_collab.db"
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

def test_collaborator_creation_and_deletion():
    # 1. Verify collaborators table is initially populated with default collabs
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM collaborators")
    initial_count = cursor.fetchone()[0]
    assert initial_count > 0
    
    # 2. Add a new collaborator
    response = client.post("/api/collaborators/new", data={"name": "Mario Gomez", "role": "Diseñador UX"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/gantt"
    
    # Verify Mario exists in the DB
    cursor.execute("SELECT * FROM collaborators WHERE name = 'Mario Gomez'")
    mario = cursor.fetchone()
    assert mario is not None
    assert mario[2] == "Diseñador UX" # role is role
    mario_id = mario[0]
    
    # 3. Try to add Mario Gomez again (duplicate name)
    response_dup = client.post("/api/collaborators/new", data={"name": "Mario Gomez", "role": "Otro Rol"}, follow_redirects=False)
    assert response_dup.status_code == 303
    assert "error=name_exists" in response_dup.headers["location"]
    
    # 4. Try to add with an empty name
    response_empty = client.post("/api/collaborators/new", data={"name": "   ", "role": "Test"}, follow_redirects=False)
    assert response_empty.status_code == 303
    assert "error=name_empty" in response_empty.headers["location"]
    
    # 5. Assign Mario Gomez to a task, then delete him and check cascading behavior
    cursor.execute("INSERT INTO tasks (title, start_date, due_date, phase, collaborator_id) VALUES ('Test Task', '2026-08-14', '2026-08-30', 'Planificación', ?)", (mario_id,))
    task_id = cursor.lastrowid
    conn.commit()
    
    # Verify task is assigned to Mario
    cursor.execute("SELECT collaborator_id FROM tasks WHERE id = ?", (task_id,))
    assert cursor.fetchone()[0] == mario_id
    
    # Delete Mario
    response_del = client.post(f"/api/collaborators/{mario_id}/delete", follow_redirects=False)
    assert response_del.status_code == 303
    assert response_del.headers["location"] == "/admin/gantt"
    
    # Verify Mario is deleted
    cursor.execute("SELECT * FROM collaborators WHERE id = ?", (mario_id,))
    assert cursor.fetchone() is None
    
    # Verify the task's collaborator_id is set to NULL (due to ON DELETE SET NULL)
    cursor.execute("SELECT collaborator_id FROM tasks WHERE id = ?", (task_id,))
    assert cursor.fetchone()[0] is None
    
    conn.close()
