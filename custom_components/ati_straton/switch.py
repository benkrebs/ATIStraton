"""Schalter der ATI Straton Integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import StratonConfigEntry
from .coordinator import StratonCoordinator
from .entity import StratonEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StratonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([StratonGuardianSwitch(entry.runtime_data)])


class StratonGuardianSwitch(StratonEntity, SwitchEntity, RestoreEntity):
    """Aktiviert den Temperaturwächter."""

    _attr_translation_key = "temperature_guard"
    _attr_icon = "mdi:thermometer-alert"

    def __init__(self, coordinator: StratonCoordinator) -> None:
        super().__init__(coordinator, "temperature_guard")

    async def async_added_to_hass(self) -> None:
        """Stellt den Zustand nach einem Neustart wieder her.

        Der Wächter startet bewusst **deaktiviert**, wenn kein früherer Zustand
        vorliegt — eine Regelung soll nur laufen, wenn sie bewusst eingeschaltet
        wurde.
        """
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self.coordinator.update_guardian_config(
                enabled=last_state.state == STATE_ON
            )

    @property
    def is_on(self) -> bool:
        return self.coordinator.guardian.config.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.update_guardian_config(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.update_guardian_config(enabled=False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        guardian = self.coordinator.guardian
        return {
            "state": guardian.state,
            "reduction_percent": guardian.level,
            "config_valid": guardian.config.valid,
            "max_temperature_now": self.coordinator.data.max_temperature,
            "device_max_temperature": self.coordinator.data.device_max_temperature,
        }
