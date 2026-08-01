"""Einstellbare Schwellwerte des Temperaturwächters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import StratonConfigEntry
from .coordinator import StratonCoordinator
from .entity import StratonEntity
from .guardian import (
    MAX_REDUCTION_STEP,
    MAX_TEMP_SETTING,
    MIN_REDUCTION_STEP,
    MIN_TEMP_SETTING,
    GuardianConfig,
)
from .http import channel_picture_url
from .intensity import (
    MAX_INTENSITY,
    MIN_INTENSITY,
    IntensityError,
    current_intensity,
    max_value_org,
)
from .programs import MAX_CHANNEL_VALUE, MIN_CHANNEL_VALUE
from .spectrum import channel_hex, channel_name


@dataclass(frozen=True, kw_only=True)
class StratonNumberDescription(NumberEntityDescription):
    """Beschreibung eines Wächter-Parameters."""

    field: str
    value_fn: Callable[[GuardianConfig], float]


GUARDIAN_NUMBERS: tuple[StratonNumberDescription, ...] = (
    StratonNumberDescription(
        key="guard_max_temp",
        translation_key="guard_max_temp",
        field="max_temp",
        value_fn=lambda config: config.max_temp,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=MIN_TEMP_SETTING,
        native_max_value=MAX_TEMP_SETTING,
        native_step=0.5,
        mode=NumberMode.SLIDER,
        icon="mdi:thermometer-chevron-up",
    ),
    StratonNumberDescription(
        key="guard_low_temp",
        translation_key="guard_low_temp",
        field="low_temp",
        value_fn=lambda config: config.low_temp,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=MIN_TEMP_SETTING,
        native_max_value=MAX_TEMP_SETTING,
        native_step=0.5,
        mode=NumberMode.SLIDER,
        icon="mdi:thermometer-chevron-down",
    ),
    StratonNumberDescription(
        key="guard_reduction_step",
        translation_key="guard_reduction_step",
        field="reduction_step",
        value_fn=lambda config: config.reduction_step,
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=MIN_REDUCTION_STEP,
        native_max_value=MAX_REDUCTION_STEP,
        native_step=1.0,
        mode=NumberMode.SLIDER,
        icon="mdi:arrow-down-bold",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StratonConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = [StratonIntensityNumber(coordinator)]
    entities.extend(
        StratonGuardianNumber(coordinator, description)
        for description in GUARDIAN_NUMBERS
    )
    # Ein Regler je Kanal, den das Gerät tatsächlich besitzt.
    entities.extend(
        StratonColorChannelNumber(coordinator, channel, hass.config.language)
        for channel in sorted(
            {
                value.get("name")
                for color in coordinator.data.colors
                for value in color.get("values") or ()
                if isinstance(value.get("name"), str)
            }
        )
    )
    async_add_entities(entities)


class StratonColorChannelNumber(StratonEntity, NumberEntity):
    """Ein Kanal der gerade zur Bearbeitung gewählten Farbe.

    Schreibt **nicht** zum Gerät, sondern in einen Puffer. Erst der
    Speichern-Knopf überträgt. Ohne diese Trennung erzeugte jede
    Reglerbewegung einen Flash-Schreibvorgang auf der Leuchte.
    """

    _attr_native_min_value = float(MIN_CHANNEL_VALUE)
    _attr_native_max_value = float(MAX_CHANNEL_VALUE)
    _attr_native_step = 1.0
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: StratonCoordinator, channel: str, language: str
    ) -> None:
        super().__init__(coordinator, f"color_channel_{channel.lower()}")
        self._channel = channel
        self._attr_name = channel_name(channel, language)
        # Gefärbter Punkt statt des einfarbigen Icons.
        self._attr_entity_picture = channel_picture_url(channel)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.color_buffer.get(self._channel)

    @property
    def available(self) -> bool:
        return (
            super().available
            and not self.coordinator.guard_engaged
            and self._channel in self.coordinator.color_buffer
        )

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_buffered_channel(self._channel, int(value))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        color = self.coordinator.edited_color
        return {
            "channel": self._channel,
            "hex": channel_hex(self._channel),
            "color": color.name if color else None,
            "on_device": color.composition.get(self._channel) if color else None,
            "unsaved_changes": self.coordinator.color_buffer_dirty,
        }


class StratonIntensityNumber(StratonEntity, NumberEntity):
    """Globale Intensität des Tagesverlaufs.

    Entspricht dem Intensitätsregler der Geräteoberfläche: Alle Kurvenknoten
    werden relativ zu ihrem unveränderlichen ``valueOrg`` skaliert und mit
    ``PUT /api/data`` geschrieben. Die Wirkung tritt am Gerät innerhalb weniger
    Sekunden ein.
    """

    _attr_translation_key = "intensity"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = MIN_INTENSITY
    _attr_native_step = 1.0
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:brightness-6"

    def __init__(self, coordinator: StratonCoordinator) -> None:
        super().__init__(coordinator, "intensity")

    @property
    def native_max_value(self) -> float:
        """Obergrenze aus den Optionen, nie über der Skala des Geräts."""
        return min(self.coordinator.max_intensity, MAX_INTENSITY)

    @property
    def native_value(self) -> float | None:
        timelines = self.coordinator.data.timelines
        return round(current_intensity(timelines), 1) if timelines else None

    @property
    def available(self) -> bool:
        # Während der Wächter regelt, gehört die Kurve ihm.
        return super().available and not self.coordinator.guard_engaged

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.async_set_intensity(value)
        except IntensityError as err:
            raise HomeAssistantError(str(err)) from err

    @property
    def extra_state_attributes(self) -> dict[str, float | None]:
        timelines = self.coordinator.data.timelines
        return {
            "reference": max_value_org(timelines) if timelines else None,
            "guard_engaged": self.coordinator.guard_engaged,
        }


class StratonGuardianNumber(StratonEntity, RestoreNumber):
    """Ein Parameter des Temperaturwächters."""

    entity_description: StratonNumberDescription
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: StratonCoordinator, description: StratonNumberDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        """Stellt den Schwellwert nach einem Neustart wieder her."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.coordinator.guardian.update_config(
                **{self.entity_description.field: float(last.native_value)}
            )

    @property
    def native_value(self) -> float:
        return self.entity_description.value_fn(self.coordinator.guardian.config)

    async def async_set_native_value(self, value: float) -> None:
        """Übernimmt den Wert und hält die Hysterese-Invariante ein.

        ``low_temp`` muss unterhalb von ``max_temp`` bleiben; die jeweils andere
        Schwelle wird bei Bedarf mitgeführt, statt eine ungültige Konfiguration
        entstehen zu lassen, die den Wächter stilllegen würde.
        """
        config = self.coordinator.guardian.config
        field = self.entity_description.field
        changes: dict[str, float] = {field: value}

        if field == "max_temp" and value - config.low_temp < 1.0:
            changes["low_temp"] = max(MIN_TEMP_SETTING, value - 1.0)
        elif field == "low_temp" and config.max_temp - value < 1.0:
            changes["max_temp"] = min(MAX_TEMP_SETTING, value + 1.0)

        self.coordinator.update_guardian_config(**changes)
