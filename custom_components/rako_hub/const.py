"""Constants for the Rako Hub integration."""
DOMAIN = "rako_hub"
CONF_HOST = "host"
CONF_PORT = "port"
DEFAULT_PORT = 9762

EVENT_FEEDBACK = "rako_hub_feedback"   # every command on the Rako wire (keypad, app, timer, HA)
EVENT_TRACKER = "rako_hub_tracker"     # level/scene changes in progress

STRUCTURE_REFRESH_HOURS = 6            # rooms/channels/scene-levels re-read interval

SERVICE_SET_SCENE = "set_scene"
SERVICE_SET_LEVEL = "set_level"
SERVICE_FADE = "fade"
SERVICE_SET_RGB = "set_rgb"
SERVICE_STORE_SCENE = "store_scene"
SERVICE_REFRESH = "refresh"
