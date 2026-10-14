"""Test-only HTTP bridge: actual router, isolated DB path required, no listener."""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.assistant.api import create_router
from server.assistant.service import AssistantService
from server.interaction.threads_api import create_threads_router

request = json.load(sys.stdin)
database = Path(sys.argv[1]).resolve()
temporary_root = Path(tempfile.gettempdir()).resolve()
if database.name != 'assistant.db' or temporary_root not in database.parents:
    raise ValueError('An isolated temporary assistant.db path is required')
service = AssistantService(database.parent, SimpleNamespace(offline=True))
app = FastAPI()
app.include_router(create_router(service))
app.include_router(create_threads_router(service))
try:
    with TestClient(app) as client:
        response = client.request(request['method'], request['path'], headers={'Origin':'http://testserver'}, **({'json':request['body']} if 'body' in request else {}))
        print(json.dumps({'status':response.status_code,'body':response.json()}))
finally:
    service.close()
