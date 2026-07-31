"""Sensoren der ATI Straton Integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import StratonConfigEntry
from .coordinator import StratonCoordinator, StratonData, StratonMode
from .entity import StratonEntity
from .guardian import GuardianState
from .intensity import current_intensity


@dataclass(frozen=True, kw_only=True)
class StratonSensorDescription(SensorEntityDescription):
    """Sensorbeschreibung mit Wertermittlung."""

    value_fn: Callable[[StratonData], Any]
    available_fn: Callable[[StratonData], bool] = lambda _: True


DEVICE_SENSORS: tuple[StratonSensorDescription, ...] = (
    StratonSensorDescription(
        key="current_adc",
        translation_key="current_adc",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.current.get("adc"),
        available_fn=lambda data: data.has_adc and bool(data.current),
    ),
    StratonSensorDescription(
        key="current_load",
        translation_key="current_load",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: _load_percentage(data),
        available_fn=lambda data: data.has_adc and bool(data.current),
    ),
)


def _load_percentage(data: StratonData) -> float | None:
    adc = data.current.get("adc")
    maximum = data.current.get("max")
    if not isinstance(adc, (int, float)) or not isinstance(maximum, (int, float)):
        return None
    if maximum <= 0:
        return None
    return round(adc / maximum * 100, 1)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StratonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Legt Geräte-, Spot- und Kanalsensoren an."""
    coordinator = entry.runtime_data
    data = coordinator.data

    entities: list[SensorEntity] = [
        StratonDeviceSensor(coordinator, description)
        for description in DEVICE_SENSORS
        if description.available_fn(data)
    ]

    entities.extend(
        StratonSpotTemperatureSensor(coordinator, spot)
        for spot in data.spots
        if "_id" in spot
    )

    entities.append(StratonModeSensor(coordinator))
    entities.append(StratonGuardianStateSensor(coordinator))
    entities.append(StratonGuardianReductionSensor(coordinator))
    entities.append(StratonColorsSensor(coordinator))

    async_add_entities(entities)


class StratonModeSensor(StratonEntity, SensorEntity):
    """Betriebsmodus: wer bestimmt gerade den Tagesverlauf.

    ``normal`` — das Gerät fährt seinen Zeitplan unverändert.
    ``manual_intensity`` — die Intensität wurde über die Integration gesetzt.
    ``guard`` — der Temperaturwächter hält den Verlauf abgesenkt.
    """

    _attr_translation_key = "mode"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = [mode.value for mode in StratonMode]
    _attr_icon = "mdi:state-machine"

    def __init__(self, coordinator: StratonCoordinator) -> None:
        super().__init__(coordinator, "mode")

    @property
    def native_value(self) -> str:
        return self.coordinator.mode.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        coordinator = self.coordinator
        program = coordinator.active_program
        return {
            "guard_engaged": coordinator.guard_engaged,
            "guard_state": coordinator.guardian.state.value,
            "guard_reduction": coordinator.guardian.level,
            "active_program": program.label if program else None,
            "intensity": round(current_intensity(coordinator.data.timelines), 1)
            if coordinator.data.timelines
            else None,
        }


class StratonColorsSensor(StratonEntity, SensorEntity):
    """Farben des Geräts samt ihrer spektralen Zusammensetzung.

    Der Zustand ist die Anzahl der Farben; die Zusammensetzungen stehen in den
    Attributen und lassen sich über ``ati_straton.set_color`` ändern.
    """

    _attr_translation_key = "colors"
    _attr_icon = "mdi:palette"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: StratonCoordinator) -> None:
        super().__init__(coordinator, "colors")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.colors)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "colors": [
                {
                    "id": color.id,
                    "name": color.name,
                    "disabled": color.disabled,
                    "composition": color.composition,
                }
                for color in self.coordinator.colors
            ]
        }


class StratonDeviceSensor(StratonEntity, SensorEntity):
    """Sensor auf Geräteebene."""

    entity_description: StratonSensorDescription

    def __init__(
        self, coordinator: StratonCoordinator, description: StratonSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)


class StratonSpotTemperatureSensor(StratonEntity, SensorEntity):
    """Temperatur eines physischen LED-Moduls."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: StratonCoordinator, spot: dict[str, Any]) -> None:
        spot_id = spot["_id"]
        super().__init__(coordinator, f"spot_{spot_id}_temperature")
        self._external_id = f"{coordinator.data.device_id}:{spot_id}"
        self._attr_name = spot.get("name") or f"Spot {spot_id}"

    @property
    def native_value(self) -> float | None:
        reading = self.coordinator.data.readings.get(self._external_id)
        return reading.temperature if reading else None

    @property
    def available(self) -> bool:
        return super().available and self._external_id in self.coordinator.data.readings

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        reading = self.coordinator.data.readings.get(self._external_id)
        return {
            "external_id": self._external_id,
            "raw_temperature": reading.raw_temperature if reading else None,
        }


class StratonGuardianStateSensor(StratonEntity, SensorEntity):
    """Betriebszustand des Temperaturwächters."""

    _attr_translation_key = "guard_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = [state.value for state in GuardianState]
    _attr_icon = "mdi:shield-check"

    def __init__(self, coordinator: StratonCoordinator) -> None:
        super().__init__(coordinator, "guard_state")

    @property
    def native_value(self) -> str:
        return self.coordinator.guardian.state.value


class StratonGuardianReductionSensor(StratonEntity, SensorEntity):
    """Aktuell vom Wächter angewandte Absenkung."""

    _attr_translation_key = "guard_reduction"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:arrow-down-bold"

    def __init__(self, coordinator: StratonCoordinator) -> None:
        super().__init__(coordinator, "guard_reduction")

    @property
    def native_value(self) -> float:
        return self.coordinator.guardian.level
