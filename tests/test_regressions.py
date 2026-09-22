"""Offline regressions for action exposure, target selection and completion."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import controller
import jev_agent


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
        tap = Mock()
        release = Mock()

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

        tap.assert_called_once_with([controller.VK["left"]], 1.2)
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
            [call([controller.VK["left"]], 1.2)] * 3
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


if __name__ == "__main__":
    unittest.main()
