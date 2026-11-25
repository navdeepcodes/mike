#!/usr/bin/env python3
"""Hidden tests for Task 1: FastAPI Todo API with JWT auth."""
import pytest
import tempfile
import os
import sys
from pathlib import Path

# Add the task directory to path
task_dir = Path(__file__).parent / "task1_fastapi_todo_run1"
if not task_dir.exists():
    task_dir = Path(__file__).parent / "task1_fastapi_todo_run2"

sys.path.insert(0, str(task_dir))

from httpx import AsyncClient, ASGITransport


@pytest.fixture(scope="session")
def app_module():
    """Import the app module."""
    import importlib.util
    main_py = task_dir / "main.py"
    if not main_py.exists():
        pytest.skip("main.py not found")

    spec = importlib.util.spec_from_file_location("main", main_py)
    module = importlib.util.module_from_spec(spec)
    sys.modules["main"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="function")
async def client(app_module):
    """Create test client with temp database."""
    # Use a temp database file
    import tempfile
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)

    # Set env var for database URL if the app uses it
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    # Recreate tables
    if hasattr(app_module, 'Base') and hasattr(app_module, 'engine'):
        app_module.Base.metadata.create_all(bind=app_module.engine)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    # Cleanup
    try:
        os.unlink(db_path)
    except:
        pass


@pytest.fixture
async def auth_token(client):
    """Create a user and return auth token."""
    # Signup
    resp = await client.post("/auth/signup", json={
        "username": "testuser",
        "password": "testpass123"
    })
    assert resp.status_code == 200

    # Login
    resp = await client.post("/auth/login", json={
        "username": "testuser",
        "password": "testpass123"
    })
    assert resp.status_code == 200
    return resp.json()["access_token"]


class TestAuth:
    """Test authentication endpoints."""

    async def test_signup_success(self, client):
        resp = await client.post("/auth/signup", json={
            "username": "newuser",
            "password": "password123"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_signup_duplicate(self, client, auth_token):
        resp = await client.post("/auth/signup", json={
            "username": "testuser",
            "password": "differentpass"
        })
        assert resp.status_code == 400

    async def test_login_success(self, client):
        # First signup
        await client.post("/auth/signup", json={
            "username": "loginuser",
            "password": "loginpass"
        })
        # Then login
        resp = await client.post("/auth/login", json={
            "username": "loginuser",
            "password": "loginpass"
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_login_wrong_password(self, client):
        await client.post("/auth/signup", json={
            "username": "loginuser2",
            "password": "loginpass"
        })
        resp = await client.post("/auth/login", json={
            "username": "loginuser2",
            "password": "wrongpass"
        })
        assert resp.status_code == 401


class TestTodoCRUD:
    """Test todo CRUD operations."""

    async def test_create_todo(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        resp = await client.post("/todos/", json={
            "title": "Test Todo",
            "description": "Test Description"
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test Todo"
        assert data["description"] == "Test Description"
        assert data["completed"] is False
        assert "id" in data
        assert "created_at" in data
        assert "owner_id" in data

    async def test_create_todo_minimal(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        resp = await client.post("/todos/", json={
            "title": "Minimal Todo"
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Minimal Todo"
        assert data["description"] is None
        assert data["completed"] is False

    async def test_create_todo_unauthorized(self, client):
        resp = await client.post("/todos/", json={"title": "No Auth"})
        assert resp.status_code == 401

    async def test_read_todos_empty(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        resp = await client.get("/todos/", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_read_todos_with_data(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        # Create 3 todos
        for i in range(3):
            await client.post("/todos/", json={"title": f"Todo {i}"}, headers=headers)

        resp = await client.get("/todos/", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

    async def test_read_todos_filter_completed(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        # Create todos
        r1 = await client.post("/todos/", json={"title": "Todo 1"}, headers=headers)
        r2 = await client.post("/todos/", json={"title": "Todo 2"}, headers=headers)
        id1 = r1.json()["id"]

        # Complete one
        await client.put(f"/todos/{id1}", json={"completed": True}, headers=headers)

        # Filter completed=false
        resp = await client.get("/todos/?completed=false", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Todo 2"

        # Filter completed=true
        resp = await client.get("/todos/?completed=true", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Todo 1"

    async def test_read_todos_pagination(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        for i in range(15):
            await client.post("/todos/", json={"title": f"Todo {i}"}, headers=headers)

        resp = await client.get("/todos/?skip=0&limit=5", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 5

        resp = await client.get("/todos/?skip=5&limit=5", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 5
        assert data[0]["title"] == "Todo 5"

    async def test_read_single_todo(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        create_resp = await client.post("/todos/", json={"title": "Single Todo"}, headers=headers)
        todo_id = create_resp.json()["id"]

        resp = await client.get(f"/todos/{todo_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Single Todo"
        assert data["id"] == todo_id

    async def test_read_single_todo_not_found(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        resp = await client.get("/todos/999", headers=headers)
        assert resp.status_code == 404

    async def test_read_single_todo_wrong_user(self, client, auth_token):
        """Users should not see each other's todos."""
        headers = {"Authorization": f"Bearer {auth_token}"}
        create_resp = await client.post("/todos/", json={"title": "User1 Todo"}, headers=headers)
        todo_id = create_resp.json()["id"]

        # Create second user
        await client.post("/auth/signup", json={"username": "user2", "password": "pass2"})
        login_resp = await client.post("/auth/login", json={"username": "user2", "password": "pass2"})
        token2 = login_resp.json()["access_token"]
        headers2 = {"Authorization": f"Bearer {token2}"}

        # User2 tries to access user1's todo
        resp = await client.get(f"/todos/{todo_id}", headers=headers2)
        assert resp.status_code == 404

    async def test_update_todo(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        create_resp = await client.post("/todos/", json={"title": "Original", "completed": False}, headers=headers)
        todo_id = create_resp.json()["id"]

        resp = await client.put(f"/todos/{todo_id}", json={"title": "Updated", "completed": True}, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Updated"
        assert data["completed"] is True

    async def test_update_todo_partial(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        create_resp = await client.post("/todos/", json={"title": "Original", "description": "Desc", "completed": False}, headers=headers)
        todo_id = create_resp.json()["id"]

        resp = await client.put(f"/todos/{todo_id}", json={"completed": True}, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Original"
        assert data["description"] == "Desc"
        assert data["completed"] is True

    async def test_update_todo_not_found(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        resp = await client.put("/todos/999", json={"title": "Updated"}, headers=headers)
        assert resp.status_code == 404

    async def test_delete_todo(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        create_resp = await client.post("/todos/", json={"title": "To Delete"}, headers=headers)
        todo_id = create_resp.json()["id"]

        resp = await client.delete(f"/todos/{todo_id}", headers=headers)
        assert resp.status_code == 200

        # Verify deleted
        get_resp = await client.get(f"/todos/{todo_id}", headers=headers)
        assert get_resp.status_code == 404

    async def test_delete_todo_not_found(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        resp = await client.delete("/todos/999", headers=headers)
        assert resp.status_code == 404

    async def test_full_crud_workflow(self, client, auth_token):
        headers = {"Authorization": f"Bearer {auth_token}"}
        create_resp = await client.post("/todos/", json={"title": "Workflow", "description": "Test", "completed": False}, headers=headers)
        assert create_resp.status_code == 200
        todo = create_resp.json()
        todo_id = todo["id"]

        read_resp = await client.get(f"/todos/{todo_id}", headers=headers)
        assert read_resp.status_code == 200
        assert read_resp.json()["title"] == "Workflow"

        update_resp = await client.put(f"/todos/{todo_id}", json={"completed": True}, headers=headers)
        assert update_resp.status_code == 200
        assert update_resp.json()["completed"] is True

        list_resp = await client.get("/todos/", headers=headers)
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        delete_resp = await client.delete(f"/todos/{todo_id}", headers=headers)
        assert delete_resp.status_code == 200

        final_list = await client.get("/todos/", headers=headers)
        assert final_list.status_code == 200
        assert len(final_list.json()) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])