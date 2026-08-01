"""Knöpfe des Farb-Editors."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import StratonConfigEntry
from .coordinator import StratonCoordinator
from .entity import StratonEntity
from .programs import ProgramError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StratonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [StratonSaveColorButton(coordinator), StratonDiscardColorButton(coordinator)]
    )


class StratonSaveColorButton(StratonEntity, ButtonEntity):
    """Überträgt den bearbeiteten Farbstand zum Gerät.

    Die Kanalregler schreiben bewusst nur in einen Puffer. Erst dieser Knopf
    löst den einen Schreibvorgang aus — sonst erzeugte jede Reglerbewegung einen
    Flash-Zugriff auf das Gerät.
    """

    _attr_translation_key = "save_color"
    _attr_icon = "mdi:content-save"

    def __init__(self, coordinator: StratonCoordinator) -> None:
        super().__init__(coordinator, "save_color")

    @property
    def available(self) -> bool:
        return (
            super().available
            and not self.coordinator.guard_engaged
            and self.coordinator.color_buffer_dirty
        )

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_save_color_buffer()
        except ProgramError as err:
            raise HomeAssistantError(str(err)) from err


class StratonDiscardColorButton(StratonEntity, ButtonEntity):
    """Verwirft den Bearbeitungsstand und lädt die Gerätewerte zurück."""

    _attr_translation_key = "discard_color"
    _attr_icon = "mdi:undo"

    def __init__(self, coordinator: StratonCoordinator) -> None:
        super().__init__(coordinator, "discard_color")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.color_buffer_dirty

    async def async_press(self) -> None:
        self.coordinator.discard_color_buffer()
