"""Offline regressions for action exposure, target selection and completion."""

from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, call, patch

import controller
import jev_agent
import telemetry


def grass(guid: int, distance: float, pickable: bool) -> dict:
    return {
        "guid": guid,
        "prefab": "grass",
        "distance": distance,
        "dx": distance,
        "dz": 0,
        "tags": ["pickable"] if pickable else [],
        "pickable": pickable,
    }


def hostile(guid: int, distance: float) -> dict:
    return {
        "guid": guid,
        "prefab": "hound",
        "distance": distance,
        "dx": distance,
        "dz": 0,
        "tags": ["hostile", "monster"],
        "attackable": True,
        "activeThreat": True,
    }


def lit_campfire(guid: int = 40, distance: float = 3.0) -> dict:
    return {
        "guid": guid,
        "prefab": "campfire",
        "distance": distance,
        "dx": distance,
        "dz": 0,
        "tags": ["campfire", "fire"],
        "attackable": False,
    }


def state(nearby: list[dict], cutgrass: int = 0) -> dict:
    items = []
    if cutgrass:
        items.append({"prefab": "cutgrass", "count": cutgrass})
    return {
        "world": {"day": 1, "phase": "day", "time": 0.2},
        "player": {
            "vitals": {"health": 150, "hunger": 140, "hunger_max": 150, "sanity": 200},
            "inventory": {"items": items, "equipped": []},
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
        },
        "crafting": {"torch": False, "axe": False, "pickaxe": False, "campfire": False},
        "nearby": nearby,
        "navigation": {
            "mode": "frontier",
            "status": "ready",
            "target": {
                "x": 0.0,
                "z": 12.0,
                "distance": 12.0,
                "information_gain": 4,
                "visits": 0,
                "score": 34.0,
            },
            "leg": {"x": 0.0, "z": 4.0, "dx": 0.0, "dz": 4.0, "distance": 4.0},
        },
        "camera": {
            "right": {"x": 1, "z": 0},
            "forward": {"x": 0, "z": 1},
        },
    }


class CandidateTests(unittest.TestCase):
    def test_first_day_state_contains_textual_survival_knowledge(self) -> None:
        observed = state([])
        observed["player"]["inventory"]["items"] = [
            {"prefab": "twigs", "count": 1},
            {"prefab": "cutgrass", "count": 2},
        ]

        compact = jev_agent.compact_game_state(observed)

        self.assertIn("knowledge", compact)
        self.assertTrue(any("darkness attacks" in text for text in compact["knowledge"]))
        status = compact["knowledge"][-1]
        self.assertIn("twigs=1/2", status)
        self.assertIn("cut_grass=2/2", status)
        self.assertIn("missing_twigs=1", status)
        self.assertIn("missing_cut_grass=0", status)

    def test_first_day_knowledge_is_omitted_after_day_one(self) -> None:
        observed = state([])
        observed["world"]["day"] = 2

        compact = jev_agent.compact_game_state(observed)

        self.assertNotIn("knowledge", compact)

    def test_explore_is_omitted_without_a_reachable_frontier(self) -> None:
        observed = state([])
        observed["navigation"] = {
            "mode": "frontier",
            "status": "no_frontier",
            "target": None,
            "leg": None,
        }

        criteria, _ = jev_agent.build_candidates(observed)

        self.assertNotIn("explore", criteria)

    def test_frontier_explore_is_one_semantic_action(self) -> None:
        criteria, dispatch = jev_agent.build_candidates(state([]))

        self.assertIn("explore", criteria)
        self.assertNotIn("explore_up", criteria)
        self.assertEqual(dispatch["explore"]["kind"], "explore")
        self.assertEqual(dispatch["explore"]["target_z"], 4.0)
        self.assertEqual(dispatch["explore"]["frontier_z"], 12.0)

    def test_torch_is_never_equipped_during_day_or_dusk(self) -> None:
        for phase in ("day", "dusk"):
            observed = state([])
            observed["world"]["phase"] = phase
            observed["player"]["inventory"]["items"] = [{"prefab": "torch", "count": 1}]

            criteria, _ = jev_agent.build_candidates(observed)

            self.assertNotIn("equip_torch", criteria)

    def test_equipped_torch_is_an_option_during_day_and_dusk_without_forcing(self) -> None:
        for phase in ("day", "dusk"):
            observed = state([])
            observed["world"]["phase"] = phase
            observed["player"]["inventory"]["equipped"] = [{"prefab": "torch", "count": 1}]

            criteria, _ = jev_agent.build_candidates(observed)

            self.assertIn("unequip_torch", criteria)
            self.assertIn("wait", criteria)
            self.assertIn("explore", criteria)
            self.assertNotIn("equip_torch", criteria)

    def test_night_without_fire_exposes_equip_torch(self) -> None:
        observed = state([])
        observed["world"]["phase"] = "night"
        observed["player"]["inventory"]["items"] = [{"prefab": "torch", "count": 1}]

        criteria, _ = jev_agent.build_candidates(observed)

        self.assertIn("equip_torch", criteria)
        self.assertNotIn("unequip_torch", criteria)

    def test_night_beside_lit_fire_offers_unequip_without_forcing(self) -> None:
        observed = state([lit_campfire()])
        observed["world"]["phase"] = "night"
        observed["player"]["inventory"]["equipped"] = [{"prefab": "torch", "count": 1}]

        criteria, _ = jev_agent.build_candidates(observed)

        self.assertIn("unequip_torch", criteria)
        self.assertIn("wait", criteria)
        self.assertIn("explore", criteria)
        self.assertNotIn("equip_torch", criteria)

    def test_campfire_is_built_only_at_night_without_lit_fire(self) -> None:
        dusk = state([])
        dusk["world"]["phase"] = "dusk"
        dusk["crafting"]["campfire"] = True
        night = state([])
        night["world"]["phase"] = "night"
        night["crafting"]["campfire"] = True
        safe_night = state([lit_campfire()])
        safe_night["world"]["phase"] = "night"
        safe_night["crafting"]["campfire"] = True

        dusk_criteria, _ = jev_agent.build_candidates(dusk)
        night_criteria, _ = jev_agent.build_candidates(night)
        safe_criteria, _ = jev_agent.build_candidates(safe_night)

        self.assertNotIn("build_campfire", dusk_criteria)
        self.assertIn("build_campfire", night_criteria)
        self.assertNotIn("build_campfire", safe_criteria)

    def test_depleted_near_grass_does_not_hide_live_far_grass(self) -> None:
        observed = state([grass(10, 0.5, False), grass(20, 2.0, True)])

        criteria, dispatch = jev_agent.build_candidates(observed)

        self.assertIn("collect_grass", criteria)
        self.assertEqual(dispatch["collect_grass"]["guid"], 20)

    def test_no_grass_action_when_every_patch_is_depleted(self) -> None:
        criteria, _ = jev_agent.build_candidates(
            state([grass(10, 0.5, False), grass(20, 2.0, False)])
        )

        self.assertNotIn("collect_grass", criteria)

    def test_immediate_hostile_unarmed_exposes_only_flee(self) -> None:
        criteria, _ = jev_agent.build_candidates(state([grass(10, 2.0, True), hostile(30, 4.0)]))

        self.assertEqual(set(criteria), {"flee_from_nearest_hostile"})

    def test_low_hunger_cannot_remove_flee_during_attack(self) -> None:
        observed = state([hostile(30, 4.0)])
        observed["player"]["vitals"]["hunger"] = 10

        criteria, _ = jev_agent.build_candidates(observed)

        self.assertEqual(set(criteria), {"flee_from_nearest_hostile"})

    def test_attackable_non_pursuer_does_not_offer_flee(self) -> None:
        observed = state([hostile(30, 4.0)])
        observed["nearby"][0]["activeThreat"] = False

        criteria, _ = jev_agent.build_candidates(observed)

        self.assertNotIn("flee_from_nearest_hostile", criteria)

    def test_pursuer_can_be_fled_even_when_not_attackable(self) -> None:
        observed = state([hostile(30, 4.0)])
        observed["nearby"][0]["attackable"] = False

        criteria, dispatch = jev_agent.build_candidates(observed)

        self.assertIn("flee_from_nearest_hostile", criteria)
        self.assertEqual(dispatch["flee_from_nearest_hostile"]["guid"], 30)

    def test_pursuer_in_twelve_unit_escape_range_offers_flee(self) -> None:
        criteria, _ = jev_agent.build_candidates(state([hostile(30, 10.0)]))

        self.assertIn("flee_from_nearest_hostile", criteria)

    def test_carried_weapon_must_be_equipped_before_attack(self) -> None:
        observed = state([hostile(30, 4.0)])
        observed["player"]["inventory"]["items"] = [
            {"prefab": "spear", "count": 1, "weapon": True, "weapon_damage": 34}
        ]

        criteria, _ = jev_agent.build_candidates(observed)

        self.assertEqual(set(criteria), {"flee_from_nearest_hostile", "equip_weapon"})

    def test_equipped_weapon_allows_attack_or_flee(self) -> None:
        observed = state([hostile(30, 4.0)])
        observed["player"]["inventory"]["equipped"] = [
            {"prefab": "spear", "count": 1, "weapon": True, "weapon_damage": 34}
        ]

        criteria, _ = jev_agent.build_candidates(observed)

        self.assertEqual(set(criteria), {"flee_from_nearest_hostile", "attack_nearest_hostile"})


class ControllerTests(unittest.TestCase):
    def test_explore_walks_one_frontier_leg(self) -> None:
        before = state([])
        after = state([])
        after["player"]["position"]["z"] = 3.5
        tap = Mock()
        release = Mock()
        controller.EXPLORATION_STALL.update(target=None, count=0, last_position=None)

        with (
            patch.object(controller, "latest_state", return_value=(before, 100)),
            patch.object(controller, "find_game_window", return_value=123),
            patch.object(controller, "focus_game"),
            patch.object(controller, "wait_for_fresh_state", return_value=(after, 101)),
            patch.object(controller, "tap", tap),
            patch.object(controller, "release_movement_keys", release),
        ):
            controller.explore_leg(
                Path("unused.log"),
                target_x=0.0,
                target_z=4.0,
                leg_distance=4.0,
                full_leg_seconds=1.0,
            )

        tap.assert_called_once_with([controller.VK["up"]], 1.0)
        release.assert_called_once_with()

    def test_two_stalled_exploration_legs_reject_current_frontier(self) -> None:
        observed = state([])
        replanned = state([])
        replanned["navigation"]["target"]["x"] = 12.0
        controller.EXPLORATION_STALL.update(target=None, count=0, last_position=None)
        tap = Mock()
        with (
            patch.object(controller, "latest_state", return_value=(observed, 100)),
            patch.object(controller, "find_game_window", return_value=123),
            patch.object(controller, "focus_game"),
            patch.object(controller, "wait_for_fresh_state", side_effect=[(observed, 101), (observed, 102)]),
            patch.object(controller, "wait_for_state_condition", return_value=(replanned, 103)) as verified,
            patch.object(controller, "tap", tap),
            patch.object(controller, "release_movement_keys"),
        ):
            for _ in range(2):
                controller.explore_leg(
                    Path("unused.log"), 0.0, 4.0, 4.0, 1.0,
                    frontier_x=0.0, frontier_z=12.0,
                )

        self.assertEqual(tap.call_args_list, [
            call([controller.VK["up"]], 1.0),
            call([controller.VK["up"]], 1.0),
            call([controller.VK["reject_frontier"]], 0.08),
        ])
        verified.assert_called_once()
        self.assertEqual(controller.EXPLORATION_STALL["count"], 0)

    def test_explore_uses_updated_leg_for_same_frontier(self) -> None:
        observed = state([])
        observed["navigation"]["leg"]["x"] = 4.0
        observed["navigation"]["leg"]["z"] = 0.0
        after = state([])
        after["player"]["position"]["x"] = 3.5
        tap = Mock()
        with (
            patch.object(controller, "latest_state", return_value=(observed, 100)),
            patch.object(controller, "find_game_window", return_value=123),
            patch.object(controller, "focus_game"),
            patch.object(controller, "wait_for_fresh_state", return_value=(after, 101)),
            patch.object(controller, "tap", tap),
            patch.object(controller, "release_movement_keys"),
        ):
            controller.explore_leg(Path("unused.log"), 0.0, 4.0, 4.0)

        tap.assert_called_once_with([controller.VK["right"]], 1.0)

    def test_approach_uses_longer_pulses_for_distant_targets(self) -> None:
        self.assertEqual(controller.approach_step_duration(8.0), 1.0)
        self.assertGreater(controller.approach_step_duration(4.0), 0.70)
        self.assertEqual(controller.approach_step_duration(1.1), 0.18)

    def test_torch_is_not_treated_as_a_combat_weapon(self) -> None:
        self.assertFalse(controller.is_weapon_item({"prefab": "torch", "weapon": True}))

    def test_flee_moves_directly_away_from_hostile(self) -> None:
        observed = state([hostile(30, 3.0)])
        safe = state([])
        tap = Mock()
        release = Mock()

        with (
            patch.object(controller, "latest_state", return_value=(observed, 100)),
            patch.object(controller, "find_game_window", return_value=123),
            patch.object(controller, "focus_game"),
            patch.object(
                controller,
                "wait_for_fresh_state",
                side_effect=[(safe, 101), (safe, 102)],
            ),
            patch.object(controller, "tap", tap),
            patch.object(controller, "release_movement_keys", release),
        ):
            controller.flee_from_hostile(Path("unused.log"), preferred_guid=30)

        tap.assert_called_once_with([controller.VK["left"]], 1.0)
        release.assert_called_once_with()

    def test_flee_repeats_until_chasing_hostile_is_clear(self) -> None:
        observed = state([hostile(30, 3.0)])
        chasing_1 = state([hostile(30, 5.0)])
        chasing_2 = state([hostile(30, 9.0)])
        safe = state([])
        tap = Mock()

        with (
            patch.object(controller, "latest_state", return_value=(observed, 100)),
            patch.object(controller, "find_game_window", return_value=123),
            patch.object(controller, "focus_game"),
            patch.object(
                controller,
                "wait_for_fresh_state",
                side_effect=[
                    (chasing_1, 101),
                    (chasing_2, 102),
                    (safe, 103),
                    (safe, 104),
                ],
            ),
            patch.object(controller, "tap", tap),
            patch.object(controller, "release_movement_keys"),
        ):
            controller.flee_from_hostile(Path("unused.log"), preferred_guid=30)

        self.assertEqual(tap.call_count, 3)
        tap.assert_has_calls(
            [call([controller.VK["left"]], 1.0)] * 3
        )

    def test_depleted_preferred_guid_falls_back_to_live_grass(self) -> None:
        observed = state([grass(10, 0.4, False), grass(20, 0.8, True)])

        selected = controller.find_collectible_target(
            observed,
            "grass",
            preferred_guid=10,
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected["guid"], 20)

    def test_inventory_gain_finishes_collect_while_plant_remains(self) -> None:
        before = state([grass(20, 0.8, True)], cutgrass=0)
        after = state([grass(20, 0.8, True)], cutgrass=1)
        tap = Mock()
        release = Mock()

        with (
            patch.object(controller, "latest_state", return_value=(before, 100)),
            patch.object(controller, "find_game_window", return_value=123),
            patch.object(controller, "focus_game"),
            patch.object(
                controller,
                "wait_for_fresh_state",
                side_effect=[(before, 101), (after, 102)],
            ),
            patch.object(controller, "tap", tap),
            patch.object(controller, "release_movement_keys", release),
        ):
            controller.collect(Path("unused.log"), "grass", preferred_guid=20)

        tap.assert_called_once_with([controller.VK["interact"]], 0.08)
        release.assert_called_once_with()


class ExistingSelfTests(unittest.TestCase):
    def test_controller_self_test(self) -> None:
        self.assertEqual(controller.run_self_test(), 0)

    def test_agent_candidate_self_test_equivalent(self) -> None:
        sample = state([grass(1, 3.0, True)])
        criteria, dispatch = jev_agent.build_candidates(sample)
        self.assertIn("collect_grass", criteria)
        self.assertEqual(dispatch["collect_grass"]["guid"], 1)
        self.assertEqual(jev_agent.default_execution_threshold("explore"), 0.15)
        self.assertEqual(jev_agent.default_execution_threshold("collect_grass"), 0.25)
        self.assertEqual(jev_agent.default_execution_threshold("craft_torch"), 0.45)
        self.assertEqual(jev_agent.default_execution_threshold("build_campfire"), 0.45)


class TelemetryTests(unittest.TestCase):
    def test_truncated_line_is_ignored_and_chunked_frame_waits_until_complete(self) -> None:
        original = {"schema": 6, "world": {"day": 1}}
        updated = {"schema": 7, "world": {"day": 2}, "blob": "x" * 7000}
        first_line = f"[JEV_DST_STATE]{json.dumps(original)}\n".encode("utf-8")
        truncated = ("[JEV_DST_STATE]" + json.dumps(updated)[:4070] + "\n").encode("utf-8")
        encoded = json.dumps(updated)
        parts = [encoded[index:index + 3000] for index in range(0, len(encoded), 3000)]
        chunk_lines = [
            f"[JEV_DST_CHUNK]test-1:{index}:{len(parts)}:{part}\t\n".encode("utf-8")
            for index, part in enumerate(parts, 1)
        ]

        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "client_log.txt"
            log_path.write_bytes(first_line + truncated + b"".join(chunk_lines[:-1]))
            state_before, position_before = telemetry.latest_state(log_path)
            self.assertEqual(state_before, original)
            self.assertEqual(position_before, len(first_line))

            with log_path.open("ab") as stream:
                stream.write(chunk_lines[-1])
            state_after, position_after = telemetry.latest_state(log_path)
            self.assertEqual(state_after, updated)
            self.assertEqual(position_after, log_path.stat().st_size)

    def test_incomplete_last_log_line_is_not_accepted(self) -> None:
        complete = b'[JEV_DST_STATE]{"schema":6}\n'
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "client_log.txt"
            log_path.write_bytes(complete + b'[JEV_DST_STATE]{"schema":7}')

            parsed, position = telemetry.latest_state(log_path)

            self.assertEqual(parsed["schema"], 6)
            self.assertEqual(position, len(complete))


if __name__ == "__main__":
    unittest.main()
