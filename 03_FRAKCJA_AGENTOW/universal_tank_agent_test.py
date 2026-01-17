"""Simple local runner for UniversalTankAgent
Run in PowerShell:
python universal_tank_agent_test.py

This script will add the engine to sys.path, import the agent module and call
`get_action` with simple mocked sensor input to validate the interface.
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(r"D:/Polibuda/Sezon_2_Semestr_2/MSI/MSI_project")
ENGINE = ROOT / "02_FRAKCJA_SILNIKA"

if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

try:
    from universal_tank_agent import agent_controller
    print('Imported agent_controller:', type(agent_controller))

    # Mocked sensor data: one enemy at relative position
    mock_my_tank = {
        '_id': 'test_agent',
        '_team': 1,
        'position': {'x': 50.0, 'y': 50.0},
        'heading': 0.0,
        'barrel_angle': 0.0,
        'move_speed': 0.0,
        '_top_speed': 5.0,
        'hp': 100,
        'ammo_loaded': 'BASIC',
        '_reload_timer': 0.0,
    }

    mock_sensor = {
        'seen_tanks': [
            {'id': 'enemy_1', 'distance': 60.0, 'bearing': 0.0, 'position': {'x': 110.0, 'y': 50.0}, 'hp': 80},
        ],
        'seen_powerups': [],
        'seen_obstacles': [],
    }

    action = agent_controller.get_action(0, mock_my_tank, mock_sensor, 1)
    print('Action returned:', action)
except Exception as e:
    print('Error during test run:')
    traceback.print_exc()
    sys.exit(1)

print('Test completed successfully')

