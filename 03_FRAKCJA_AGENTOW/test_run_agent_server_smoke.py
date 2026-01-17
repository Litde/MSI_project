"""
Smoke test runner for the agent FastAPI server.
Runs TestClient against the `run_agent_server.app` to validate endpoints.
Run: python 03_FRAKCJA_AGENTOW/test_run_agent_server_smoke.py
"""
import sys
from pathlib import Path
ROOT = Path(r"D:/Polibuda/Sezon_2_Semestr_2/MSI/MSI_project")
AGENT_FOLDER = ROOT / "03_FRAKCJA_AGENTOW"
if str(AGENT_FOLDER) not in sys.path:
    sys.path.insert(0, str(AGENT_FOLDER))

from fastapi.testclient import TestClient
from run_agent_server import app

client = TestClient(app)

# Health check
r = client.get('/')
print('GET / ->', r.status_code, r.json())

# Action call
payload = {
    'current_tick': 1,
    'my_tank_status': {'_id':'t1','_team':1,'position':{'x':10,'y':10},'heading':0,'barrel_angle':0,'move_speed':0,'_top_speed':5,'hp':100,'ammo_loaded':None,'_reload_timer':0.0,'ammo':{}},
    'sensor_data': {'seen_tanks':[],'seen_powerups':[],'seen_obstacles':[]},
    'enemies_remaining': 0
}

r = client.post('/agent/action', json=payload)
print('POST /agent/action ->', r.status_code, r.json())

# Destroy
r = client.post('/agent/destroy')
print('POST /agent/destroy ->', r.status_code, r.json())

# End
r = client.post('/agent/end', json={'damage_dealt':0,'tanks_killed':0})
print('POST /agent/end ->', r.status_code, r.json())

print('Smoke test completed')

