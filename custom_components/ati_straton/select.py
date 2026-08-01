"""Auswahl von Lichtprogramm und Gewöhnungsstufe."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import StratonConfigEntry
from .coordinator import StratonCoordinator
from .entity import StratonEntity
from .programs import ProgramError

DEFAULT_LEVEL_INDEX = 1
"""Ohne frühere Auswahl: „Lichtgewöhnt", die mittlere der drei Stufen."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StratonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    level = StratonAcclimatisationSelect(coordinator)
    async_add_entities([StratonProgramSelect(coordinator, level), level])


class StratonAcclimatisationSelect(StratonEntity, SelectEntity, RestoreEntity):
    """Gewöhnungsstufe — bestimmt die Intensität beim Laden eines Programms.

    Die Stufe wird **nicht** sofort auf das Gerät geschrieben. Sie wirkt beim
    nächsten Programmwechsel, genau wie in der Geräteoberfläche, wo sie Teil des
    Ladevorgangs ist.
    """

    _attr_translation_key = "acclimatisation"
    _attr_icon = "mdi:sprout"

    def __init__(self, coordinator: StratonCoordinator) -> None:
        super().__init__(coordinator, "acclimatisation")
        self._index = DEFAULT_LEVEL_INDEX

    @property
    def index(self) -> int:
        return self._index

    def _levels(self) -> tuple:
        program = self.coordinator.active_program or next(
            iter(self.coordinator.programs), None
        )
        return program.levels if program else ()

    @property
    def options(self) -> list[str]:
        return [level.title for level in self._levels()]

    @property
    def current_option(self) -> str | None:
        levels = self._levels()
        if not levels or self._index >= len(levels):
            return None
        return levels[self._index].title

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None or last.state in (None, STATE_UNKNOWN):
            return
        for index, level in enumerate(self._levels()):
            if level.title == last.state:
                self._index = index
                return

    async def async_select_option(self, option: str) -> None:
        for index, level in enumerate(self._levels()):
            if level.title == option:
                self._index = index
                self.async_write_ha_state()
                return
        raise HomeAssistantError(f"Unbekannte Gewöhnungsstufe: {option}")

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        levels = self._levels()
        if not levels or self._index >= len(levels):
            return {}
        level = levels[self._index]
        return {
            "description": level.description,
            "preferred_intensity": level.preferred,
            "levels": [
                {
                    "title": entry.title,
                    "description": entry.description,
                    "preferred": entry.preferred,
                }
                for entry in levels
            ],
        }


class StratonProgramSelect(StratonEntity, SelectEntity):
    """Lichtprogramm — das Laden überschreibt den Tagesverlauf."""

    _attr_translation_key = "program"
    _attr_icon = "mdi:playlist-star"

    def __init__(
        self,
        coordinator: StratonCoordinator,
        level_select: StratonAcclimatisationSelect,
    ) -> None:
        super().__init__(coordinator, "program")
        self._level_select = level_select

    @property
    def options(self) -> list[str]:
        return [program.label for program in self.coordinator.programs]

    @property
    def current_option(self) -> str | None:
        program = self.coordinator.active_program
        return program.label if program else None

    @property
    def available(self) -> bool:
        # Während der Wächter regelt, gehört der Tagesverlauf ihm.
        return super().available and not self.coordinator.guard_engaged

    async def async_select_option(self, option: str) -> None:
        try:
            await self.coordinator.async_load_program(option, self._level_select.index)
        except ProgramError as err:
            raise HomeAssistantError(str(err)) from err

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Auswahlliste, verwendete Farben und der Tagesverlauf.

        ``colors_in_use`` beantwortet, welche Spektren das gewählte Programm
        tatsächlich fährt — meist nur zwei bis drei der zehn verfügbaren.
        ``schedule`` zeigt, wann welche davon greift.
        """
        return {
            "programs": [
                {
                    "id": program.id,
                    "title": program.title,
                    "group": program.group,
                    "description": program.description,
                    "custom": program.is_custom,
                }
                for program in self.coordinator.programs
            ],
            "colors_in_use": [
                {
                    "id": color.id,
                    "name": color.name,
                    "composition": color.composition,
                }
                for color in self.coordinator.schedule_colors
            ],
            "schedule": self.coordinator.schedule,
        }
