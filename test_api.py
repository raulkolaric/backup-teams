from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)
try:
    with TestClient(app) as client:
        response = client.get("/stats")
        print(response.status_code)
        print(response.json())
except Exception as e:
    import traceback
    traceback.print_exc()
