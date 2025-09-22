"""Constants for the TR7 Exalus integration."""

DOMAIN = "tr7_exalus"

# Configuration
CONF_HOST = "host"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# Default values
DEFAULT_PORT = 81
DEFAULT_TIMEOUT = 30

# WebSocket API endpoints
WS_API_PATH = "/api"

# TR7 API Methods
METHOD_GET = 0
METHOD_POST = 1
METHOD_PUT = 2
METHOD_LOGIN = 3

# TR7 API Resources
RESOURCE_LOGIN = "/users/user/login"
RESOURCE_DEVICE_STATES = "/devices/channels/states"
RESOURCE_DEVICE_STATE_CHANGED = "/info/devices/device/state/changed"
RESOURCE_DEVICE_CONTROL = "/devices/device/control"
RESOURCE_DEVICE_POSITION = "/devices/device/position"
RESOURCE_DEVICE_STOP = "/devices/device/stop"

# Data types
DATA_TYPE_BLIND_POSITION = "BlindPosition"
DATA_TYPE_SCENE_EXECUTED = "SceneExecuted"

# Auth/session behavior
# Some TR7 hubs appear to expire session auth within a few minutes.
# We proactively refresh authentication before this window elapses.
AUTH_REFRESH_MINUTES = 3

# Entity platforms
PLATFORMS = ["cover"]
