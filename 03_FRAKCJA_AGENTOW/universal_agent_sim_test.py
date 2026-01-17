"""
Advanced simulation test for the Universal Tank Agent.

This script runs a small headless duel between two mock tanks that use
`universal_tank_agent.agent_controller` for decisions. The simulation is
intentionally lightweight: simple kinematics, reload timers and ammo counts,
and a simple hit model (angle + range).

Run with:
python 03_FRAKCJA_AGENTOW/universal_agent_sim_test.py
"""
import sys
import time
import math
from pathlib import Path

ROOT = Path(r"D:/Polibuda/Sezon_2_Semestr_2/MSI/MSI_project")
ENGINE = ROOT / "02_FRAKCJA_SILNIKA"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

# Import the agent (module under test)
try:
    from universal_tank_agent import agent_controller
except Exception as e:
    print('Failed to import agent_controller from universal_tank_agent:', e)
    raise

# Simulation constants
DELTA_TIME = 1.0 / 60.0
FIRE_ANGLE_THRESHOLD_DEG = 10.0
FIRE_RANGE = 200.0
MAX_TICKS = 600

# Helpers

def _normalize_angle(a: float) -> float:
    a = a % 360.0
    if a < 0:
        a += 360.0
    return a


def _angle_diff(a: float, b: float) -> float:
    d = (a - b + 180.0) % 360.0 - 180.0
    return d


def distance(a, b):
    return math.hypot(a['position']['x'] - b['position']['x'], a['position']['y'] - b['position']['y'])


def bearing_to(a, b):
    dx = b['position']['x'] - a['position']['x']
    dy = b['position']['y'] - a['position']['y']
    return math.degrees(math.atan2(dy, dx)) % 360.0


# Try to import AmmoType for damage/reload values if available
AmmoType = None
try:
    from backend.structures.ammo import AmmoType as AmmoType
except Exception:
    AmmoType = None

# Create two mock tanks as dicts (compatible with agent _to_numeric_status)
teamA = {
    '_id': 'tank_A',
    '_team': 1,
    'position': {'x': 100.0, 'y': 100.0},
    'heading': 0.0,
    'barrel_angle': 0.0,
    'move_speed': 0.0,
    '_top_speed': 5.0,
    'hp': 100,
    'ammo_loaded': 'LIGHT',
    '_reload_timer': 0.0,
    'ammo': {
        'HEAVY': {'count': 1},
        'LIGHT': {'count': 10},
        'LONG_DISTANCE': {'count': 2},
    }
}

teamB = {
    '_id': 'tank_B',
    '_team': 2,
    'position': {'x': 160.0, 'y': 100.0},
    'heading': 180.0,
    'barrel_angle': 180.0,
    'move_speed': 0.0,
    '_top_speed': 5.0,
    'hp': 100,
    'ammo_loaded': 'LIGHT',
    '_reload_timer': 0.0,
    'ammo': {
        'HEAVY': {'count': 1},
        'LIGHT': {'count': 10},
        'LONG_DISTANCE': {'count': 2},
    }
}

all_tanks = {'tank_A': teamA, 'tank_B': teamB}

# Logging
logs = []

print('Starting simulation between tank_A and tank_B for up to', MAX_TICKS, 'ticks')

winner = None
for tick in range(1, MAX_TICKS + 1):
    # Build sensor_data for each tank (simple: always see the other when alive)
    actions = {}
    for tid, tank in list(all_tanks.items()):
        if tank['hp'] <= 0:
            continue
        other = teamB if tid == 'tank_A' else teamA
        sensor = {
            'seen_tanks': [],
            'seen_powerups': [],
            'seen_obstacles': []
        }
        if other['hp'] > 0:
            dist = distance(tank, other)
            sensor['seen_tanks'].append({
                'id': other['_id'],
                'team': other['_team'],
                'tank_type': 'UNKNOWN',
                'position': {'x': other['position']['x'], 'y': other['position']['y']},
                'distance': dist,
                'is_damaged': other['hp'] < 100,
                'heading': other['heading'],
                'barrel_angle': other['barrel_angle']
            })
        # Enemies remaining (simple count)
        enemies_remaining = 1 if other['hp'] > 0 else 0

        # Ask agent for action
        action = agent_controller.get_action(tick, tank, sensor, enemies_remaining)

        # Basic validation
        assert isinstance(action, dict), 'Agent must return a dict'
        for k in ('barrel_rotation_angle', 'heading_rotation_angle', 'move_speed', 'ammo_to_load', 'should_fire'):
            assert k in action, f'Missing field {k} in action'

        actions[tid] = (action, sensor)

    # Apply actions (simple kinematics & firing model)
    for tid, (action, sensor) in actions.items():
        tank = all_tanks[tid]
        if tank['hp'] <= 0:
            continue

        # Rotate heading and barrel
        tank['heading'] = _normalize_angle(tank['heading'] + float(action['heading_rotation_angle']))
        tank['barrel_angle'] = _normalize_angle(tank['barrel_angle'] + float(action['barrel_rotation_angle']))

        # Move
        mv = float(action['move_speed'])
        # clamp
        mv = max(-tank.get('_top_speed', 5.0), min(mv, tank.get('_top_speed', 5.0)))
        rad = math.radians(tank['heading'])
        tank['position']['x'] += math.cos(rad) * mv * DELTA_TIME
        tank['position']['y'] += math.sin(rad) * mv * DELTA_TIME

        # Update reload timer
        if tank['_reload_timer'] > 0:
            tank['_reload_timer'] = max(0.0, tank['_reload_timer'] - DELTA_TIME)

        # Handle firing
        if bool(action.get('should_fire')) and tank['_reload_timer'] <= 0:
            # determine target (first seen)
            seen = sensor.get('seen_tanks', [])
            if seen:
                tgt_info = seen[0]
                # compute bearing and distance to actual target
                other = teamB if tid == 'tank_A' else teamA
                dist = distance(tank, other)
                bearing = bearing_to(tank, other)
                ang_err = abs(_angle_diff(tank['barrel_angle'], bearing))
                if ang_err <= FIRE_ANGLE_THRESHOLD_DEG and dist <= FIRE_RANGE and other['hp'] > 0:
                    # apply damage based on ammo_loaded if AmmoType available
                    ammo_name = tank.get('ammo_loaded')
                    if AmmoType is not None and ammo_name in AmmoType.__members__:
                        at = AmmoType[ammo_name]
                        dmg = abs(at.value_amount)
                        reload_t = float(at.reload_time)
                    else:
                        # fallback
                        dmg = 25
                        reload_t = 5.0

                    other['hp'] -= dmg
                    tank['_reload_timer'] = reload_t
                    # decrement ammo count
                    try:
                        if ammo_name and tank['ammo'] and ammo_name in tank['ammo']:
                            tank['ammo'][ammo_name]['count'] = max(0, int(tank['ammo'][ammo_name]['count']) - 1)
                    except Exception:
                        pass

                    logs.append((tick, tid, 'hit', other['_id'], dmg, other['hp']))
                    print(f"tick {tick}: {tid} hit {other['_id']} for {dmg} hp (target hp={other['hp']})")

    # Check end condition
    if teamA['hp'] <= 0 and teamB['hp'] <= 0:
        winner = 'draw'
        break
    if teamA['hp'] <= 0:
        winner = 'tank_B'
        break
    if teamB['hp'] <= 0:
        winner = 'tank_A'
        break

print('Simulation ended at tick', tick, 'winner:', winner)
print('Final HP -> tank_A:', teamA['hp'], 'tank_B:', teamB['hp'])
print('Logs (first 20):')
for e in logs[:20]:
    print(e)

if winner is None:
    print('No winner after max ticks')

# Simple assertions to ensure simulation ran and produced actions
assert isinstance(teamA['hp'], (int, float))
assert isinstance(teamB['hp'], (int, float))
print('Test completed successfully')
