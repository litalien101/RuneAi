"""Small deterministic game rules. The client never decides world outcomes."""

from __future__ import annotations

from datetime import datetime, timezone
from math import exp, floor, hypot, isfinite, sin
from typing import Any
from .history import HitboxSnapshot, validate_melee_hit

WIDTH, HEIGHT = 20, 14
START = (3, 10)
PLAYER_ENTITY_ID = "9163de5b-b156-5d5e-bb43-f371650c4998"
SECOND_PLAYER_ENTITY_ID = "70968bb0-4c0a-52ad-94ea-3f4f6f1368b2"
PLAYER_ENTITY_IDS = (PLAYER_ENTITY_ID, SECOND_PLAYER_ENTITY_ID)
PLAYER_NAMES = {PLAYER_ENTITY_ID: "Wayfarer", SECOND_PLAYER_ENTITY_ID: "Pathfinder"}
PLAYER_STARTS = {PLAYER_ENTITY_ID: START, SECOND_PLAYER_ENTITY_ID: (4.2, 10.0)}
REGION_ENTITY_ID = "55cab3bc-32ce-5db6-b831-3dbde118912e"
WORLD_ENTITY_IDS = {
    "mara": "a237cd4e-50fb-5448-9f16-88c6a2436d3e",
    "beacon": "b71e10e4-cb79-5c1e-9355-542c1d82512d",
    "reed-west": "b40a2c87-93f0-5988-a1fa-2781a3257b0d",
    "reed-north": "d1de73d7-91b2-5c1e-a6c2-1e7879277533",
    "reed-east": "e8b0b59e-df17-592b-9faf-91d0d14c84d4",
    "reed-south": "f84b7c8d-f829-594e-a086-34cde48d7a84",
    "mossling": "a4e5a9ad-2176-52b4-a130-aeafcdbef021",
}
MARA = {"id": "mara", "name": "Mara the Wayfinder", "x": 7, "y": 7}
BEACON = {"id": "beacon", "name": "The Listening Beacon", "x": 15, "y": 4}
MOSSLING = {"id": "mossling", "name": "Mossling", "x": 12, "y": 9}
REEDS = [
    {"id": "reed-west", "x": 5, "y": 5},
    {"id": "reed-north", "x": 11, "y": 3},
    {"id": "reed-east", "x": 14, "y": 9},
    {"id": "reed-south", "x": 8, "y": 11},
]

def world_manifest() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return the static game world in Atlas entity and relationship form."""
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    entities: list[dict[str, Any]] = [
        *[{"id": player_id, "type": "Player", "created_at": created_at,
          "name": PLAYER_NAMES[player_id], "x": PLAYER_STARTS[player_id][0], "y": PLAYER_STARTS[player_id][1]}
          for player_id in PLAYER_ENTITY_IDS],
        {"id": REGION_ENTITY_ID, "type": "Region", "created_at": created_at,
         "name": "Valley of First Light"},
        {"id": WORLD_ENTITY_IDS["mara"], "type": "NPC", "created_at": created_at,
         "name": MARA["name"], "x": MARA["x"], "y": MARA["y"]},
        {"id": WORLD_ENTITY_IDS["beacon"], "type": "Building", "created_at": created_at,
         "name": BEACON["name"], "x": BEACON["x"], "y": BEACON["y"]},
    ]
    entities.extend(
        {"id": WORLD_ENTITY_IDS[reed["id"]], "type": "ResourceNode", "created_at": created_at,
         "name": "Lumen reed patch", "resource_type": "lumen_reed", "x": reed["x"], "y": reed["y"]}
        for reed in REEDS
    )
    entities.append({"id": WORLD_ENTITY_IDS["mossling"], "type": "Creature", "created_at": created_at,
                     "name": MOSSLING["name"], "x": MOSSLING["x"], "y": MOSSLING["y"]})
    relationships = [
        *[{"type": "located_in", "source_id": player_id, "target_id": REGION_ENTITY_ID}
          for player_id in PLAYER_ENTITY_IDS],
        {"type": "located_in", "source_id": WORLD_ENTITY_IDS["mara"], "target_id": REGION_ENTITY_ID},
        {"type": "placed_in", "source_id": WORLD_ENTITY_IDS["beacon"], "target_id": REGION_ENTITY_ID},
        *[
            {"type": "placed_in", "source_id": WORLD_ENTITY_IDS[reed["id"]], "target_id": REGION_ENTITY_ID}
            for reed in REEDS
        ],
        {"type": "located_in", "source_id": WORLD_ENTITY_IDS["mossling"], "target_id": REGION_ENTITY_ID},
    ]
    return entities, relationships

# Grid values: grass, water, stone path, tall grass, shore.
TERRAIN = [
    "~~~~~~~~~~~~~~~~~~~~",
    "~....g.......g.....~",
    "~...gg......gg.....~",
    "~......g...........~",
    "~.....g.......g....~",
    "~...ggg........g...~",
    "~....g......ggg....~",
    "~..................~",
    "~..gg......g.......~",
    "~...g......g..gg...~",
    "~..........g.......~",
    "~......g...........~",
    "~....ggg......g....~",
    "~~~~~~~~~~~~~~~~~~~~",
]


def seeded(x: int, y: int) -> float:
    value = sin(x * 127.1 + y * 311.7) * 43758.5453
    return value - floor(value)


def _is_path_cell(x: int, y: int) -> bool:
    path = ((2, 10), (4, 9), (6, 9), (7, 8), (8, 7), (10, 7), (11, 6), (12, 6), (13, 5), (15, 4))
    return any(abs(px - x) + abs(py - y) <= 1 for px, py in path)


def _world_obstacles() -> list[dict[str, Any]]:
    props = {(MARA["x"], MARA["y"]), (BEACON["x"], BEACON["y"]), (MOSSLING["x"], MOSSLING["y"]), START}
    props.update((reed["x"], reed["y"]) for reed in REEDS)
    obstacles = [
        {"kind": "tree", "x": x, "z": z, "radius": 0.2, "seed": n, "scale": 0.75 + n * 0.52}
        for z, row in enumerate(TERRAIN)
        for x, cell in enumerate(row)
        if cell == "g" and not _is_path_cell(x, z) and (x, z) not in props
        for n in (seeded(x, z),) if n > 0.37
    ]
    obstacles.extend((
        {"kind": "npc", "x": MARA["x"], "z": MARA["y"], "radius": 0.29},
        {"kind": "beacon", "x": BEACON["x"], "z": BEACON["y"], "radius": 0.62},
        {"kind": "creature", "x": MOSSLING["x"], "z": MOSSLING["y"], "radius": 0.46},
    ))
    return obstacles


WORLD_OBSTACLES = _world_obstacles()

def initial_state() -> dict[str, Any]:
    return {
        "player": {"x": float(START[0]), "y": float(START[1]), "vx": 0.0, "vz": 0.0},
        "inventory": {"lumen_reed": 0},
        "gathered": [],
        "mara_met": False,
        "beacon_awake": False,
        "mossling_health": 3,
        "player_health": 100,
        "player_defense": None,
        "mossling_defeated": False,
        "journal": ["Find Mara near the western path."],
    }

def walkable(x: int, y: int) -> bool:
    return 0 <= x < WIDTH and 0 <= y < HEIGHT and TERRAIN[y][x] != "~"

def walkable_position(x: float, z: float, radius: float = 0.2,
                      obstacles: list[dict[str, Any]] | None = None) -> bool:
    """Check the player footprint against terrain and static scene colliders."""
    for ox, oz in ((-radius, 0), (radius, 0), (0, -radius), (0, radius),
                   (-radius * .7, -radius * .7), (radius * .7, -radius * .7),
                   (-radius * .7, radius * .7), (radius * .7, radius * .7)):
        cell_x = int((x + ox) // 1) if x + ox < 0 else int(x + ox + .5)
        cell_z = int((z + oz) // 1) if z + oz < 0 else int(z + oz + .5)
        if not walkable(cell_x, cell_z):
            return False
    for obstacle in WORLD_OBSTACLES if obstacles is None else obstacles:
        combined_radius = radius + obstacle["radius"]
        if hypot(x - obstacle["x"], z - obstacle["z"]) < combined_radius:
            return False
    return True

def distance(a: dict[str, int], b: dict[str, int]) -> int:
    return hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))

def normalize_movement(value: Any, run: Any) -> dict[str, float]:
    if not isinstance(value, dict) or not isinstance(run, bool):
        raise ValueError("Movement input is invalid.")
    x, z = value.get("x"), value.get("z")
    if isinstance(x, bool) or isinstance(z, bool) or not isinstance(x, (int, float)) or not isinstance(z, (int, float)):
        raise ValueError("Movement direction must contain numeric x and z values.")
    if not isfinite(x) or not isfinite(z) or abs(x) > 1.001 or abs(z) > 1.001:
        raise ValueError("Movement direction is outside the allowed range.")
    magnitude = hypot(x, z)
    if magnitude > 1:
        x, z = x / magnitude, z / magnitude
    return {"x": float(x), "z": float(z)}

def public_state(state: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "width": WIDTH,
        "height": HEIGHT,
        "terrain": TERRAIN,
        "obstacles": WORLD_OBSTACLES,
        "player": {"x": state["player"]["x"], "y": state["player"]["y"]},
        "velocity": {"x": state["player"].get("vx", 0.0), "z": state["player"].get("vz", 0.0)},
        "inventory": state["inventory"],
        "gathered": state["gathered"],
        "mara_met": state["mara_met"],
        "beacon_awake": state["beacon_awake"],
        "player_health": state.get("player_health", 100),
        "player_defense": state.get("player_defense"),
        "mossling": {**MOSSLING, "health": state.get("mossling_health", 3), "max_health": 3,
                     "defeated": state.get("mossling_defeated", False)},
        "journal": state["journal"][-5:],
        "mara": MARA,
        "beacon": BEACON,
        "reed_patches": [reed for reed in REEDS if reed["id"] not in state["gathered"]],
        "events": events,
        "players": state.get("players", []),
    }

def apply_action(state: dict[str, Any], action: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    kind = action.get("type")
    player = state["player"]
    if kind == "move":
        player.setdefault("vx", 0.0)
        player.setdefault("vz", 0.0)
        move_input = normalize_movement(action.get("input"), action.get("run", False))
        sim_input = normalize_movement(action.get("_sim_input", move_input), action.get("_sim_run", action.get("run", False)))
        dt = action.get("dt")
        if isinstance(dt, bool) or not isinstance(dt, (int, float)) or not isfinite(dt) or not 0 <= dt <= .35:
            raise ValueError("Movement tick is outside the allowed range.")
        speed = 4.2 if action.get("_sim_run", action.get("run", False)) else 2.5
        target_vx, target_vz = sim_input["x"] * speed, sim_input["z"] * speed
        braking = hypot(target_vx, target_vz) < 1e-6
        rate = 28.0 if braking else 11.0
        old_vx, old_vz = player.get("vx", 0.0), player.get("vz", 0.0)
        remaining = dt
        vx, vz = old_vx, old_vz
        obstacles = action.get("_obstacles")
        while remaining > 1e-9:
            step = min(remaining, 1 / 60)
            decay = exp(-rate * step)
            next_vx = target_vx + (vx - target_vx) * decay
            next_vz = target_vz + (vz - target_vz) * decay
            displacement_x = target_vx * step + (vx - target_vx) * (1 - decay) / rate
            displacement_z = target_vz * step + (vz - target_vz) * (1 - decay) / rate
            next_x, next_z = player["x"] + displacement_x, player["y"] + displacement_z
            if walkable_position(next_x, player["y"], obstacles=obstacles):
                player["x"] = next_x
                vx = next_vx
            else:
                vx = 0.0
            if walkable_position(player["x"], next_z, obstacles=obstacles):
                player["y"] = next_z
                vz = next_vz
            else:
                vz = 0.0
            remaining -= step
        if braking and hypot(vx, vz) < 0.035:
            vx = vz = 0.0
        player.update(vx=vx, vz=vz)
        return state, "PlayerMoved", {"x": player["x"], "z": player["y"],
            "vx": vx, "vz": vz, "running": action.get("_sim_run", action.get("run", False))}

    if kind == "attack":
        if action.get("target") != MOSSLING["id"]:
            raise ValueError("That creature cannot be attacked.")
        attack_origin = action.get("_attack_origin", player)
        hitbox_at_attack = action.get("_rewound_target", MOSSLING)
        hitbox = HitboxSnapshot(0.0, hitbox_at_attack["x"], 0.0,
                                hitbox_at_attack.get("y", hitbox_at_attack.get("z", 0.0)),
                                hitbox_at_attack.get("radius", .35), hitbox_at_attack.get("height", 1.25))
        origin = (attack_origin["x"], 0.0, attack_origin.get("y", attack_origin.get("z", 0.0)))
        if not validate_melee_hit(origin, hitbox, reach=.45, attacker_radius=.2):
            raise ValueError("Move closer to the Mossling before attacking.")
        if state.get("mossling_defeated", False):
            raise ValueError("The Mossling has already fled into the undergrowth.")
        state["mossling_health"] = max(0, state.get("mossling_health", 3) - 1)
        defense = state.get("player_defense")
        player_damage = 0 if defense in {"guard", "dodge"} else 7
        state["player_health"] = max(0, state.get("player_health", 100) - player_damage)
        state["player_defense"] = None
        defeated = state["mossling_health"] == 0
        state["mossling_defeated"] = defeated
        state["journal"].append("Drove the Mossling back into the undergrowth." if defeated else "Struck the Mossling; it fought back.")
        origin = action.get("_attack_origin", player)
        rewound_target = action.get("_rewound_target", MOSSLING)
        return state, "CreatureDamaged", {"creature_id": MOSSLING["id"], "damage": 1,
            "creature_health": state["mossling_health"], "player_damage": player_damage,
            "defense_used": defense,
            "player_health": state["player_health"], "defeated": defeated,
            "rewind_sequence": action.get("rewind_sequence"),
            "rewind_applied": bool(action.get("_rewind_applied", False)),
            "attack_origin": {"x": origin["x"], "z": origin.get("y", origin.get("z", 0.0))},
            "rewound_target": {"x": rewound_target["x"], "z": rewound_target.get("y", rewound_target.get("z", 0.0))}}

    if kind in {"block", "dodge"}:
        if action.get("target") != MOSSLING["id"]:
            raise ValueError("That creature cannot be targeted by this action.")
        if state.get("mossling_defeated", False):
            raise ValueError("The Mossling has already fled into the undergrowth.")
        if distance(player, MOSSLING) > 1.8:
            raise ValueError("Move closer to the Mossling first.")
        mode = "guard" if kind == "block" else "dodge"
        state["player_defense"] = mode
        if kind == "dodge":
            dx, dz = player["x"] - MOSSLING["x"], player["y"] - MOSSLING["y"]
            length = max(hypot(dx, dz), 1e-6)
            # Sidestep across the attack line so the dodge is visible and
            # remains useful without teleporting the traveler through terrain.
            nx, nz = -dz / length, dx / length
            candidates = ((1, 1), (-1, -1)) if player["x"] <= MOSSLING["x"] else ((-1, 1), (1, -1))
            moved = False
            for sx, sz in candidates:
                next_x, next_z = player["x"] + nx * .7 * sx, player["y"] + nz * .7 * sz
                if walkable_position(next_x, next_z):
                    player.update(x=next_x, y=next_z, vx=0.0, vz=0.0)
                    moved = True
                    break
            if not moved:
                state["player_defense"] = None
                raise ValueError("There is no room to dodge here.")
        state["journal"].append("Sidestepped the Mossling's attack line." if kind == "dodge" else "Raised a guard against the Mossling.")
        return state, "PlayerDodged" if kind == "dodge" else "PlayerGuarded", {
            "creature_id": MOSSLING["id"], "defense": mode,
            "x": round(player["x"], 4), "z": round(player["y"], 4),
            "vx": player.get("vx", 0.0), "vz": player.get("vz", 0.0),
        }

    if kind == "reawaken":
        if action.get("target") != MOSSLING["id"]:
            raise ValueError("That creature cannot be reawakened.")
        if not state.get("mossling_defeated", False):
            raise ValueError("The Mossling is already active.")
        if distance(player, MOSSLING) > 4.0:
            raise ValueError("Move closer to the Mossling's resting place.")
        state["mossling_health"] = 3
        state["mossling_defeated"] = False
        state["player_defense"] = None
        state["journal"].append("The mossling stirred and returned to the valley.")
        return state, "CreatureReawakened", {"creature_id": MOSSLING["id"], "health": 3}

    if kind != "interact":
        raise ValueError("That action is not available.")

    target_id = action.get("target")
    for reed in REEDS:
        if reed["id"] == target_id:
            if distance(player, reed) > 1:
                raise ValueError("Move closer to the glowing reeds.")
            if reed["id"] in state["gathered"]:
                raise ValueError("This patch has already been gathered.")
            state["gathered"].append(reed["id"])
            state["inventory"]["lumen_reed"] += 1
            state["journal"].append("Gathered a lumen reed. The beacon may need three.")
            return state, "ResourceGathered", {"resource": "lumen_reed", "patch_id": reed["id"], "quantity": 1}

    if target_id == MARA["id"]:
        if distance(player, MARA) > 1:
            raise ValueError("Mara is a little farther down the path.")
        first_meeting = not state["mara_met"]
        state["mara_met"] = True
        message = (
            "Mara: The beacon listens for three lumen reeds. Gather them from the quiet patches, "
            "then wake it. The valley has been waiting for a voice."
            if first_meeting else
            "Mara: Three reeds, then the beacon. I'll keep watch here."
        )
        state["journal"].append("Mara told you how to wake the listening beacon.")
        return state, "NPCSpokenTo", {"npc_id": "mara", "first_meeting": first_meeting, "dialogue": message}

    if target_id == BEACON["id"]:
        if distance(player, BEACON) > 1:
            raise ValueError("The old beacon is farther along the eastern trail.")
        if state["beacon_awake"]:
            raise ValueError("The beacon is already awake. Its song carries across the valley.")
        if state["inventory"]["lumen_reed"] < 3:
            raise ValueError("The beacon is quiet. It needs three lumen reeds.")
        state["inventory"]["lumen_reed"] -= 3
        state["beacon_awake"] = True
        state["journal"].append("The listening beacon is awake. A new signal answers from beyond the valley.")
        return state, "BeaconAwakened", {"beacon_id": "beacon", "offering": {"lumen_reed": 3}, "result": "signal_received"}

    raise ValueError("There is nothing to interact with there.")

def replay_event(state: dict[str, Any], event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply a recorded event to a fresh projection; this is the replay contract."""
    if event_type == "PlayerMoved":
        state["player"].update(x=payload["x"], y=payload.get("z", payload.get("y", state["player"]["y"])),
                                vx=payload.get("vx", 0.0), vz=payload.get("vz", 0.0))
    elif event_type == "ResourceGathered":
        patch_id = payload["patch_id"]
        if patch_id not in state["gathered"]:
            state["gathered"].append(patch_id)
            state["inventory"][payload["resource"]] += payload["quantity"]
        state["journal"].append("Gathered a lumen reed. The beacon may need three.")
    elif event_type == "NPCSpokenTo":
        state["mara_met"] = True
        state["journal"].append("Mara told you how to wake the listening beacon.")
    elif event_type == "BeaconAwakened":
        state["inventory"]["lumen_reed"] -= payload["offering"]["lumen_reed"]
        state["beacon_awake"] = True
        state["journal"].append("The listening beacon is awake. A new signal answers from beyond the valley.")
    elif event_type == "CreatureDamaged":
        state["mossling_health"] = payload["creature_health"]
        state["player_health"] = payload["player_health"]
        state["mossling_defeated"] = payload["defeated"]
        state["player_defense"] = None
        state["journal"].append("Drove the Mossling back into the undergrowth." if payload["defeated"] else "Struck the Mossling; it fought back.")
    elif event_type in {"PlayerGuarded", "PlayerDodged"}:
        state["player_defense"] = payload["defense"]
        if event_type == "PlayerDodged":
            state["player"].update(x=payload["x"], y=payload["z"], vx=0.0, vz=0.0)
        state["journal"].append("Sidestepped the Mossling's attack line." if event_type == "PlayerDodged" else "Raised a guard against the Mossling.")
    elif event_type == "CreatureReawakened":
        state["mossling_health"] = payload["health"]
        state["mossling_defeated"] = False
        state["player_defense"] = None
        state["journal"].append("The mossling stirred and returned to the valley.")
    else:
        raise ValueError(f"Unsupported event in world history: {event_type}")
    return state
