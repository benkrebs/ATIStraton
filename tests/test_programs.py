"""Tests für Lichtprogramme, Gewöhnungsstufen und Farbzusammensetzungen.

Die Testdaten sind frei erfunden — siehe ``fixtures/README.md``. Geprüft wird
die Struktur der Nutzlast, nicht ein bestimmter Inhalt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.ati_straton.programs import (
    DEFAULT_END,
    DEFAULT_START,
    ProgramError,
    build_load_payload,
    colors_in_schedule,
    derive_timerange,
    parse_colors,
    parse_programs,
    schedule_overview,
    with_updated_color,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Auszug der Geräteübersetzung lang-de_DE.json.
TRANSLATIONS = {
    "PRESETTING_GROUP_1": "Allgemeine Einstellungen",
    "PRESETTING_GROUP_2": "Bewährte Einstellungen der Community",
    "PRESETTING_GROUP_CUSTOM": "Eigene Voreinstellungen",
    "PRESETTING_TITLE_1": "Erstes Testprogramm",
    "PRESETTING_TITLE_5": "Fünftes Testprogramm",
    "PRESETTING_INTENSITY_1": "Eingewöhnungsphase",
    "PRESETTING_INTENSITY_2": "Lichtgewöhnt",
    "PRESETTING_INTENSITY_3": "Starklichtgewöhnt",
    "PRESETTING_INTENSITY_DESC_1": "für lichtempfindliche Korallen",
    "COLORNAME_V": "V",
}


@pytest.fixture(name="presettings")
def presettings_fixture() -> list[dict]:
    return json.loads((FIXTURES / "presettings.json").read_text())


@pytest.fixture(name="timelines")
def timelines_fixture() -> list[dict]:
    return json.loads((FIXTURES / "timelines.json").read_text())


@pytest.fixture(name="colors")
def colors_fixture() -> list[dict]:
    return json.loads((FIXTURES / "colors.json").read_text())


class TestPrograms:
    def test_all_programs_are_parsed(self, presettings: list[dict]) -> None:
        programs = parse_programs(presettings, TRANSLATIONS)
        assert len(programs) == 7

    def test_titles_are_translated(self, presettings: list[dict]) -> None:
        titles = {p.title for p in parse_programs(presettings, TRANSLATIONS)}
        assert "Erstes Testprogramm" in titles
        assert "Fünftes Testprogramm" in titles

    def test_custom_program_gets_the_custom_group(
        self, presettings: list[dict]
    ) -> None:
        custom = next(
            p for p in parse_programs(presettings, TRANSLATIONS) if p.is_custom
        )
        assert custom.title == "Eigenes Testprogramm"
        assert custom.group == "Eigene Voreinstellungen"

    def test_every_program_has_three_levels(self, presettings: list[dict]) -> None:
        for program in parse_programs(presettings, TRANSLATIONS):
            assert len(program.levels) == 3

    def test_levels_carry_the_device_wording(self, presettings: list[dict]) -> None:
        program = next(
            p for p in parse_programs(presettings, TRANSLATIONS) if p.id == 3
        )
        assert [level.title for level in program.levels] == [
            "Eingewöhnungsphase",
            "Lichtgewöhnt",
            "Starklichtgewöhnt",
        ]
        assert [level.preferred for level in program.levels] == [30, 60, 90]

    def test_labels_are_unique(self, presettings: list[dict]) -> None:
        labels = [p.label for p in parse_programs(presettings, TRANSLATIONS)]
        assert len(labels) == len(set(labels))

    def test_untranslated_keys_survive(self, presettings: list[dict]) -> None:
        programs = parse_programs(presettings, None)
        assert any(p.title.startswith("PRESETTING_TITLE") for p in programs)


class TestLoadPayload:
    """Vergleich mit dem echten POST der Originaloberfläche."""

    def test_payload_matches_the_captured_request(
        self, presettings: list[dict]
    ) -> None:
        program = next(
            p for p in parse_programs(presettings, TRANSLATIONS) if p.id == 3
        )
        payload = build_load_payload(program, level_index=1, group_ids=[1])

        assert payload["_id"] == 3
        assert payload["intensity"] == 60
        assert payload["groups"] == [1]
        assert payload["start"] == payload["timerange"]["start"]
        assert payload["end"] == payload["timerange"]["end"]

    def test_exactly_one_level_is_highlighted(self, presettings: list[dict]) -> None:
        program = next(p for p in parse_programs(presettings) if p.id == 3)
        for index in range(3):
            payload = build_load_payload(program, index, [1])
            flags = [e["highlighted"] for e in payload["intensities"]]
            assert flags.count(True) == 1
            assert flags[index] is True

    def test_intensity_follows_the_selected_level(
        self, presettings: list[dict]
    ) -> None:
        program = next(p for p in parse_programs(presettings) if p.id == 3)
        assert build_load_payload(program, 0, [1])["intensity"] == 30
        assert build_load_payload(program, 2, [1])["intensity"] == 90

    def test_explicit_intensity_wins(self, presettings: list[dict]) -> None:
        program = next(p for p in parse_programs(presettings) if p.id == 3)
        assert build_load_payload(program, 0, [1], intensity=45)["intensity"] == 45

    def test_source_program_is_not_mutated(self, presettings: list[dict]) -> None:
        program = next(p for p in parse_programs(presettings) if p.id == 3)
        build_load_payload(program, 2, [1])
        assert all(not e.get("highlighted") for e in program.raw.get("intensities", []))

    @pytest.mark.parametrize("bad", [-1, 3, 99])
    def test_invalid_level_is_rejected(self, presettings: list[dict], bad: int) -> None:
        program = next(p for p in parse_programs(presettings) if p.id == 3)
        with pytest.raises(ProgramError, match="Gewöhnungsstufe"):
            build_load_payload(program, bad, [1])

    def test_missing_group_is_rejected(self, presettings: list[dict]) -> None:
        program = next(p for p in parse_programs(presettings) if p.id == 3)
        with pytest.raises(ProgramError, match="Timeline"):
            build_load_payload(program, 0, [])


class TestTimerange:
    """Der Zeitbereich gehört nicht fest zum Programm — siehe derive_timerange."""

    def test_custom_program_without_timerange_still_works(
        self, presettings: list[dict]
    ) -> None:
        """Eigene Presettings bringen keinen timerange mit."""
        custom = next(p for p in parse_programs(presettings) if p.is_custom)
        assert "timerange" not in custom.raw
        payload = build_load_payload(custom, 1, [1])
        assert payload["start"] == DEFAULT_START
        assert payload["end"] == DEFAULT_END
        assert payload["timerange"] == {"start": DEFAULT_START, "end": DEFAULT_END}

    def test_program_timerange_is_used_when_present(
        self, presettings: list[dict]
    ) -> None:
        program = next(p for p in parse_programs(presettings) if p.id == 3)
        assert derive_timerange(program) == (32400, 79200)

    def test_existing_schedule_wins(
        self, presettings: list[dict], timelines: list[dict]
    ) -> None:
        """Das gewohnte Lichtfenster hat Vorrang vor dem Programmvorschlag."""
        program = next(p for p in parse_programs(presettings) if p.id == 3)
        start, end = derive_timerange(program, timelines)
        assert (start, end) == (36000, 79200)

    def test_dark_schedule_falls_back_to_the_program(
        self, presettings: list[dict]
    ) -> None:
        program = next(p for p in parse_programs(presettings) if p.id == 3)
        dark = [{"nodes": [{"time": 0, "value": 0}, {"time": 86400, "value": 0}]}]
        assert derive_timerange(program, dark) == (32400, 79200)

    def test_explicit_timerange_wins(self, presettings: list[dict]) -> None:
        program = next(p for p in parse_programs(presettings) if p.id == 3)
        payload = build_load_payload(program, 0, [1], timerange=(30000, 70000))
        assert (payload["start"], payload["end"]) == (30000, 70000)

    def test_inverted_timerange_is_rejected(self, presettings: list[dict]) -> None:
        program = next(p for p in parse_programs(presettings) if p.id == 3)
        with pytest.raises(ProgramError, match="Zeitbereich"):
            build_load_payload(program, 0, [1], timerange=(70000, 30000))


class TestColors:
    def test_all_colors_are_parsed(self, colors: list[dict]) -> None:
        assert len(parse_colors(colors, TRANSLATIONS)) == 10

    def test_composition_is_readable(self, colors: list[dict]) -> None:
        natural = next(c for c in parse_colors(colors) if c.name == "Testfarbe 0")
        assert natural.composition == {
            "V": 120,
            "RB": 60,
            "B": 110,
            "LC": 250,
            "W": 250,
            "R": 30,
        }

    def test_update_changes_only_the_target(self, colors: list[dict]) -> None:
        updated = with_updated_color(colors, 5, {"W": 200})
        changed = next(c for c in parse_colors(updated) if c.id == 5)
        assert changed.composition["W"] == 200
        untouched = [c.composition for c in parse_colors(updated) if c.id != 5]
        assert untouched == [c.composition for c in parse_colors(colors) if c.id != 5]

    def test_input_is_not_mutated(self, colors: list[dict]) -> None:
        before = parse_colors(colors)[0].composition
        with_updated_color(colors, 5, {"W": 1})
        assert parse_colors(colors)[0].composition == before

    def test_multiple_channels_at_once(self, colors: list[dict]) -> None:
        updated = with_updated_color(colors, 5, {"W": 10, "B": 20, "R": 30})
        composition = next(c for c in parse_colors(updated) if c.id == 5).composition
        assert (composition["W"], composition["B"], composition["R"]) == (10, 20, 30)

    @pytest.mark.parametrize("bad", [-1, 256, 1000])
    def test_out_of_range_is_rejected(self, colors: list[dict], bad: int) -> None:
        with pytest.raises(ProgramError, match="außerhalb"):
            with_updated_color(colors, 5, {"W": bad})

    @pytest.mark.parametrize("bad", [True, 1.5, "100", None])
    def test_non_int_is_rejected(self, colors: list[dict], bad: object) -> None:
        with pytest.raises(ProgramError, match="ganzzahlig"):
            with_updated_color(colors, 5, {"W": bad})

    def test_unknown_channel_is_rejected(self, colors: list[dict]) -> None:
        with pytest.raises(ProgramError, match="Unbekannte Kanäle"):
            with_updated_color(colors, 5, {"XYZ": 100})

    def test_unknown_color_is_rejected(self, colors: list[dict]) -> None:
        with pytest.raises(ProgramError, match="nicht gefunden"):
            with_updated_color(colors, 9999, {"W": 100})

    def test_full_range_is_allowed(self, colors: list[dict]) -> None:
        for value in (0, 255):
            updated = with_updated_color(colors, 5, {"W": value})
            assert (
                next(c for c in parse_colors(updated) if c.id == 5).composition["W"]
                == value
            )


class TestScheduleColors:
    """Welche Farben verwendet der aktuelle Tagesverlauf?"""

    def test_only_colors_actually_used_are_returned(
        self, timelines: list[dict], colors: list[dict]
    ) -> None:
        used = colors_in_schedule(timelines)
        assert 0 < len(used) < len(colors), "Ein Programm nutzt nur einen Teil"

    def test_names_match_the_device(self, timelines: list[dict]) -> None:
        assert {c.name for c in colors_in_schedule(timelines)} == {
            "Testfarbe 0",
            "Testfarbe 1",
            "Testfarbe 2",
        }

    def test_compositions_are_included(self, timelines: list[dict]) -> None:
        for color in colors_in_schedule(timelines):
            assert color.composition
            assert all(0 <= v <= 255 for v in color.composition.values())

    def test_each_color_appears_once(self, timelines: list[dict]) -> None:
        ids = [c.id for c in colors_in_schedule(timelines)]
        assert len(ids) == len(set(ids))

    def test_empty_schedule_yields_nothing(self) -> None:
        assert colors_in_schedule([]) == []
        assert colors_in_schedule([{"nodes": []}]) == []

    def test_nodes_without_colour_are_skipped(self) -> None:
        assert colors_in_schedule([{"nodes": [{"time": 0, "value": 0}]}]) == []


class TestScheduleOverview:
    def test_one_entry_per_node(self, timelines: list[dict]) -> None:
        overview = schedule_overview(timelines)
        assert len(overview) == len(timelines[0]["nodes"])

    def test_entries_are_sorted_by_time(self, timelines: list[dict]) -> None:
        seconds = [entry["seconds"] for entry in schedule_overview(timelines)]
        assert seconds == sorted(seconds)

    def test_times_are_formatted_readably(self, timelines: list[dict]) -> None:
        overview = schedule_overview(timelines)
        assert overview[0]["time"] == "00:00"
        assert any(entry["time"] == "09:00" for entry in overview)

    def test_intensity_and_colour_are_carried_over(self, timelines: list[dict]) -> None:
        peak = max(schedule_overview(timelines), key=lambda e: e["intensity"])
        assert peak["intensity"] == 50.0
        assert peak["color"] == "Testfarbe 2"

    def test_malformed_nodes_are_skipped(self) -> None:
        assert schedule_overview([{"nodes": [{"value": 1}]}]) == []
