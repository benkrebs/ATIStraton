"""Schalter der ATI Straton Integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import StratonConfigEntry
from .coordinator import StratonCoordinator
from .entity import StratonEntity
from .intensity import IntensityError, current_intensity

ATTR_INTENSITY_BEFORE_OFF = "intensity_before_off"
"""Attributname, unter dem die gemerkte Intensität einen Neustart übersteht."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StratonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [StratonPowerSwitch(coordinator), StratonGuardianSwitch(coordinator)]
    )


class StratonPowerSwitch(StratonEntity, SwitchEntity, RestoreEntity):
    """Schaltet die Beleuchtung aus und wieder ein.

    Ausschalten setzt die Intensität auf 0 — der Tagesverlauf bleibt dabei
    vollständig erhalten und wird lediglich auf null skaliert. Einschalten
    stellt die Intensität wieder her, die vor dem Ausschalten galt.

    Das Gerät kennt keinen echten Netzschalter; alles läuft über dieselbe
    Intensitätsskalierung wie der Regler.
    """

    _attr_translation_key = "power"
    _attr_icon = "mdi:lightbulb"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, coordinator: StratonCoordinator) -> None:
        super().__init__(coordinator, "power")

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_on

    @property
    def available(self) -> bool:
        # Während der Wächter regelt, gehört der Tagesverlauf ihm.
        return super().available and not self.coordinator.guard_engaged

    async def async_added_to_hass(self) -> None:
        """Holt die gemerkte Intensität zurück, damit ein Neustart im
        ausgeschalteten Zustand das Einschalten nicht raten lässt."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            remembered = last.attributes.get(ATTR_INTENSITY_BEFORE_OFF)
            if isinstance(remembered, (int, float)):
                self.coordinator.restore_intensity_before_off(float(remembered))

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.async_turn_on()
        except IntensityError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.async_turn_off()
        except IntensityError as err:
            raise HomeAssistantError(str(err)) from err

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_INTENSITY_BEFORE_OFF: self.coordinator.intensity_before_off,
            "intensity": round(current_intensity(self.coordinator.data.timelines), 1)
            if self.coordinator.data.timelines
            else None,
        }


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
