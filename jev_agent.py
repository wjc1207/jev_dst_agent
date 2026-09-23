"""Use JEV to choose one bounded DST action from fresh mod telemetry."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

import controller


DEFAULT_API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_INSTALLED_ENV = Path(
    r"C:\Program Files (x86)\Steam\steamapps\common\Don't Starve Together\mods\jev_dst_agent\.env"
)
SAFE_COLLECT_PREFABS = {
    "grass": "Cut grass source; needed urgently for a torch.",
    "sapling": "Twig source; needed urgently for a torch.",
    "sapling_moon": "Twig source; needed for basic crafting.",
    "twigs": "Loose twigs; needed urgently for a torch.",
    "cutgrass": "Loose cut grass; needed urgently for a torch.",
    "flint": "Loose flint; useful for early tools.",
    "log": "Loose log; needed for campfires and early structures.",
    "rocks": "Loose rocks; needed for early tools and structures.",
    "berries": "Food that can reduce early hunger risk.",
    "berrybush": "Berry source if it currently offers a pick action.",
    "berrybush2": "Berry source if it currently offers a pick action.",
    "juicyberrybush": "Berry source if it currently offers a pick action.",
    "carrot": "Loose food.",
    "carrot_planted": "Food source.",
    "seeds": "Low-priority emergency food.",
}
SAFE_FOOD_PREFABS = {
    "berries_cooked",
    "carrot_cooked",
    "berries",
    "carrot",
    "seeds_cooked",
    "seeds",
}
TOOL_MAX_USES = {"axe": 100, "pickaxe": 33}
PICKABLE_SOURCE_PREFABS = {
    "grass",
    "sapling",
    "sapling_moon",
    "berrybush",
    "berrybush2",
    "juicyberrybush",
}


def load_dotenv(path: Path) -> None:
    """Load a minimal KEY=VALUE dotenv file without logging secret values."""
    if not path.exists():
        raise RuntimeError(f".env file was not found: {path}")
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if value and value[0:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        if name:
            os.environ.setdefault(name, value)


def resolve_env_path(explicit: Optional[Path]) -> Path:
    candidates = []
    if explicit is not None:
        candidates.append(explicit)
    candidates.extend(
        [
            Path(__file__).resolve().parent / ".env",
            Path.cwd() / ".env",
            DEFAULT_INSTALLED_ENV,
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError(
        "No .env file found. Put it beside jev_agent.py or pass --env PATH."
    )


def inventory_counts(state: dict) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    inventory = state.get("player", {}).get("inventory", {})
    for item in inventory.get("items", []):
        prefab = str(item.get("prefab", "unknown"))
        counts[prefab] = counts.get(prefab, 0) + int(item.get("count", 1))
    for item in inventory.get("equipped", []):
        prefab = str(item.get("prefab", "unknown"))
        counts[prefab] = counts.get(prefab, 0) + int(item.get("count", 1))
    return counts


def is_weapon_item(item: dict) -> bool:
    return controller.is_weapon_item(item)


def weapon_status(state: dict) -> Tuple[bool, bool, Optional[str]]:
    inventory = state.get("player", {}).get("inventory", {})
    equipped = [item for item in inventory.get("equipped", []) if is_weapon_item(item)]
    carried = [item for item in inventory.get("items", []) if is_weapon_item(item)]
    best = max(
        equipped + carried,
        key=lambda item: float(item.get("weapon_damage") or 0),
        default=None,
    )
    return bool(equipped), bool(equipped or carried), None if best is None else best.get("prefab")


def has_equipped_torch(state: dict) -> bool:
    equipped = state.get("player", {}).get("inventory", {}).get("equipped", [])
    return any(item.get("prefab") == "torch" for item in equipped)


def has_nearby_lit_fire(state: dict, maximum_distance: float = 8.0) -> bool:
    return any(
        entity.get("prefab") in {"campfire", "firepit"}
        and "fire" in entity.get("tags", [])
        and float(entity.get("distance", 999)) <= maximum_distance
        for entity in state.get("nearby", [])
    )


def first_day_survival_knowledge(state: dict) -> list[str]:
    """Return textual game knowledge plus live first-night preparation progress."""
    if state.get("world", {}).get("day") != 1:
        return []

    counts = inventory_counts(state)
    twigs = counts.get("twigs", 0)
    cutgrass = counts.get("cutgrass", 0)
    torches = counts.get("torch", 0)
    missing_twigs = max(0, 2 - twigs)
    missing_grass = max(0, 2 - cutgrass)
    phase = state.get("world", {}).get("phase", "unknown")
    light_ready = torches > 0 or has_nearby_lit_fire(state)
    return [
        "On the first night, darkness attacks and damages an unprotected character; an active light source is required.",
        "One torch costs exactly 2 twigs and 2 cut grass, so gather at least those materials before night.",
	    "Pick up berries, carrots, or seeds to reduce hunger risk; they are optional but useful for early survival.",
        "Grass tufts provide cut grass; saplings and loose twigs provide twigs.",
        "Dusk is the final warning before night. If first-night light is not ready, prioritize missing torch materials and crafting over optional resources.",
        "A crafted torch can be kept unequipped during day and dusk to save durability, then equipped at night when no lit campfire or firepit is nearby.",
        "A campfire is stationary light and costs 2 logs plus 3 cut grass; it is an alternative only when its recipe is available and a suitable site can be reached safely.",
        (
            f"Current first-night preparation state: phase={phase}; twigs={twigs}/2; "
            f"cut_grass={cutgrass}/2; torches={torches}; missing_twigs={missing_twigs}; "
            f"missing_cut_grass={missing_grass}; can_craft_torch="
            f"{bool(state.get('crafting', {}).get('torch', False))}; light_ready={light_ready}."
        ),
    ]


def conservative_tool_uses(state: dict, prefab: str) -> int:
    """Return the minimum possible uses represented by DST's rounded percent."""
    maximum = TOOL_MAX_USES[prefab]
    possible_uses = []
    inventory = state.get("player", {}).get("inventory", {})
    for item in inventory.get("items", []) + inventory.get("equipped", []):
        if item.get("prefab") != prefab or item.get("durability_percent") is None:
            continue
        reported = int(round(float(item["durability_percent"]) * 100))
        matching = [
            uses for uses in range(maximum + 1)
            if int(uses / maximum * 100 + 0.5) == reported
        ]
        if matching:
            possible_uses.append(min(matching))
    return max(possible_uses, default=0)


def has_immediate_threat(state: dict, threshold: float = 5.0) -> bool:
    return any(
        entity.get("attackable") is True
        and float(entity.get("distance", 999)) <= threshold
        for entity in state.get("nearby", [])
    )

def action_allowed(state: dict, action_kind: str) -> tuple[bool, str]:
    """Return (allowed, reason_if_blocked). Filter actions that are meaningless or unsafe."""
    phase = state.get("world", {}).get("phase")
    torch_equipped = has_equipped_torch(state)
    nearby_lit_fire = has_nearby_lit_fire(state)
    immediate_threat = has_immediate_threat(state)

    if immediate_threat:
        allowed = {
            "flee_from_nearest_hostile",
            "equip_weapon",
            "attack_nearest_hostile",
        }
        if action_kind not in allowed:
            return False, "immediate threat: only flee, equip weapon, or attack are allowed"

    # night policy: no chopping or mining
    if phase == "night" and action_kind in {"chop_nearest_tree", "mine_nearest_rock","equip_weapon","attack_nearest_hostile"}:
        return False, "chopping or mining is forbidden at night"

    # night no torch: no movement or collection
    if phase == "night" and not torch_equipped:
        allowed = {
            "wait",
            "craft_torch",
            "equip_torch",
            "build_campfire",
            "flee_from_nearest_hostile",
        }
        if action_kind not in allowed:
            return False, "movement or collection b:locked at night without an equipped torch"

    # day/dusk or nearby fire: no torch equip
    if action_kind in {"equip_torch","build_campfire"} and (phase != "night" or nearby_lit_fire):
        return False, "torch should not be equipped in daylight or beside a lit fire"

    # night equip torch: no unequip torch
    if action_kind == "unequip_torch" and phase == "night" and not nearby_lit_fire:
        return False, "torch should not be unequipped at night without a lit fire"

    return True, ""

def build_candidates(state: dict) -> Tuple[Dict[str, str], Dict[str, dict]]:
    """Return [criteria, dispatch]. Generate available actions. """
    criteria: Dict[str, str] = {}
    dispatch: Dict[str, dict] = {}
    nearest_by_prefab: Dict[str, dict] = {}

    for entity in state.get("nearby", []):
        prefab = str(entity.get("prefab", ""))
        if prefab not in SAFE_COLLECT_PREFABS:
            continue
        tags = set(entity.get("tags", []))
        if prefab in PICKABLE_SOURCE_PREFABS:
            if entity.get("pickable") is not True:
                continue
        elif "pickable" not in tags and "_inventoryitem" not in tags:
            continue
        current = nearest_by_prefab.get(prefab)
        if current is None or float(entity.get("distance", 999)) < float(current.get("distance", 999)):
            nearest_by_prefab[prefab] = entity

    for prefab, entity in sorted(nearest_by_prefab.items()):
        action_id = f"collect_{prefab}"
        distance = float(entity.get("distance", 0))
        criteria[action_id] = (
            f"Approach and collect the nearest observed {prefab}, {distance:.2f} world units away. "
            f"{SAFE_COLLECT_PREFABS[prefab]} Choose only when its benefit exceeds exploration risk."
        )
        dispatch[action_id] = {
            "kind": "collect",
            "prefab": prefab,
            "guid": entity.get("guid"),
        }

    navigation = state.get("navigation", {})
    frontier_target = navigation.get("target")
    frontier_leg = navigation.get("leg")
    if navigation.get("status") == "ready" and frontier_target and frontier_leg:
        criteria["explore"] = (
            f"Continue frontier exploration toward the persistent target at "
            f"({float(frontier_target.get('x', 0)):.2f}, {float(frontier_target.get('z', 0)):.2f}), "
            f"{float(frontier_target.get('distance', 0)):.2f} units away. Walk only the next "
            f"{float(frontier_leg.get('distance', 0)):.2f}-unit leg, then stop and reconsider all actions. "
            f"The frontier has information gain {frontier_target.get('information_gain')} and score "
            f"{frontier_target.get('score')}."
        )
        dispatch["explore"] = {
            "kind": "explore",
            "target_x": frontier_leg.get("x"),
            "target_z": frontier_leg.get("z"),
            "leg_distance": frontier_leg.get("distance"),
        }

    counts = inventory_counts(state)
    torch_count = counts.get("torch", 0)
    torch_equipped = has_equipped_torch(state)
    if state.get("crafting", {}).get("torch", False) and torch_count == 0:
        criteria["craft_torch"] = (
            "Craft one torch now. This option is exposed only because the game reports that the "
            "recipe is currently craftable. Strongly prefer before night, especially during dusk."
        )
        dispatch["craft_torch"] = {"kind": "craft_torch"}
    if torch_count > 0 and not torch_equipped:
        criteria["equip_torch"] = (
            "Equip the existing torch because it is night and no lit campfire or firepit is nearby."
        )
        dispatch["equip_torch"] = {"kind": "equip_torch"}
    if torch_equipped:
        criteria["unequip_torch"] = (
            "Unequip the torch from the hand slot and return it to the backpack to preserve fuel. "
            "Required during day and dusk, and also at night while a lit campfire or firepit is nearby."
        )
        dispatch["unequip_torch"] = {"kind": "unequip_torch"}

    crafting = state.get("crafting", {})
    if counts.get("axe", 0) == 0 and crafting.get("axe", False):
        criteria["craft_axe"] = (
            "Craft a basic axe. Exposed only because the recipe is currently craftable and no axe is held. "
            "Choose when nearby trees or a need for logs justify spending materials."
        )
        dispatch["craft_axe"] = {"kind": "craft_axe"}
    if counts.get("pickaxe", 0) == 0 and crafting.get("pickaxe", False):
        criteria["craft_pickaxe"] = (
            "Craft a basic pickaxe. Exposed only because the recipe is currently craftable and no pickaxe is held. "
            "Choose when nearby mineable rocks justify spending materials."
        )
        dispatch["craft_pickaxe"] = {"kind": "craft_pickaxe"}

    nearby = state.get("nearby", [])
    chop_targets = [
        entity for entity in nearby
        if "CHOP_workable" in entity.get("tags", []) and float(entity.get("distance", 999)) <= 6
    ]
    mine_targets = [
        entity for entity in nearby
        if "MINE_workable" in entity.get("tags", []) and float(entity.get("distance", 999)) <= 6
    ]
    hostile_targets = [
        entity for entity in nearby
        if entity.get("attackable") is True and float(entity.get("distance", 999)) <= 8
    ]
    weapon_equipped, weapon_carried, best_weapon = weapon_status(state)
    axe_uses = conservative_tool_uses(state, "axe")
    pickaxe_uses = conservative_tool_uses(state, "pickaxe")
    eligible_chop_targets = [
        entity for entity in chop_targets
        if entity.get("work_required") is not None
        and axe_uses >= int(entity["work_required"])
    ]
    eligible_mine_targets = [
        entity for entity in mine_targets
        if entity.get("work_required") is not None
        and pickaxe_uses >= int(entity["work_required"])
    ]
    if  eligible_chop_targets:
        target = min(eligible_chop_targets, key=lambda entity: float(entity.get("distance", 999)))
        required = int(target["work_required"])
        criteria["chop_nearest_tree"] = (
            f"Chop the nearest tree-like target ({target.get('prefab')}) completely, continuing until it falls. "
            f"It is {float(target.get('distance', 0)):.2f} units away and is budgeted for {required} chops; "
            f"the selected axe has at least {axe_uses} uses left."
        )
        dispatch["chop_nearest_tree"] = {"kind": "chop_nearest_tree"}
    if  eligible_mine_targets:
        target = min(eligible_mine_targets, key=lambda entity: float(entity.get("distance", 999)))
        required = int(target["work_required"])
        criteria["mine_nearest_rock"] = (
            f"Mine the nearest rock target ({target.get('prefab')}) completely, continuing until it breaks. "
            f"It is {float(target.get('distance', 0)):.2f} units away and requires at most {required} strikes; "
            f"the selected pickaxe has at least {pickaxe_uses} uses left."
        )
        dispatch["mine_nearest_rock"] = {"kind": "mine_nearest_rock"}
    if hostile_targets:
        target = min(hostile_targets, key=lambda entity: float(entity.get("distance", 999)))
        threat_distance = float(target.get("distance", 999))
        criteria["flee_from_nearest_hostile"] = (
            f"Move directly away from the nearest hostile ({target.get('prefab')}) at {threat_distance:.2f} units "
            "in repeated escape legs until no attackable hostile remains nearby or the bounded safety limit is reached. "
            "Strongly prefer when unarmed, badly hurt, or fighting is unnecessary."
        )
        dispatch["flee_from_nearest_hostile"] = {
            "kind": "flee_from_nearest_hostile",
            "guid": target.get("guid"),
        }
        if weapon_carried and not weapon_equipped:
            criteria["equip_weapon"] = (
                f"Equip the best carried weapon ({best_weapon}) before fighting. The hostile is "
                f"{threat_distance:.2f} units away. Prefer fleeing instead if there is no safe time to equip."
            )
            dispatch["equip_weapon"] = {"kind": "equip_weapon"}
        if weapon_equipped and threat_distance <= 6.0:
            vitals_for_combat = state.get("player", {}).get("vitals", {})
            health = float(vitals_for_combat.get("health") or 0)
            health_max = max(float(vitals_for_combat.get("health_max") or 1), 1)
            criteria["attack_nearest_hostile"] = (
                f"Attack the nearest hostile ({target.get('prefab')}) once with the equipped weapon at "
                f"{threat_distance:.2f} units. Health is {health:.1f}/{health_max:.1f}; prefer fleeing at low health "
                "or when combat is not necessary. Never attacks neutral creatures."
            )
            dispatch["attack_nearest_hostile"] = {"kind": "attack_nearest_hostile"}

    vitals = state.get("player", {}).get("vitals", {})
    hunger = float(vitals.get("hunger") or 0)
    hunger_max = max(float(vitals.get("hunger_max") or 1), 1)
    safe_food_count = sum(counts.get(prefab, 0) for prefab in SAFE_FOOD_PREFABS)
    if safe_food_count > 0 and hunger / hunger_max < 0.80:
        criteria["eat_safe_food"] = (
            f"Eat one whitelisted safe food item. Hunger is {hunger:.1f}/{hunger_max:.1f}; "
            "choose when preserving hunger is more important than saving food."
        )
        dispatch["eat_safe_food"] = {"kind": "eat_safe_food"}

    if crafting.get("campfire", False):
        criteria["build_campfire"] = (
            "Build a campfire at the nearest valid open position. Exposed only because the recipe is craftable, "
            "it is night, and no lit campfire or firepit is nearby."
        )
        dispatch["build_campfire"] = {"kind": "build_campfire"}

    criteria["wait"] = (
        "Take no action for one telemetry interval. Choose only when moving or collecting is less safe."
    )
    dispatch["wait"] = {"kind": "wait"}

    # filter out any actions that are blocked by hard safety rules
    criteria = {
        k: v for k, v in criteria.items()
        if action_allowed(state, dispatch[k]["kind"])[0]
    }
    dispatch = {k: v for k, v in dispatch.items() if k in criteria}
    
    return criteria, dispatch


def compact_game_state(state: dict) -> dict:
    world = state.get("world", {})
    player = state.get("player", {})
    vitals = player.get("vitals", {})
    weapon_equipped, weapon_carried, best_weapon = weapon_status(state)
    compact = {
        "goal": "Survive indefinitely and steadily improve food, light, tools, resources, and safety.",
        "hard_rules": [
            "When a hostile is close, flee if unarmed; with an equipped weapon, fight only when safer than fleeing.",
            "Never equip a torch during day or dusk; equip it at night only when no lit campfire or firepit is nearby.",
            "Build a campfire only at night. At night beside a lit campfire or firepit, unequip the torch.",
            "Prefer nearby required resources over optional food or flowers.",
        ],
        "day": world.get("day"),
        "phase": world.get("phase"),
        "world_time": world.get("time"),
        "health": vitals.get("health"),
        "hunger": vitals.get("hunger"),
        "sanity": vitals.get("sanity"),
        "dead": vitals.get("dead"),
        "position": player.get("position"),
        "navigation": state.get("navigation", {}),
        "inventory": inventory_counts(state),
        "torch_equipped": has_equipped_torch(state),
        "nearby_lit_fire": has_nearby_lit_fire(state),
        "weapon_equipped": weapon_equipped,
        "weapon_carried": weapon_carried,
        "best_weapon": best_weapon,
        "can_craft_torch": state.get("crafting", {}).get("torch", False),
        "can_craft_axe": state.get("crafting", {}).get("axe", False),
        "can_craft_pickaxe": state.get("crafting", {}).get("pickaxe", False),
        "can_craft_campfire": state.get("crafting", {}).get("campfire", False),
        "nearby": [
            {
                "prefab": entity.get("prefab"),
                "distance": entity.get("distance"),
                "tags": entity.get("tags", []),
                "pickable": entity.get("pickable"),
                "work_required": entity.get("work_required"),
                "attackable": entity.get("attackable", False),
            }
            for entity in state.get("nearby", [])
        ],
    }
    knowledge = first_day_survival_knowledge(state)
    if knowledge:
        compact["knowledge"] = knowledge
    return compact


def call_jev(api_url: str, api_key: str, model: str, state: dict, criteria: Dict[str, str]) -> dict:
    body = {
        "state": json.dumps(compact_game_state(state), ensure_ascii=False, separators=(",", ":")),
        "model": model,
        "questions": {
            "next_action": {
                "type": "choice",
                "instructions": (
                    "Choose exactly one available action that most improves long-term survival. "
                    "Respect every hard rule in the state and use any textual knowledge in the state "
                    "as game-mechanics context."
                ),
                "criteria": criteria,
            }
        },
    }
    request = urllib.request.Request(
        api_url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "jev-dst-agent/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"JEV API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"JEV API connection failed: {exc.reason}") from exc

    answer = payload.get("answers", {}).get("next_action")
    if not isinstance(answer, dict) or not answer.get("choice"):
        raise RuntimeError("JEV response did not contain answers.next_action.choice")
    return {"response": payload, "answer": answer}


def execute_action(action: dict, log_path: Path, move_seconds: float = 1.0) -> None:
    kind = action["kind"]
    if kind == "collect":
        controller.collect(log_path, action["prefab"], preferred_guid=action.get("guid"))
    elif kind == "explore":
        controller.explore_leg(
            log_path,
            float(action["target_x"]),
            float(action["target_z"]),
            float(action["leg_distance"]),
            move_seconds,
        )
    elif kind == "wait":
        time.sleep(1.0)
    elif kind == "craft_torch":
        controller.craft_torch(log_path)
    elif kind == "equip_torch":
        controller.equip_torch(log_path)
    elif kind == "unequip_torch":
        controller.unequip_torch(log_path)
    elif kind == "craft_axe":
        controller.craft_inventory_item(log_path, "axe", "craft_axe")
    elif kind == "craft_pickaxe":
        controller.craft_inventory_item(log_path, "pickaxe", "craft_pickaxe")
    elif kind == "build_campfire":
        controller.build_campfire(log_path)
    elif kind == "eat_safe_food":
        controller.eat_safe_food(log_path)
    elif kind == "chop_nearest_tree":
        controller.complete_work_action(log_path, kind, "CHOP_workable")
    elif kind == "mine_nearest_rock":
        controller.complete_work_action(log_path, kind, "MINE_workable")
    elif kind == "attack_nearest_hostile":
        controller.force_attack(log_path)
    elif kind == "equip_weapon":
        controller.equip_weapon(log_path)
    elif kind == "flee_from_nearest_hostile":
        controller.flee_from_hostile(log_path, preferred_guid=action.get("guid"))
    else:
        raise RuntimeError(f"Unsupported bounded action kind: {kind}")


def default_execution_threshold(choice: str) -> float:
    """Use lower gates for reversible actions and higher gates for committed ones."""
    if choice == "explore":
        return 0.15
    if choice.startswith("collect_"):
        return 0.25
    if choice == "wait":
        return 0.0
    if choice == "craft_torch":
        return 0.45
    if choice == "equip_torch":
        return 0.25
    if choice == "unequip_torch":
        return 0.25
    if choice in {"craft_axe", "craft_pickaxe"}:
        return 0.45
    if choice in {"chop_nearest_tree", "mine_nearest_rock"}:
        return 0.45
    if choice == "eat_safe_food":
        return 0.35
    if choice == "build_campfire":
        return 0.45
    if choice == "flee_from_nearest_hostile":
        return 0.0
    if choice == "equip_weapon":
        return 0.25
    if choice == "attack_nearest_hostile":
        return 0.45
    return 0.70


def run_decision_cycle(args, api_url: str, api_key: str, model: str) -> bool:
    """Run one sense-decide-act cycle. Return True at a terminal game state."""
    state, _ = controller.latest_state(args.log)
    world = state.get("world", {})
    vitals = state.get("player", {}).get("vitals", {})
    if vitals.get("dead"):
        print("goal_failed: player is dead; control loop stopped")
        return True
    criteria, dispatch = build_candidates(state)
    result = call_jev(api_url, api_key, model, state, criteria)
    answer = result["answer"]
    choice = answer["choice"]
    confidence = float(answer.get("confidence", 0))
    print(
        f"day={world.get('day')} phase={world.get('phase')} "
        f"model={result['response'].get('model', model)} "
        f"choice={choice} confidence={confidence:.3f}"
    )
    if args.show_probabilities:
        print(json.dumps(answer.get("probabilities", {}), indent=2, sort_keys=True))

    if choice not in dispatch:
        raise RuntimeError(f"JEV selected unknown action: {choice}")
    if not args.execute:
        print("dry_run: no game input sent")
        return False

    threshold = (
        args.min_confidence
        if args.min_confidence is not None
        else default_execution_threshold(choice)
    )
    print(f"execution_threshold={threshold:.2f}")
    if confidence < threshold:
        print(f"abstained: confidence below {threshold:.2f}")
        return False

    phase = state.get("world", {}).get("phase")
    night_safe_kinds = {
        "wait",
        "craft_torch",
        "equip_torch",
        "build_campfire",
        "eat_safe_food",
        "flee_from_nearest_hostile",
    }
    if phase == "night" and not has_equipped_torch(state) and dispatch[choice]["kind"] not in night_safe_kinds:
        print("abstained: hard safety rule blocks movement or collection at night without an equipped torch")
        return False

    execute_action(dispatch[choice], args.log, args.move_seconds)
    print("action_executed")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, help="Path to the .env file")
    parser.add_argument("--log", type=Path, default=controller.DEFAULT_LOG)
    parser.add_argument("--execute", action="store_true", help="Execute the selected action")
    parser.add_argument(
        "--min-confidence",
        type=float,
        help="Override the action-specific execution threshold",
    )
    parser.add_argument("--show-probabilities", action="store_true")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Continuously sense, ask JEV, and optionally execute until death or interruption",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="Seconds to wait after each completed cycle (default: 0, no cooldown)",
    )
    parser.add_argument(
        "--move-seconds",
        type=float,
        default=1.0,
        help="Duration of each exploratory movement (default: 1.0, range: 0.2-2.0)",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        sample = {
            "world": {"day": 1, "phase": "day", "time": 0.2},
            "player": {"vitals": {"health": 150, "hunger": 140, "sanity": 200}, "inventory": {"items": []}},
            "crafting": {"torch": False},
            "nearby": [
                {
                    "guid": 101,
                    "prefab": "grass",
                    "distance": 3.0,
                    "tags": ["pickable"],
                    "pickable": True,
                }
            ],
        }
        criteria, dispatch = build_candidates(sample)
        assert "collect_grass" in criteria
        assert dispatch["collect_grass"] == {
            "kind": "collect",
            "prefab": "grass",
            "guid": 101,
        }
        depleted = dict(sample)
        depleted["nearby"] = [
            {"prefab": "grass", "distance": 1.0, "tags": [], "pickable": False},
            {"prefab": "sapling", "distance": 1.0, "tags": [], "pickable": False},
            {"prefab": "berrybush", "distance": 1.0, "tags": [], "pickable": False},
        ]
        depleted_criteria, _ = build_candidates(depleted)
        assert "collect_grass" not in depleted_criteria
        assert "collect_sapling" not in depleted_criteria
        assert "collect_berrybush" not in depleted_criteria
        loose_resources = dict(sample)
        loose_resources["nearby"] = [
            {"guid": 102, "prefab": "log", "distance": 2.0, "tags": ["_inventoryitem"]},
            {"guid": 103, "prefab": "rocks", "distance": 2.5, "tags": ["_inventoryitem"]},
        ]
        loose_criteria, loose_dispatch = build_candidates(loose_resources)
        assert loose_dispatch["collect_log"] == {"kind": "collect", "prefab": "log", "guid": 102}
        assert loose_dispatch["collect_rocks"] == {"kind": "collect", "prefab": "rocks", "guid": 103}
        assert compact_game_state(sample)["goal"].startswith("Survive")
        assert default_execution_threshold("explore") == 0.15
        assert default_execution_threshold("collect_grass") == 0.25
        assert default_execution_threshold("craft_torch") == 0.45
        assert default_execution_threshold("build_campfire") == 0.45
        assert default_execution_threshold("wait") == 0.0
        assert "craft_torch" not in criteria
        craftable = dict(sample)
        craftable["crafting"] = {"torch": True}
        craft_criteria, craft_dispatch = build_candidates(craftable)
        assert "craft_torch" in craft_criteria
        assert craft_dispatch["craft_torch"] == {"kind": "craft_torch"}
        equipped = {
            "world": {"day": 1, "phase": "day"},
            "crafting": {"torch": False},
            "player": {"inventory": {"items": [], "equipped": [{"prefab": "torch", "count": 1}]}},
            "nearby": [],
        }
        equipped_criteria, equipped_dispatch = build_candidates(equipped)
        assert "unequip_torch" in equipped_criteria
        assert equipped_dispatch["unequip_torch"] == {"kind": "unequip_torch"}
        equipped["world"]["phase"] = "night"
        night_criteria, _ = build_candidates(equipped)
        assert "unequip_torch" not in night_criteria
        survival = {
            "world": {"day": 1, "phase": "night"},
            "crafting": {"torch": False, "axe": True, "pickaxe": True, "campfire": True},
            "player": {
                "vitals": {"health": 150, "hunger": 50, "hunger_max": 150, "sanity": 200},
                "inventory": {"items": [{"prefab": "berries", "count": 2}], "equipped": []},
            },
            "nearby": [],
        }
        survival_criteria, _ = build_candidates(survival)
        assert "build_campfire" in survival_criteria
        assert "craft_torch" not in survival_criteria
        durable = {
            "world": {"day": 1, "phase": "day"},
            "crafting": {},
            "player": {
                "vitals": {"health": 150, "hunger": 100, "hunger_max": 150, "sanity": 200},
                "inventory": {
                    "items": [{"prefab": "axe", "count": 1, "durability_percent": 0.15}],
                    "equipped": [],
                },
            },
            "nearby": [
                {"prefab": "evergreen", "distance": 3, "tags": ["CHOP_workable"], "work_required": 15}
            ],
        }
        durable_criteria, _ = build_candidates(durable)
        assert "chop_nearest_tree" in durable_criteria
        durable["player"]["inventory"]["items"][0]["durability_percent"] = 0.14
        worn_criteria, _ = build_candidates(durable)
        assert "chop_nearest_tree" not in worn_criteria
        print("self-test passed")
        return 0

    env_path = resolve_env_path(args.env)
    load_dotenv(env_path)
    api_key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        raise RuntimeError(f"JEV_API_KEY is missing or empty in {env_path}")
    api_url = os.environ.get("JEV_API_URL", DEFAULT_API_URL)
    model = os.environ.get("JEV_MODEL", DEFAULT_MODEL)
    if not 0.0 <= args.interval <= 60.0:
        raise ValueError("--interval must be between 0 and 60 seconds")
    if not 0.2 <= args.move_seconds <= 2.0:
        raise ValueError("--move-seconds must be between 0.2 and 2.0 seconds")

    if args.loop:
        mode = "execute" if args.execute else "dry-run"
        print(f"control_loop_started mode={mode} interval={args.interval:.1f}s")

    cycle = 0
    consecutive_errors = 0
    while True:
        cycle += 1
        if args.loop:
            print(f"cycle={cycle}")
        try:
            terminal = run_decision_cycle(args, api_url, api_key, model)
            consecutive_errors = 0
        except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
            if not args.loop:
                raise
            consecutive_errors += 1
            retry_base = max(args.interval, 1.0)
            retry_delay = min(retry_base * (2 ** min(consecutive_errors - 1, 3)), 30.0)
            print(
                f"cycle_error={exc}; retrying_in={retry_delay:.1f}s",
                file=sys.stderr,
            )
            time.sleep(retry_delay)
            continue

        if terminal or not args.loop:
            return 0
        if args.interval > 0:
            time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        controller.release_movement_keys()
        print("control_loop_stopped: keyboard interrupt")
        raise SystemExit(130)
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
