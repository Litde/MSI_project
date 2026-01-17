"""
Simple agent server runner for testing the universal agent.

Usage (for local testing):
  python 03_FRAKCJA_AGENTOW/run_agent_server.py --host 127.0.0.1 --port 8001

This starts a minimal FastAPI app that delegates to the `universal_tank_agent.agent_controller`.
It implements the endpoints the engine expects:
  GET /             -> health check
  POST /agent/action -> returns action JSON
  POST /agent/destroy -> notifies agent
  POST /agent/end -> sends final scoreboard

This file is intended for quick local testing; for production the engine's `controller/server.py`
can be used instead.
"""
from __future__ import annotations
import argparse
import uvicorn
from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import Any, Dict
from pathlib import Path
import sys

# Ensure engine package is importable (02_FRAKCJA_SILNIKA) when running from this folder
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = PROJECT_ROOT / "02_FRAKCJA_SILNIKA"
if str(ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(ENGINE_PATH))

# Import our agent module
try:
    from universal_tank_agent import agent_controller
except Exception as e:
    raise RuntimeError(f"Failed to import universal_tank_agent: {e}")

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "agent": "universal_tank_agent"}


class ActionRequest(BaseModel):
    current_tick: int
    my_tank_status: Dict[str, Any]
    sensor_data: Dict[str, Any]
    enemies_remaining: int


@app.post("/agent/action")
async def agent_action(payload: ActionRequest):
    # Delegate to agent_controller.get_action
    try:
        action = agent_controller.get_action(payload.current_tick, payload.my_tank_status, payload.sensor_data, payload.enemies_remaining)
        # Ensure JSON-serializable
        return action
    except Exception as e:
        return {"error": str(e)}


@app.post("/agent/destroy")
async def agent_destroy(req: Request):
    try:
        agent_controller.destroy()
    except Exception:
        pass
    return {"status": "destroy_received"}


@app.post("/agent/end")
async def agent_end(payload: Dict[str, Any]):
    try:
        agent_controller.end(payload)
    except Exception:
        pass
    return {"status": "end_received"}


def main():
    parser = argparse.ArgumentParser(description="Run universal agent server (FastAPI uvicorn)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    print(f"Starting universal agent server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
