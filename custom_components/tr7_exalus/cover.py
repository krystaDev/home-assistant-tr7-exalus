"""Cover platform for TR7 Exalus integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TR7ExalusCoordinator
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the TR7 Exalus cover platform."""
    coordinator: TR7ExalusCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Wait for initial data and ensure device discovery is complete
    await coordinator.async_config_entry_first_refresh()

    # The coordinator should have devices after first refresh, but let's ensure it
    if not coordinator.devices:
        _LOGGER.warning("No devices found after first refresh. This might indicate a discovery issue.")

    # Create cover entities for all discovered devices
    entities = []
    for device_guid, device_data in coordinator.devices.items():
        entities.append(TR7ExalusCover(coordinator, device_guid, device_data))

    if entities:
        _LOGGER.info("Setting up %d TR7 cover entities", len(entities))
        async_add_entities(entities, True)
    else:
        _LOGGER.warning("No TR7 devices found to create entities - check device discovery")


class TR7ExalusCover(CoordinatorEntity, CoverEntity):
    """Representation of a TR7 Exalus cover."""

    def __init__(
        self,
        coordinator: TR7ExalusCoordinator,
        device_guid: str,
        device_data: dict[str, Any],
    ) -> None:
        """Initialize the cover."""
        super().__init__(coordinator)
        self._device_guid = device_guid
        self._attr_unique_id = device_guid
        self._attr_name = f"TR7 Blind {device_guid[-8:]}"  # Use last 8 chars of GUID
        self._attr_device_class = CoverDeviceClass.BLIND
        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._device_guid)},
            "name": self.name,
            "manufacturer": "TR7 Exalus",
            "model": "Smart Blind",
            "sw_version": "1.0",
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Check both authentication and device existence
        authenticated = self.coordinator.authenticated
        device_exists = self._device_guid in self.coordinator.devices
        is_available = authenticated and device_exists

        # Log detailed availability info for debugging
        if not is_available:
            device_short = self._device_guid[-8:] if self._device_guid else "unknown"
            total_devices = len(self.coordinator.devices)
            device_list = [guid[-8:] for guid in self.coordinator.devices.keys()]

            _LOGGER.warning(
                "Device %s unavailable: authenticated=%s, device_exists=%s, total_devices=%d, known_devices=%s",
                device_short,
                authenticated,
                device_exists,
                total_devices,
                device_list
            )
        else:
            # Log successful availability at debug level to avoid spam
            _LOGGER.debug("Device %s is available", self._device_guid[-8:])

        return is_available

    @property
    def current_cover_position(self) -> int | None:
        """Return current position of cover.

        None is unknown, 0 is closed, 100 is fully open.
        """
        device_data = self.coordinator.devices.get(self._device_guid)
        if device_data is None:
            return None

        # TR7 position seems to be 0=open, 100=closed based on the traffic
        # We need to invert this for Home Assistant (0=closed, 100=open)
        tr7_position = device_data.get("position", 0)
        return 100 - tr7_position

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed."""
        position = self.current_cover_position
        if position is None:
            return None
        return position == 0

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        # This would require tracking movement state from TR7
        # For now, we'll return False as we don't have this info
        return False

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        # This would require tracking movement state from TR7
        # For now, we'll return False as we don't have this info
        return False

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        _LOGGER.info("async_open_cover called for device %s", self._device_guid)
        try:
            _LOGGER.debug("Calling coordinator.open_cover for device %s", self._device_guid)
            await self.coordinator.open_cover(self._device_guid)
            _LOGGER.info("Successfully called open_cover for device %s", self._device_guid)
            # Force update after command
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to open cover %s: %s", self._device_guid, err)
            raise

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        _LOGGER.info("async_close_cover called for device %s", self._device_guid)
        try:
            _LOGGER.debug("Calling coordinator.close_cover for device %s", self._device_guid)
            await self.coordinator.close_cover(self._device_guid)
            _LOGGER.info("Successfully called close_cover for device %s", self._device_guid)
            # Force update after command
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to close cover %s: %s", self._device_guid, err)
            raise

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        _LOGGER.info("async_stop_cover called for device %s", self._device_guid)
        try:
            _LOGGER.debug("Calling coordinator.stop_cover for device %s", self._device_guid)
            await self.coordinator.stop_cover(self._device_guid)
            _LOGGER.info("Successfully called stop_cover for device %s", self._device_guid)
            # Force update after command
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to stop cover %s: %s", self._device_guid, err)
            raise

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        position = kwargs.get(ATTR_POSITION)
        _LOGGER.info("async_set_cover_position called for device %s with position %s", self._device_guid, position)

        if position is None:
            _LOGGER.warning("No position provided for device %s", self._device_guid)
            return

        try:
            # Pass position directly to coordinator - it will handle TR7 conversion
            _LOGGER.debug("Setting cover position to %s for device %s", position, self._device_guid)
            await self.coordinator.set_cover_position(self._device_guid, position)
            _LOGGER.info("Successfully called set_cover_position for device %s", self._device_guid)
            # Force update after command
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error(
                "Failed to set cover %s position to %s: %s",
                self._device_guid,
                position,
                err,
            )
            raise

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        device_data = self.coordinator.devices.get(self._device_guid, {})
        return {
            "device_guid": self._device_guid,
            "raw_position": device_data.get("raw_position"),
            "channel": device_data.get("channel"),
            "last_update": device_data.get("time"),
            "reliability": device_data.get("reliability"),
        }