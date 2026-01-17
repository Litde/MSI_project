"""
HTTP smoke test for the running universal agent server.
Sends requests to http://127.0.0.1:8001 to verify endpoints and payload shapes
expected by game_loop.py.
Run: python agent_server_smoke_http.py
"""
import requests
import json

BASE = 'http://127.0.0.1:8001'

def pretty(resp):
    try:
        return json.dumps(resp.json(), indent=2)
    except Exception:
        return resp.text

print('GET /')
r = requests.get(BASE + '/')
print(r.status_code)
print(pretty(r))

print('\nPOST /agent/action')
payload = {
    'current_tick': 123,
    'my_tank_status': {
        '_id': 'tank_test',
        '_team': 1,
        '_tank_type': 'LIGHT',
        'hp': 100,
        'shield': 50,
        '_max_hp': 100,
        '_max_shield': 50,
        'position': {'x': 50.0, 'y': 75.0},
        'heading': 0.0,
        'barrel_angle': 0.0,
        'move_speed': 0.0,
        '_top_speed': 5.0,
        '_vision_range': 10.0,
        '_vision_angle': 40.0,
        'ammo_loaded': None,
        'is_overcharged': False,
        '_reload_timer': 0.0,
        'ammo': {'LIGHT': {'count': 10}, 'HEAVY': {'count': 1}}
    },
    'sensor_data': {
        'seen_tanks': [
            {
                'id': 'enemy_1',
                'team': 2,
                'tank_type': 'HEAVY',
                'position': {'x': 120.0, 'y': 75.0},
                'is_damaged': False,
                'heading': 180.0,
                'barrel_angle': 180.0,
                'distance': 70.0
            }
        ],
        'seen_powerups': [],
        'seen_obstacles': []
    },
    'enemies_remaining': 1
}
r = requests.post(BASE + '/agent/action', json=payload, timeout=2.0)
print(r.status_code)
print(pretty(r))

print('\nPOST /agent/destroy')
r = requests.post(BASE + '/agent/destroy', timeout=1.0)
print(r.status_code)
print(pretty(r))

print('\nPOST /agent/end')
r = requests.post(BASE + '/agent/end', json={'damage_dealt': 10, 'tanks_killed': 0}, timeout=1.0)
print(r.status_code)
print(pretty(r))

