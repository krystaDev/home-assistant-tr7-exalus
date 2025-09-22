"""The TR7 Exalus integration."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_EMAIL,
    CONF_HOST,
    CONF_PASSWORD,
    DATA_TYPE_BLIND_POSITION,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    DOMAIN,
    METHOD_GET,
    METHOD_LOGIN,
    METHOD_POST,
    PLATFORMS,
    RESOURCE_DEVICE_CONTROL,
    RESOURCE_DEVICE_POSITION,
    RESOURCE_DEVICE_STATES,
    RESOURCE_DEVICE_STATE_CHANGED,
    RESOURCE_DEVICE_STOP,
    RESOURCE_LOGIN,
    WS_API_PATH,
    AUTH_REFRESH_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.COVER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TR7 Exalus from a config entry."""

    host = entry.data[CONF_HOST]
    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]

    coordinator = TR7ExalusCoordinator(hass, host, email, password)

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await coordinator.close()
        raise

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.close()

    return unload_ok


class TR7ExalusCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the TR7 Exalus system."""

    def __init__(self, hass: HomeAssistant, host: str, email: str, password: str) -> None:
        """Initialize."""
        self.host = host
        self.email = email
        self.password = password
        self.websocket = None
        self.authenticated = False
        self.devices = {}
        self._listen_task = None
        self._auth_event = None
        self._empty_device_states = 0  # Count consecutive empty device lists
        self._last_auth_time: datetime | None = None
        self._last_activity: datetime | None = None
        self._command_lock = asyncio.Lock()
        self._last_command_time: datetime | None = None
        self._command_delay = 0.5  # 500ms base delay between commands
        self._timeout_count = 0

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),  # Health check every 30 seconds
        )

    def _is_websocket_connected(self) -> bool:
        """Check if WebSocket is connected."""
        try:
            return self.websocket is not None and not self.websocket.closed
        except AttributeError:
            # Handle different websocket library versions
            return self.websocket is not None

    async def _ensure_connected_and_authenticated(self) -> None:
        """Ensure the websocket is connected and authenticated before sending commands."""
        if not self._is_websocket_connected():
            _LOGGER.info("WebSocket not connected. Reconnecting before command...")
            await self._connect()
        # Proactively re-authenticate if the auth is stale (some TR7 hubs drop auth ~30min)
        if self.authenticated and self._last_auth_time:
            age = datetime.now() - self._last_auth_time
            if age > timedelta(minutes=AUTH_REFRESH_MINUTES):
                _LOGGER.info("Authentication is stale (age=%s). Re-authenticating before command...", age)
                self.authenticated = False
                self._last_auth_time = None
        if not self.authenticated:
            _LOGGER.info("Not authenticated. Authenticating before command...")
            await self._authenticate()

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via WebSocket."""
        ws_connected = self._is_websocket_connected()
        device_count = len(self.devices)

        _LOGGER.debug("Health check: websocket_connected=%s, authenticated=%s, devices_count=%s",
                     ws_connected, self.authenticated, device_count)

        if not ws_connected:
            _LOGGER.info("WebSocket not connected, attempting to reconnect...")
            await self._connect()

        # Proactively re-authenticate if auth is stale even if connection looks fine
        if self.authenticated and self._last_auth_time:
            age = datetime.now() - self._last_auth_time
            if age > timedelta(minutes=AUTH_REFRESH_MINUTES):
                _LOGGER.info("Authentication is stale (age=%s). Forcing re-authentication...", age)
                self.authenticated = False
                self._last_auth_time = None

        if not self.authenticated:
            _LOGGER.info("Not authenticated, attempting to authenticate...")
            await self._authenticate()

        # Only send keepalive if there's been no activity for a long time (5 minutes)
        if self._last_activity:
            time_since_activity = datetime.now() - self._last_activity
            if time_since_activity > timedelta(minutes=5):
                try:
                    await self._send_message({
                        "TransactionId": str(uuid.uuid4()),
                        "Data": False,
                        "Resource": RESOURCE_DEVICE_STATES,
                        "Method": METHOD_GET
                    })
                    _LOGGER.debug("Sent passive keepalive after %s of inactivity", time_since_activity)
                except Exception as err:
                    _LOGGER.warning("Failed to send keepalive: %s", err)
                    # Connection might be broken, trigger reconnect on next update
                    self.authenticated = False
                    self._last_auth_time = None

        # Log device availability for debugging
        if device_count == 0:
            _LOGGER.warning("No devices available - covers will show as unavailable")
        else:
            _LOGGER.debug("TR7 coordinator healthy: %d devices available", device_count)

        return self.devices

    async def _connect(self) -> None:
        """Connect to WebSocket."""
        try:
            uri = f"ws://{self.host}:{DEFAULT_PORT}{WS_API_PATH}"
            _LOGGER.info("Connecting to TR7 Exalus at %s", uri)

            self.websocket = await asyncio.wait_for(
                websockets.connect(
                    uri,
                    ping_interval=30,
                    ping_timeout=10
                ),
                timeout=DEFAULT_TIMEOUT
            )

            # Reset authentication state
            self.authenticated = False
            self._auth_event = asyncio.Event()

            # Initialize activity tracking
            self._last_activity = datetime.now()

            # Reset rate limiting variables
            self._last_command_time = None
            self._command_delay = 0.5
            self._timeout_count = 0

            # Cancel any existing tasks
            if self._listen_task:
                self._listen_task.cancel()

            # Start listening for messages BEFORE authentication
            self._listen_task = asyncio.create_task(self._listen_for_messages())

            # Give the listener a moment to start
            await asyncio.sleep(0.5)

            _LOGGER.info("Connected to TR7 Exalus at %s", self.host)

        except (OSError, WebSocketException, asyncio.TimeoutError) as err:
            _LOGGER.error("Error connecting to TR7 Exalus: %s", err)
            raise ConfigEntryNotReady from err

    async def _authenticate(self) -> None:
        """Authenticate with the TR7 system."""
        try:
            transaction_id = str(uuid.uuid4())
            login_message = {
                "TransactionId": transaction_id,
                "Data": {
                    "Email": self.email,
                    "Password": self.password
                },
                "Resource": RESOURCE_LOGIN,
                "Method": METHOD_LOGIN
            }

            _LOGGER.info("Sending authentication request with transaction ID: %s", transaction_id)
            _LOGGER.debug("Login message: %s", {**login_message, "Data": {"Email": self.email, "Password": "***"}})

            await self._send_message(login_message)

            # Wait for authentication response with timeout
            try:
                await asyncio.wait_for(self._auth_event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                raise UpdateFailed("Authentication timeout - no response from TR7 system")

            if not self.authenticated:
                raise UpdateFailed("Authentication failed - invalid credentials or system error")

            _LOGGER.info("Successfully authenticated with TR7 Exalus")
            self._last_auth_time = datetime.now()

            # Perform initial device discovery after successful authentication
            await self._discover_devices()

        except Exception as err:
            _LOGGER.error("Authentication error: %s", err)
            raise UpdateFailed(f"Authentication failed: {err}") from err

    async def _discover_devices(self) -> None:
        """Discover devices after authentication."""
        try:
            transaction_id = str(uuid.uuid4())
            discovery_message = {
                "TransactionId": transaction_id,
                "Data": False,
                "Resource": RESOURCE_DEVICE_STATES,
                "Method": METHOD_GET
            }

            _LOGGER.info("Sending device discovery request with transaction ID: %s", transaction_id)
            await self._send_message(discovery_message)

            # Wait a moment for the response - devices will be populated in _handle_message
            await asyncio.sleep(2)

            _LOGGER.info("Device discovery completed. Found %d devices", len(self.devices))

            if not self.devices:
                _LOGGER.warning("No devices discovered. This may indicate an API issue or no configured devices.")

        except Exception as err:
            _LOGGER.error("Device discovery error: %s", err)
            # Don't raise - authentication was successful, just device discovery failed

    async def _send_message(self, message: dict) -> None:
        """Send a message via WebSocket with rate limiting."""
        if not self._is_websocket_connected():
            raise UpdateFailed("WebSocket not connected")

        # Use lock to ensure commands are sent sequentially
        async with self._command_lock:
            # Enforce minimum delay between commands
            if self._last_command_time:
                time_since_last = datetime.now() - self._last_command_time
                min_delay = timedelta(seconds=self._command_delay)
                if time_since_last < min_delay:
                    sleep_time = (min_delay - time_since_last).total_seconds()
                    _LOGGER.debug("Rate limiting: waiting %.2fs before next command", sleep_time)
                    await asyncio.sleep(sleep_time)

            try:
                message_str = json.dumps(message)
                _LOGGER.info("Sending WebSocket message: %s", message_str)
                await self.websocket.send(message_str)
                _LOGGER.debug("Message sent successfully")

                # Update timestamps
                self._last_command_time = datetime.now()
                self._last_activity = datetime.now()

                # Reset timeout count on successful send
                self._timeout_count = 0
                # Reset command delay to base value on success
                self._command_delay = 0.5

            except (ConnectionClosed, WebSocketException) as err:
                _LOGGER.error("Error sending message: %s", err)
                self.authenticated = False
                self._last_auth_time = None
                raise UpdateFailed(f"Failed to send message: {err}") from err

    async def _listen_for_messages(self) -> None:
        """Listen for incoming WebSocket messages."""
        try:
            async for message in self.websocket:
                await self._handle_message(message)
        except ConnectionClosed:
            _LOGGER.warning("WebSocket connection closed")
            self.authenticated = False
            self._last_auth_time = None
            # Speed up recovery by triggering a refresh
            try:
                await self.async_request_refresh()
            except Exception:
                pass
        except Exception as err:
            _LOGGER.error("Error in message listener: %s", err)
            self.authenticated = False
            self._last_auth_time = None
            try:
                await self.async_request_refresh()
            except Exception:
                pass

    async def _handle_message(self, message: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            _LOGGER.info("Received WebSocket message: %s", data)

            # Update activity timestamp
            self._last_activity = datetime.now()

            resource = data.get("Resource", "")
            status = data.get("Status")
            transaction_id = data.get("TransactionId")

            # Handle authentication response
            if resource == RESOURCE_LOGIN:
                _LOGGER.info("Received authentication response - Status: %s, TransactionId: %s", status, transaction_id)

                if status == 0:  # Success
                    user_data = data.get("Data", {})
                    _LOGGER.info("Authentication successful for user: %s %s (%s)",
                                user_data.get("Name"), user_data.get("Surname"), user_data.get("Email"))
                    self.authenticated = True
                    # Reset timeout count on successful authentication
                    self._timeout_count = 0
                else:
                    _LOGGER.error("Authentication failed with status: %s", status)
                    self.authenticated = False
                    self._last_auth_time = None

                # Signal authentication completion
                if self._auth_event:
                    self._auth_event.set()

            # Handle device states response (initial device discovery)
            elif resource == RESOURCE_DEVICE_STATES:
                _LOGGER.info("Received device states response - Status: %s, TransactionId: %s", status, transaction_id)
                await self._handle_device_states_response(data)

            # Handle device state changes
            elif resource == RESOURCE_DEVICE_STATE_CHANGED:
                _LOGGER.debug("Received device state change: %s", data)
                await self._handle_device_state_change(data)

            # Handle other messages - look for API responses
            else:
                if transaction_id:
                    _LOGGER.warning("🎯 API DISCOVERY RESPONSE: Resource=%s, Status=%s, TransactionId=%s, Data=%s",
                                  resource, status, transaction_id, data.get("Data"))

                    # Reset timeout count on successful device control commands
                    if resource in {RESOURCE_DEVICE_CONTROL, RESOURCE_DEVICE_POSITION, RESOURCE_DEVICE_STOP} and status == 0:
                        if self._timeout_count > 0:
                            _LOGGER.debug("Successful command response, resetting timeout count from %d", self._timeout_count)
                            self._timeout_count = 0
                            # Gradually reduce command delay on success
                            self._command_delay = max(self._command_delay * 0.9, 0.5)
                else:
                    _LOGGER.debug("Received other message type - Resource: %s, Status: %s", resource, status)

                # Handle non-zero status responses with rate limiting awareness
                try:
                    critical_resources = {
                        RESOURCE_DEVICE_CONTROL,
                        RESOURCE_DEVICE_POSITION,
                        RESOURCE_DEVICE_STOP,
                        RESOURCE_DEVICE_STATES,
                    }
                    if resource in critical_resources and status not in (None, 0):
                        if status == 10:  # DeviceResponseTimeout
                            self._timeout_count += 1
                            # Implement exponential backoff for timeouts
                            old_delay = self._command_delay
                            self._command_delay = min(self._command_delay * 1.5, 3.0)  # Cap at 3 seconds
                            _LOGGER.warning("Device timeout #%d (Status=%s) for resource %s. Increasing command delay from %.2fs to %.2fs",
                                          self._timeout_count, status, resource, old_delay, self._command_delay)

                            # Only force re-auth after many consecutive timeouts
                            if self._timeout_count >= 10:
                                _LOGGER.error("Too many consecutive timeouts (%d), forcing re-authentication", self._timeout_count)
                                self.authenticated = False
                                self._last_auth_time = None
                                self._timeout_count = 0
                                self._command_delay = 0.5  # Reset delay
                        else:
                            # Other non-zero statuses are more serious
                            _LOGGER.warning("Critical error (Status=%s) for resource %s. Response: %s", status, resource, data.get("Data"))
                            self.authenticated = False
                            self._last_auth_time = None
                except Exception:
                    pass

        except json.JSONDecodeError as err:
            _LOGGER.error("Failed to decode WebSocket message: %s - Raw message: %s", err, message)
        except Exception as err:
            _LOGGER.error("Error handling WebSocket message: %s - Data: %s", err, message)

    async def _handle_device_states_response(self, data: dict) -> None:
        """Handle device states response (initial device discovery)."""
        try:
            status = data.get("Status")
            device_list = data.get("Data", [])

            if status != 0:
                _LOGGER.warning("Device states request failed with status: %s; forcing re-authentication", status)
                # Treat non-zero status as likely auth/session issue
                self.authenticated = False
                self._last_auth_time = None
                try:
                    await self.async_request_refresh()
                except Exception:
                    pass
                return

            if not device_list:
                self._empty_device_states += 1
                _LOGGER.warning("Device states response contains no devices (consecutive=%d)", self._empty_device_states)
                # If we get repeated empty lists, likely auth/session expired – force re-auth
                if self._empty_device_states >= 3:
                    _LOGGER.warning("No devices returned %d times. Forcing re-authentication and re-discovery.", self._empty_device_states)
                    self.authenticated = False
                    self._last_auth_time = None
                    # Trigger a refresh cycle to reconnect/authenticate
                    try:
                        await self.async_request_refresh()
                    except Exception:
                        pass
                return

            _LOGGER.info("Processing %d devices from device states response", len(device_list))

            # Reset empty response counter on success
            self._empty_device_states = 0
            # Reset timeout count on successful device discovery
            self._timeout_count = 0

            # Clear existing devices and repopulate
            self.devices.clear()

            for device_info in device_list:
                if isinstance(device_info, dict):
                    device_guid = device_info.get("DeviceGuid") or device_info.get("Guid")

                    if device_guid:
                        # Initialize device with available info
                        self.devices[device_guid] = {
                            "guid": device_guid,
                            "position": device_info.get("Position", 0),
                            "raw_position": device_info.get("RawPosition", 0),
                            "channel": device_info.get("Channel", 1),
                            "time": device_info.get("Time"),
                            "reliability": device_info.get("StateReliability", 0),
                            "name": device_info.get("Name", f"TR7 Blind {device_guid[-8:]}")
                        }
                        _LOGGER.info("Added device: %s (position: %s)", device_guid[-8:], device_info.get("Position", 0))

            # Notify listeners of new device data
            self.async_set_updated_data(self.devices)

            _LOGGER.info("Device discovery completed. Total devices: %d", len(self.devices))

            # Force update of all entities to refresh their availability status
            for device_guid in self.devices:
                _LOGGER.debug("Device %s discovered and added to coordinator", device_guid[-8:])

        except Exception as err:
            _LOGGER.error("Error handling device states response: %s", err)

    async def _handle_device_state_change(self, data: dict) -> None:
        """Handle device state change message."""
        try:
            device_data = data.get("Data", {})
            device_guid = device_data.get("DeviceGuid")
            state = device_data.get("state", {})
            data_type = device_data.get("DataType")

            if not device_guid or data_type != DATA_TYPE_BLIND_POSITION:
                return

            # Update device state
            self.devices[device_guid] = {
                "guid": device_guid,
                "position": state.get("Position", 0),
                "raw_position": state.get("RawPosition", 0),
                "channel": state.get("Channel", 0),
                "time": state.get("Time"),
                "reliability": state.get("StateReliability", 0)
            }

            # Reset empty list counter when a state change arrives
            self._empty_device_states = 0

            # Notify listeners
            self.async_set_updated_data(self.devices)

            _LOGGER.debug("Updated device %s position to %s", device_guid, state.get("Position"))

        except Exception as err:
            _LOGGER.error("Error handling device state change: %s", err)


    async def set_cover_position(self, device_guid: str, position: int) -> None:
        """Set cover position."""
        try:
            # Get device info to include Channel if available
            device_info = self.devices.get(device_guid, {})
            channel = device_info.get("channel", 1)  # Default to channel 1

            _LOGGER.info("Setting position for device %s to %s (channel %s)", device_guid, position, channel)

            # Start with the most likely working endpoint based on TR7 traffic analysis
            await self._send_position_command(device_guid, position, channel)

        except Exception as err:
            _LOGGER.error("Error setting position for device %s: %s", device_guid, err)
            raise

    async def _send_position_command(self, device_guid: str, position: int, channel: int) -> None:
        """Send position command using the correct TR7 API format."""
        # Ensure connection/auth before sending
        await self._ensure_connected_and_authenticated()
        try:
            transaction_id = str(uuid.uuid4())

            # Convert HA position (0-100) to TR7 command codes
            # Based on original app: 101 = open (100%), 102 = close (0%)
            # TR7 uses inverted position scale: 0=open, 100=closed (opposite of HA)
            if position == 100:
                control_data = 101  # Open command
            elif position == 0:
                control_data = 102  # Close command
            else:
                # For intermediate positions, invert the scale
                # HA 85% open = TR7 15 (since TR7: 0=open, 100=closed)
                control_data = 100 - position

            # Use the exact format from the original app
            message = {
                "TransactionId": transaction_id,
                "Resource": RESOURCE_DEVICE_CONTROL,
                "Method": METHOD_POST,
                "Data": {
                    "DeviceGuid": device_guid,
                    "Channel": channel,
                    "ControlFeature": 3,
                    "SequnceExecutionOrder": 0,
                    "Data": control_data
                }
            }

            _LOGGER.info("Sending TR7 position command: device=%s, position=%s, control_data=%s",
                        device_guid[-8:], position, control_data)
            _LOGGER.debug("Full command: %s", message)
            await self._send_message(message)

            # Give some time for the command to be processed
            await asyncio.sleep(0.5)

        except UpdateFailed as err:
            _LOGGER.warning("Position command failed, attempting re-auth and retry: %s", err)
            # Force re-auth and retry once
            self.authenticated = False
            self._last_auth_time = None
            await self._ensure_connected_and_authenticated()
            await self._send_message(message)
            await asyncio.sleep(0.5)
        except Exception as err:
            _LOGGER.error("Failed to send position command: %s", err)
            raise

    async def open_cover(self, device_guid: str) -> None:
        """Open cover."""
        await self.set_cover_position(device_guid, 100)

    async def close_cover(self, device_guid: str) -> None:
        """Close cover."""
        await self.set_cover_position(device_guid, 0)

    async def stop_cover(self, device_guid: str) -> None:
        """Stop cover movement."""
        # Ensure connection/auth before sending
        await self._ensure_connected_and_authenticated()
        try:
            transaction_id = str(uuid.uuid4())
            device_info = self.devices.get(device_guid, {})
            channel = device_info.get("channel", 1)

            # Use the exact TR7 API format for stop command
            # Based on original app: Data: 103 = stop command
            message = {
                "TransactionId": transaction_id,
                "Resource": RESOURCE_DEVICE_CONTROL,
                "Method": METHOD_POST,
                "Data": {
                    "DeviceGuid": device_guid,
                    "Channel": channel,
                    "ControlFeature": 3,
                    "SequnceExecutionOrder": 0,
                    "Data": 103  # Stop command from original app
                }
            }

            _LOGGER.info("Stopping device %s (channel %s)", device_guid[-8:], channel)
            _LOGGER.debug("Stop command: %s", message)
            await self._send_message(message)

        except UpdateFailed as err:
            _LOGGER.warning("Stop command failed, attempting re-auth and retry: %s", err)
            self.authenticated = False
            self._last_auth_time = None
            await self._ensure_connected_and_authenticated()
            await self._send_message(message)
        except Exception as err:
            _LOGGER.error("Error stopping cover for device %s: %s", device_guid, err)
            raise

    async def close(self) -> None:
        """Close WebSocket connection."""
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass


        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as err:
                _LOGGER.warning("Error closing WebSocket: %s", err)

        self.websocket = None
        self.authenticated = False
        self._last_auth_time = None
        self._last_activity = None
        self._last_command_time = None
        self._command_delay = 0.5
        self._timeout_count = 0
        _LOGGER.info("TR7 Exalus connection closed")
