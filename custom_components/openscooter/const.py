"""Constants for the OpenScooter integration."""

DOMAIN = "openscooter"
VERSION = "0.6.5"

CONF_WEBHOOK_ID = "webhook_id"
CONF_SECRET = "secret"

# Smart charging options
CONF_PLUG = "plug_switch"
CONF_POWER = "power_sensor"
CONF_ENERGY = "energy_sensor"
CONF_TARGET = "target_percent"
CONF_START_BELOW = "start_below_percent"
CONF_OFF_W = "off_power_w"
CONF_OFF_MIN = "off_delay_min"

DEFAULT_TARGET = 90
DEFAULT_START_BELOW = 95
DEFAULT_OFF_W = 8
DEFAULT_OFF_MIN = 6

STATIC_URL = "/openscooter_static"
SIGNAL_UPDATE = "openscooter_update_{}"

EVENT_CHARGE_STARTED = "openscooter_charge_started"
EVENT_CHARGE_FINISHED = "openscooter_charge_finished"
