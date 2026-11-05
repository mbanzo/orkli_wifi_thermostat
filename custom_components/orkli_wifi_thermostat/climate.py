"""Interfaces with the Example api sensors."""

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import APIConnectionError, Device
from .const import DOMAIN
from .coordinator import OrkliCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Climates."""

    coordinator: OrkliCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ].coordinator

    climate_devices = [
        ExampleClimate(coordinator, device)
        for device in coordinator.data.devices
    ]

    async_add_entities(climate_devices)


class ExampleClimate(CoordinatorEntity, ClimateEntity, RestoreEntity):
    """Implementation of a climate entity."""

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return the list of supported features."""
        return (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )

    def __init__(self, coordinator: OrkliCoordinator, device: Device) -> None:
        """Initialise climate entity."""
        super().__init__(coordinator)
        self.device = device
        self.device_id = device.device_id

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update entity with latest data from coordinator."""
        self.device = self.coordinator.get_device_by_id(self.device_id)
        self.async_write_ha_state()

    @property
    def hvac_modes(self) -> list"""Offer zone power control in the device's currently cached mode."""
        mode = self._device_hvac_mode
        return [HVACMode.OFF, mode] if mode is not None else [HVACMode.OFF]

    @property
    def _device_hvac_mode(self) -> HVACMode | None:
        """Map the existing mode interpretation without selecting a new mode."""
        return {0: HVACMode.HEAT, 1: HVACMode.COOL}.get(self.device.mode)

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current operation mode."""
        if not self.device.on:
            return HVACMode.OFF
        return self._device_hvac_mode

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            name=f"Thermostat {self.device.name}: {self.device.device_id}",
            manufacturer="Orkli",
            model="TermoLite",
            sw_version="1.0",
            identifiers={
                (
                    DOMAIN,
                    f"{self.coordinator.data.controller_name}-{self.device.device_id}",
                )
            },
        )

    @property
    def name(self) -> str:
        """Return the entity name."""
        return self.device.name

    @property
    def native_current_temperature(self) -> float | None:
        """Return the current temperature."""
        return (
            (162.0 - float(self.device.current_temperature)) / 2.0
            if self.device.current_temperature is not None
            else None
        )

    @property
    def native_target_temperature(self) -> float | None:
        """Return the target temperature."""
        return (
            float(self.device.target_temperature) / 2.0
            if self.device.target_temperature is not None
            else None
        )

    @property
    def current_humidity(self) -> float | None:
        """Return current humidity."""
        return (
            int(float(self.device.current_humidity) / 2.55)
            if self.device.current_humidity is not None
            else None
        )

    @property
    def native_temperature_unit(self) -> str:
        """Return temperature unit."""
        return UnitOfTemperature.CELSIUS

    @property
    def native_min_temp(self) -> float:
        """Return minimum temperature."""
        return 15.0

    @property
    def native_max_temp(self) -> float:
        """Return maximum temperature."""
        return 35.0

    @property
    def precision(self) -> float:
        """Return temperature precision."""
        return 0.5

    @property
    def target_temperature_step(self) -> float:
        """Return supported target temperature step."""
        return 0.5

    async def async_added_to_hass(self):
        """Run when entity is added to Home Assistant."""
        await super().async_added_to_hass()

        previous_state = await self.async_get_last_state()

        if previous_state is not None:

            restored_current_temp = previous_state.attributes.get(
                "native_current_temperature",
                previous_state.attributes.get("current_temperature"),
            )

            if restored_current_temp is not None:
                self.device.current_temperature = (
                    162 - (restored_current_temp * 2)
                )

            restored_target_temp = previous_state.attributes.get(
                "temperature",
                previous_state.attributes.get(
                    "native_target_temperature",
                    previous_state.attributes.get("target_temperature"),
                ),
            )

            if restored_target_temp is not None:
                self.device.target_temperature = (
                    restored_target_temp * 2
                )

            restored_humidity = previous_state.attributes.get(
                "current_humidity"
            )

            if restored_humidity is not None:
                self.device.current_humidity = (
                    restored_humidity * 2.55
                )

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        await self.coordinator.async_send_temp_command(
            self.device,
            kwargs.get("temperature"),
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Enable or disable the zone without changing mode."""
        if hvac_mode not in self.hvac_modes:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_hvac_mode",
                translation_placeholders={
                    "name": self.name,
                    "mode": str(hvac_mode),
                },
            )

        await self._async_set_zone_power(hvac_mode != HVACMode.OFF)

    async def async_turn_on(self) -> None:
        """Turn the zone on."""
        await self._async_set_zone_power(True)

    async def async_turn_off(self) -> None:
        """Turn the zone off."""
        await self._async_set_zone_power(False)

    async def _async_set_zone_power(self, on: bool) -> None:
        """Send power command and wait for telemetry."""
        try:
            sent = await self.coordinator.async_send_toggle_command(
                self.device,
                on,
            )
        except (APIConnectionError, OSError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="zone_power_failed",
                translation_placeholders={"name": self.name},
            ) from err

        if not sent:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="zone_power_failed",
                translation_placeholders={"name": self.name},
            )

    @property
    def unique_id(self) -> str:
        """Return unique id."""
        return f"{DOMAIN}-{self.device.device_unique_id}"

    @property
    def extra_state_attributes(self):
        """Return extra attributes."""
        return {
            "extra_info": "Extra Info",
        }
