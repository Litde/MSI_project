"""
Universal Tank Agent (particle-search based)

Provides a simple, self-contained agent controller that can be used either
as a module (import and call get_action) or as the backing logic for the
HTTP agent server (controller.server will call into this module).

The agent is tolerant to two kinds of inputs that appear in this project:
- Direct engine objects (Tank, TankSensorData) when used via internal DI
- Serialized dicts (the game engine posts JSON to /agent/action)

API surface expected by tests/engine:
- get_action(current_tick, my_tank_status, sensor_data, enemies_remaining) -> dict
- destroy() -> None
- end(final_score: dict) -> None

The returned dict matches the engine's ActionCommand shape used in JSON:
{ 'barrel_rotation_angle': float, 'heading_rotation_angle': float,
  'move_speed': float, 'ammo_to_load': None, 'should_fire': bool }

This implementation uses a lightweight particle search / open-loop sampling
planner to pick the best first action of sampled action sequences.
"""
from __future__ import annotations

import math
import random
import time
from typing import Any, Dict, List, Optional, Tuple

# Planner hyper-parameters
PARTICLE_COUNT = 150
HORIZON = 5
DELTA_TIME = 1.0 / 60.0
FIRE_ANGLE_THRESHOLD_DEG = 10.0
FIRE_RANGE = 200.0
DEFAULT_HEADING_SPIN_RATE = 90.0  # deg / s
DEFAULT_BARREL_SPIN_RATE = 90.0  # deg / s


# -----------------------------
# Helpers to normalize inputs
# -----------------------------

def _get_field(obj: Any, keys: List[str], default=None):
    """Try to read first available key from dict-like or attribute-like object."""
    if obj is None:
        return default
    # dict-like
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                return obj[k]
        return default
    # object-like
    for k in keys:
        if hasattr(obj, k):
            return getattr(obj, k)
    return default


def _to_numeric_status(my_tank_status: Any) -> Dict[str, Any]:
    """Normalize tank status (dict or object) to a simple dict with numeric fields."""
    s = {}
    s["id"] = _get_field(my_tank_status, ["_id", "id"], "agent")
    s["team"] = _get_field(my_tank_status, ["_team", "team"], 0)
    pos = _get_field(my_tank_status, ["position"], None)
    if isinstance(pos, dict):
        s["x"] = pos.get("x", 0.0)
        s["y"] = pos.get("y", 0.0)
    else:
        # object with .x/.y
        s["x"] = _get_field(pos, ["x"], 0.0)
        s["y"] = _get_field(pos, ["y"], 0.0)

    s["heading"] = _get_field(my_tank_status, ["heading", "_heading"], 0.0)
    s["barrel_angle"] = _get_field(my_tank_status, ["barrel_angle"], s["heading"])
    s["move_speed"] = _get_field(my_tank_status, ["move_speed"], 0.0)
    s["_top_speed"] = _get_field(my_tank_status, ["_top_speed", "top_speed"], 5.0)
    s["hp"] = _get_field(my_tank_status, ["hp", "_hp"], 100)
    s["ammo_loaded"] = _get_field(my_tank_status, ["ammo_loaded"], None)
    s["_reload_timer"] = _get_field(my_tank_status, ["_reload_timer", "reload_timer"], 0.0)
    # spin rates may not be present in dicts -> use defaults
    s["_heading_spin_rate"] = _get_field(my_tank_status, ["_heading_spin_rate"], DEFAULT_HEADING_SPIN_RATE)
    s["_barrel_spin_rate"] = _get_field(my_tank_status, ["_barrel_spin_rate"], DEFAULT_BARREL_SPIN_RATE)

    # ammo counts map (optional)
    ammo = _get_field(my_tank_status, ["ammo"], {})
    if isinstance(ammo, dict):
        try:
            s["ammo_counts"] = {k: (v.get("count") if isinstance(v, dict) else getattr(v, "count", None)) for k, v in ammo.items()}
        except Exception:
            s["ammo_counts"] = {}
    else:
        s["ammo_counts"] = {}

    return s


def _normalize_angle(angle: float) -> float:
    a = angle % 360.0
    if a < 0:
        a += 360.0
    return a


def _angle_diff(a: float, b: float) -> float:
    """Smallest signed difference a - b in degrees."""
    d = (a - b + 180.0) % 360.0 - 180.0
    return d


# -----------------------------
# Sensor helpers
# -----------------------------

def _extract_seen_targets(sensor_data: Any) -> List[Dict[str, Any]]:
    """Return list of targets with position or distance/bearing info normalized."""
    seen = _get_field(sensor_data, ["seen_tanks", "seen_enemies", "seen_entities"], []) or []
    targets = []
    for st in seen:
        t = {}
        t["id"] = _get_field(st, ["id"], None)
        # position may be present as dict or object
        pos = _get_field(st, ["position"], None)
        if isinstance(pos, dict):
            t["x"] = pos.get("x", None)
            t["y"] = pos.get("y", None)
        else:
            t["x"] = _get_field(pos, ["x"], None)
            t["y"] = _get_field(pos, ["y"], None)
        t["distance"] = _get_field(st, ["distance"], None)
        t["bearing"] = _get_field(st, ["bearing"], None)
        t["hp"] = _get_field(st, ["hp"], None)
        targets.append(t)
    return targets


# -----------------------------
# Simple open-loop simulator
# -----------------------------

def _simulate_sequence(
    start_status: Dict[str, Any],
    target: Optional[Dict[str, Any]],
    actions: List[Tuple[float, float, float, bool]],
    horizon: int = HORIZON,
    delta_time: float = DELTA_TIME,
) -> Tuple[float, Dict[str, Any]]:
    """
    Simulate an action sequence and return a numeric score and the final state.
    Actions are tuples: (barrel_delta, heading_delta, move_speed, should_fire)
    This is a very lightweight simulation that approximates motion and firing.
    """
    x = start_status["x"]
    y = start_status["y"]
    heading = start_status["heading"]
    barrel = start_status["barrel_angle"]
    top_speed = start_status.get("_top_speed", 5.0)
    reload_timer = start_status.get("_reload_timer", 0.0)
    ammo_loaded = start_status.get("ammo_loaded", None)

    score = 0.0

    for t in range(horizon):
        if t < len(actions):
            barrel_delta, heading_delta, move_speed_cmd, should_fire = actions[t]
        else:
            barrel_delta, heading_delta, move_speed_cmd, should_fire = (0.0, 0.0, 0.0, False)

        # Apply rotations (no strict spin rate limit here, we assume small deltas sampled)
        heading = _normalize_angle(heading + heading_delta)
        barrel = _normalize_angle(barrel + barrel_delta)

        # Move forward in heading direction using commanded speed (clamped by top_speed)
        speed = max(-top_speed, min(move_speed_cmd, top_speed))
        rad = math.radians(heading)
        x += math.cos(rad) * speed * delta_time
        y += math.sin(rad) * speed * delta_time

        # Decrease reload timer
        if reload_timer > 0:
            reload_timer = max(0.0, reload_timer - delta_time)

        # If firing, evaluate expected damage against target
        if should_fire and ammo_loaded and reload_timer <= 0:
            # Determine distance and bearing to target if available
            if target and target.get("x") is not None and target.get("y") is not None:
                dx = target["x"] - x
                dy = target["y"] - y
                dist = math.hypot(dx, dy)
                bearing_to_target = math.degrees(math.atan2(dy, dx)) % 360.0
            elif target and target.get("distance") is not None and target.get("bearing") is not None:
                dist = target["distance"]
                bearing_to_target = (target["bearing"] + start_status.get("heading", 0.0)) % 360.0
            else:
                dist = float("inf")
                bearing_to_target = 0.0

            angle_error = abs(_angle_diff(barrel, bearing_to_target))

            # Simple hit probability model: falls off with angle and distance
            angle_factor = max(0.0, 1.0 - angle_error / FIRE_ANGLE_THRESHOLD_DEG)
            dist_factor = max(0.0, 1.0 - dist / FIRE_RANGE)
            hit_prob = angle_factor * dist_factor

            expected_damage = hit_prob * 40.0  # approximate damage per shot
            score += expected_damage

            # small bonus for aligning barrel even if not firing
        # alignment bonus
        if target:
            if target.get("x") is not None and target.get("y") is not None:
                dx = target["x"] - x
                dy = target["y"] - y
                dist = math.hypot(dx, dy)
                bearing_to_target = math.degrees(math.atan2(dy, dx)) % 360.0
                angle_error = abs(_angle_diff(barrel, bearing_to_target))
                score += max(0.0, (FIRE_ANGLE_THRESHOLD_DEG - angle_error) * 0.01)
                # prefer closer distances moderately
                score += max(0.0, (FIRE_RANGE - dist) * 0.001)

    final_state = {"x": x, "y": y, "heading": heading, "barrel": barrel}
    return score, final_state


# -----------------------------
# A* PATHFINDING (grid-based, local)
# -----------------------------

def _astar_path(start: Tuple[float, float], goal: Tuple[float, float], obstacles: List[Dict[str, Any]], cell_size: float = 5.0, search_radius: float = 200.0) -> List[Tuple[float, float]]:
    """Compute a simple grid-based A* path from start to goal using obstacles as circular blockers.

    Returns list of waypoints in world coordinates (x,y). If path cannot be found, returns empty list.
    This is intentionally simple and local (not full map) - it's suitable for short-range path planning.
    """
    sx, sy = float(start[0]), float(start[1])
    gx, gy = float(goal[0]), float(goal[1])

    # Bounding box around start/goal
    min_x = min(sx, gx) - search_radius
    max_x = max(sx, gx) + search_radius
    min_y = min(sy, gy) - search_radius
    max_y = max(sy, gy) + search_radius

    # Map dims in cells
    cols = max(3, int((max_x - min_x) / cell_size) + 1)
    rows = max(3, int((max_y - min_y) / cell_size) + 1)

    def world_to_cell(x, y):
        return int((x - min_x) / cell_size), int((y - min_y) / cell_size)

    def cell_to_world(cx, cy):
        return (min_x + (cx + 0.5) * cell_size, min_y + (cy + 0.5) * cell_size)

    # Occupancy grid
    occ = [[False for _ in range(cols)] for _ in range(rows)]

    # Mark obstacles
    for ob in obstacles or []:
        pos = _get_field(ob, ["position"], None)
        if pos is None:
            continue
        ox = _get_field(pos, ["x"], None)
        oy = _get_field(pos, ["y"], None)
        if ox is None or oy is None:
            continue
        # assume obstacle radius
        radius = _get_field(ob, ["size", "_size"], 5.0) or 5.0
        # mark cells within radius as occupied
        min_cx, min_cy = world_to_cell(ox - radius, oy - radius)
        max_cx, max_cy = world_to_cell(ox + radius, oy + radius)
        for cy in range(max(0, min_cy), min(rows, max_cy + 1)):
            for cx in range(max(0, min_cx), min(cols, max_cx + 1)):
                wx, wy = cell_to_world(cx, cy)
                if math.hypot(wx - ox, wy - oy) <= radius + (cell_size * 0.7):
                    occ[cy][cx] = True

    start_cell = world_to_cell(sx, sy)
    goal_cell = world_to_cell(gx, gy)

    # Bounds check
    if not (0 <= start_cell[0] < cols and 0 <= start_cell[1] < rows):
        return []
    if not (0 <= goal_cell[0] < cols and 0 <= goal_cell[1] < rows):
        return []

    # A* search on 4-neighbors
    import heapq

    def heuristic(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    open_set = [(0 + heuristic(start_cell, goal_cell), 0, start_cell, None)]  # (f, g, cell, parent)
    came_from = {}
    gscore = {start_cell: 0}

    while open_set:
        f, g, cell, parent = heapq.heappop(open_set)
        if cell in came_from:
            continue
        came_from[cell] = parent
        if cell == goal_cell:
            break
        cx, cy = cell
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < cols and 0 <= ny < rows):
                continue
            if occ[ny][nx]:
                continue
            ng = g + 1
            ncell = (nx, ny)
            if ncell in gscore and ng >= gscore[ncell]:
                continue
            gscore[ncell] = ng
            heapq.heappush(open_set, (ng + heuristic(ncell, goal_cell), ng, ncell, cell))

    # Reconstruct path
    if goal_cell not in came_from:
        return []
    path = []
    cur = goal_cell
    while cur is not None:
        path.append(cell_to_world(cur[0], cur[1]))
        cur = came_from[cur]
    path.reverse()
    return path


# -----------------------------
# PSO optimizer for aim / short-term pose
# -----------------------------

def _pso_optimize_aim(start_status: Dict[str, Any], target: Optional[Dict[str, Any]], time_budget_ms: float = 20.0) -> Tuple[float, float, float, bool]:
    """Optimize a short action (barrel_delta, heading_delta, move_speed, should_fire) using PSO.

    Returns the single-action tuple for immediate execution.
    """
    if target is None:
        return 0.0, 0.0, 0.0, False

    # PSO parameters
    num_particles = 18
    iterations = 10

    # bounds
    b_barrel = (-12.0, 12.0)
    b_heading = (-20.0, 20.0)
    b_speed = (-start_status.get("_top_speed", 5.0), start_status.get("_top_speed", 5.0))

    # initialize particles
    particles = []
    pbest = []
    pbest_score = []
    gbest = None
    gbest_score = -1e9

    def rand_action():
        return [random.uniform(*b_barrel), random.uniform(*b_heading), random.uniform(*b_speed), random.random() < 0.25]

    for i in range(num_particles):
        act = rand_action()
        particles.append(act)
        pbest.append(act[:])
        score, _ = _simulate_sequence(start_status, target, [tuple(act)], horizon=1)
        pbest_score.append(score)
        if score > gbest_score:
            gbest_score = score
            gbest = act[:]

    # velocities
    velocities = [[0.0, 0.0, 0.0] for _ in range(num_particles)]

    start_t = time.time()
    deadline = start_t + time_budget_ms / 1000.0
    w = 0.5
    c1 = 1.0
    c2 = 1.5

    for it in range(iterations):
        if time.time() > deadline:
            break
        for i in range(num_particles):
            # update velocity for continuous parts (ignore should_fire bool)
            for d in range(3):
                r1 = random.random()
                r2 = random.random()
                velocities[i][d] = w * velocities[i][d] + c1 * r1 * (pbest[i][d] - particles[i][d]) + c2 * r2 * (gbest[d] - particles[i][d])
                particles[i][d] = particles[i][d] + velocities[i][d]
            # clamp
            particles[i][0] = max(b_barrel[0], min(b_barrel[1], particles[i][0]))
            particles[i][1] = max(b_heading[0], min(b_heading[1], particles[i][1]))
            particles[i][2] = max(b_speed[0], min(b_speed[1], particles[i][2]))
            # toggle firing probabilistically if good alignment
            particles[i][3] = random.random() < 0.2 or particles[i][3]

            score, _ = _simulate_sequence(start_status, target, [tuple(particles[i])], horizon=1)
            if score > pbest_score[i]:
                pbest_score[i] = score
                pbest[i] = particles[i][:]
            if score > gbest_score:
                gbest_score = score
                gbest = particles[i][:]

    if gbest is None:
        return 0.0, 0.0, 0.0, False
    return float(gbest[0]), float(gbest[1]), float(gbest[2]), bool(gbest[3])


# -----------------------------
# Genetic Algorithm optimizer for short sequences
# -----------------------------

def _ga_optimize_sequence(start_status: Dict[str, Any], target: Optional[Dict[str, Any]], horizon: int = 4, population: int = 40, gens: int = 6, time_budget_ms: float = 40.0) -> Tuple[float, float, float, bool]:
    """Evolve sequences of actions and return the best first-step found.

    Sequence genes: each gene is (barrel_delta, heading_delta, move_speed, should_fire)
    """
    start_t = time.time()
    deadline = start_t + time_budget_ms / 1000.0

    def random_gene():
        return (random.uniform(-10.0, 10.0), random.uniform(-15.0, 15.0), random.uniform(-start_status.get("_top_speed", 5.0), start_status.get("_top_speed", 5.0)), random.random() < 0.25)

    # initialize population
    pop = [[random_gene() for _ in range(horizon)] for _ in range(population)]
    fitness = [0.0] * population

    def evaluate(idx):
        seq = pop[idx]
        score, _ = _simulate_sequence(start_status, target, seq, horizon=horizon)
        return score

    # initial fitness
    for i in range(population):
        fitness[i] = evaluate(i)

    for g in range(gens):
        if time.time() > deadline:
            break
        # selection by tournament
        new_pop = []
        for _ in range(population // 2):
            # pick parents
            a, b = random.sample(range(population), 2), random.sample(range(population), 2)
            pa = a[0] if fitness[a[0]] > fitness[a[1]] else a[1]
            pb = b[0] if fitness[b[0]] > fitness[b[1]] else b[1]
            parentA = pop[pa]
            parentB = pop[pb]
            # crossover
            cx = random.randint(1, horizon - 1)
            child1 = parentA[:cx] + parentB[cx:]
            child2 = parentB[:cx] + parentA[cx:]
            # mutation small chance
            def mutate(seq):
                new = []
                for gene in seq:
                    if random.random() < 0.12:
                        # mutate gene
                        new.append(random_gene())
                    else:
                        # slight gaussian tweak
                        new.append((gene[0] + random.gauss(0, 2.0), gene[1] + random.gauss(0, 3.0), max(-start_status.get("_top_speed", 5.0), min(start_status.get("_top_speed", 5.0), gene[2] + random.gauss(0, 0.5))), gene[3] if random.random() > 0.05 else not gene[3]))
                return new

            child1 = mutate(child1)
            child2 = mutate(child2)
            new_pop.extend([child1, child2])
        pop = new_pop[:population]
        # evaluate new pop
        for i in range(len(pop)):
            fitness[i] = evaluate(i)

    # pick best
    best_idx = max(range(len(pop)), key=lambda i: fitness[i]) if pop else None
    if best_idx is None:
        return 0.0, 0.0, 0.0, False
    best_seq = pop[best_idx]
    first = best_seq[0]
    return float(first[0]), float(first[1]), float(first[2]), bool(first[3])


# -----------------------------
# Particle search planner
# -----------------------------

def plan_action(
    current_tick: int,
    my_tank_status: Any,
    sensor_data: Any,
    enemies_remaining: int,
    time_budget_ms: float = 80.0,
) -> Dict[str, Any]:
    start = time.time()
    start_status = _to_numeric_status(my_tank_status)
    targets = _extract_seen_targets(sensor_data)

    # Pick the primary target: closest seen tank
    primary = None
    if targets:
        # compute distances if missing
        for t in targets:
            if t.get("distance") is None and t.get("x") is not None:
                dx = float(t["x"]) - float(start_status["x"])
                dy = float(t["y"]) - float(start_status["y"])
                t["distance"] = math.hypot(dx, dy)
        # choose by smallest distance
        primary = min(targets, key=lambda tt: tt.get("distance", float("inf")))

    # Attempt enhanced planners first (A*/PSO/GA) within time budget, fallback to particle search
    time_deadline = start + time_budget_ms / 1000.0

    # If we have a primary with world position, try A* towards it and PSO/GA for aiming
    first_action = None
    if primary and primary.get("x") is not None and primary.get("y") is not None:
        # Build obstacles list from sensor_data
        obstacles = _get_field(sensor_data, ["seen_obstacles"], [])
        # Compute path and choose next waypoint
        try:
            path = _astar_path((start_status["x"], start_status["y"]), (primary["x"], primary["y"]), obstacles)
            if path and len(path) > 1:
                next_wp = path[1]
                dx = float(next_wp[0]) - float(start_status["x"])
                dy = float(next_wp[1]) - float(start_status["y"])
                desired_heading = math.degrees(math.atan2(dy, dx)) % 360.0
                heading_delta = _angle_diff(desired_heading, start_status.get("heading", 0.0))
            else:
                heading_delta = 0.0
        except Exception:
            heading_delta = 0.0

        # Use PSO to optimize immediate aim/move
        rem_time_ms = max(5.0, (time_deadline - time.time()) * 1000.0)
        try:
            pso_barrel, pso_heading, pso_speed, pso_fire = _pso_optimize_aim(start_status, primary, time_budget_ms=min(30.0, rem_time_ms))
            # combine heading from A* and PSO: give PSO heading preference but bias lightly toward path heading
            combined_heading = 0.6 * pso_heading + 0.4 * heading_delta
            first_action = (pso_barrel, combined_heading, pso_speed, pso_fire)
        except Exception:
            first_action = None

        # If time remains, run GA to refine short sequence and override first action
        if time.time() < time_deadline:
            ga_rem_ms = max(5.0, (time_deadline - time.time()) * 1000.0)
            try:
                ga_first = _ga_optimize_sequence(start_status, primary, horizon=min(4, HORIZON), population=30, gens=4, time_budget_ms=ga_rem_ms)
                first_action = ga_first
            except Exception:
                pass

    # If no enhanced planner result, fallback to particle sampling used previously
    if first_action is None:
        # Keep the old particle-search logic but ensure we respect time_budget
        # (we reuse existing candidate_actions logic below)
        # Reuse previous sampling code: generate candidate_actions then evaluate within deadline
        best_score = -1e9
        best_first_action = (0.0, 0.0, 0.0, False)

        particles = PARTICLE_COUNT
        candidate_actions = []
        candidate_actions.append([(0.0, 0.0, 0.0, False) for _ in range(HORIZON)])

        if primary:
            if primary.get("x") is not None and primary.get("y") is not None:
                px = primary.get("x")
                py = primary.get("y")
                try:
                    dx = float(px) - float(start_status["x"])
                    dy = float(py) - float(start_status["y"])
                except Exception:
                    dx = 0.0
                    dy = 0.0
                bearing = math.degrees(math.atan2(dy, dx)) % 360.0
                seq = []
                for h in range(HORIZON):
                    barrel_err = _angle_diff(bearing, start_status["barrel_angle"] if "barrel_angle" in start_status else start_status["heading"]) if h == 0 else 0.0
                    barrel_delta = max(-5.0, min(5.0, barrel_err))
                    heading_delta = 0.0
                    move_cmd = min(start_status.get("_top_speed", 5.0), 0.0)
                    should_fire = (h == 1)
                    seq.append((barrel_delta, heading_delta, move_cmd, should_fire))
                candidate_actions.append(seq)

        for p in range(particles):
            seq = []
            for h in range(HORIZON):
                barrel_delta = random.uniform(-8.0, 8.0)
                heading_delta = random.uniform(-10.0, 10.0)
                move_cmd = random.uniform(-start_status.get("_top_speed", 5.0), start_status.get("_top_speed", 5.0))
                can_fire = bool(start_status.get("ammo_loaded")) and start_status.get("_reload_timer", 0.0) <= 0.0
                should_fire = can_fire and (random.random() < 0.25)
                seq.append((barrel_delta, heading_delta, move_cmd, should_fire))
            candidate_actions.append(seq)

        for seq in candidate_actions:
            if time.time() > time_deadline:
                break
            score, _final = _simulate_sequence(start_status, primary, seq, horizon=HORIZON)
            if score > best_score:
                best_score = score
                best_first_action = seq[0]

        first_action = best_first_action

    # Unpack chosen action
    barrel_delta, heading_delta, move_speed, should_fire = first_action

    action = {
        "barrel_rotation_angle": float(barrel_delta),
        "heading_rotation_angle": float(heading_delta),
        "move_speed": float(move_speed),
        "ammo_to_load": None,
        "should_fire": bool(should_fire),
    }
    return action


# -----------------------------
# Agent interface (module-level)
# -----------------------------

def get_action(current_tick: int, my_tank_status: Any, sensor_data: Any, enemies_remaining: int) -> Dict[str, Any]:
    """Public entry used by test harness and by simple DI servers.

    Accepts both engine objects and JSON-serialized dicts.
    """
    try:
        return plan_action(current_tick, my_tank_status, sensor_data, enemies_remaining)
    except Exception as e:
        # On any failure return a safe no-op action
        return {
            "barrel_rotation_angle": 0.0,
            "heading_rotation_angle": 0.0,
            "move_speed": 0.0,
            "ammo_to_load": None,
            "should_fire": False,
        }


def destroy():
    # Agent notified when destroyed - nothing to clean up for now
    return None


def end(final_score: Dict[str, Any]):
    # Agent receives final scoreboard - can be used for learning/logging
    return None


# make an "agent_controller" object similar to older agents that expose functions
class _AgentControllerProxy:
    def get_action(self, current_tick, my_tank_status, sensor_data, enemies_remaining):
        return get_action(current_tick, my_tank_status, sensor_data, enemies_remaining)

    def destroy(self):
        return destroy()

    def end(self, final_score):
        return end(final_score)


agent_controller = _AgentControllerProxy()
