import asyncio
import logging
from ast import literal_eval
from datetime import timedelta
from functools import partial

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_NAME, CONF_TOKEN
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.entity import Entity
from miio import Device, DeviceException  # pylint: disable=import-error
from homeassistant.components import media_player, persistent_notification
from homeassistant.components.media_player import *
from homeassistant.components.media_player.const import *
from homeassistant.const import *

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Xiaomi Gateway Radio"
DATA_KEY = "media_player.xiaomi_gateway_radio"
DOMAIN = "xiaomi_gateway_radio"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_TOKEN): vol.All(cv.string, vol.Length(min=32, max=32)),
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    }
)

ATTR_MODEL = "model"
ATTR_FIRMWARE_VERSION = "firmware_version"
ATTR_HARDWARE_VERSION = "hardware_version"
SCAN_INTERVAL = timedelta(seconds=10)

# pylint: disable=unused-argument
@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the sensor from config."""
    if DATA_KEY not in hass.data:
        hass.data[DATA_KEY] = {}

    host = config.get(CONF_HOST)
    token = config.get(CONF_TOKEN)

    _LOGGER.info("Initializing %s with host %s (token %s...)", config.get(CONF_NAME), host, token[:5])

    try:
        miio_device = Device(host, token)
        device_info = miio_device.info()
        model = device_info.model
        _LOGGER.info(
            "%s %s %s detected",
            model,
            device_info.firmware_version,
            device_info.hardware_version,
        )

        device = GatewayRadio(miio_device, config, device_info)
    except DeviceException:
        raise PlatformNotReady

    hass.data[DATA_KEY][host] = device
    async_add_devices([device], update_before_add=True)


class XiaomiMiioGenericDevice(Entity):
    """Representation of a Xiaomi Air Quality Monitor."""

    def __init__(self, device, config, device_info):
        """Initialize the entity."""
        self._device = device

        self._name = config.get(CONF_NAME)
        self._model = device_info.model
        self._unique_id = "{}-{}".format(device_info.model, device_info.mac_address)

        self._available = True
        self._state = None
        self._state_attrs = {}
        self._delay_update = 0

    def set_properties(self, properties):
        if self._properties_getter != CMD_GET_PROPERTIES:
            self._properties = list(set(properties))
            return self._properties
        else:
            attrs = {}
            for p in properties:
                p = literal_eval(p)
                attrs[p["siid"], p["piid"]] = p.pop("name", p["did"]), p
            self._properties = attrs
            return list(i[0] for i in attrs.values())

    @property
    def should_poll(self):
        """Poll the miio device."""
        return True

    @property
    def unique_id(self):
        """Return an unique ID."""
        return self._unique_id

    @property
    def name(self):
        """Return the name of this entity, if any."""
        return self._name

    @property
    def available(self):
        """Return true when state is known."""
        return self._available

    @property
    def state(self):
        """Return the state of the device."""
        return self._state

    @property
    def device_state_attributes(self):
        """Return the state attributes of the device."""
        return self._state_attrs

    async def _try_command(self, mask_error, func, *args, **kwargs):
        """Call a device command handling error messages."""
        try:
            result = await self.hass.async_add_job(partial(func, *args, **kwargs))

            _LOGGER.info("Response received from miio device: %s", result)

            return result and (result[0] == "ok")
        except DeviceException as exc:
            _LOGGER.error(mask_error, exc)
            return False

    async def async_update(self):
        """Fetch state from the miio device."""
        pass

    async def async_command(self, method: str, params):
        """Send a raw command to the device."""
        _LOGGER.info("Sending command: %s %s" % (method, params))
        await self._try_command(
            "Turning the miio device on failed.", self._device.send, method, params
        )

class GatewayRadio(XiaomiMiioGenericDevice, MediaPlayerEntity):
    def __init__(self, device, config, device_info):
        XiaomiMiioGenericDevice.__init__(self, device, config, device_info)
        self._player_state = STATE_PAUSED
        self._device_class = DEVICE_CLASS_SPEAKER
        self._volume_level = None

    @property
    def supported_features(self):
        """Return the supported features."""
        return (SUPPORT_PLAY | SUPPORT_PAUSE | SUPPORT_VOLUME_SET
            | SUPPORT_PREVIOUS_TRACK | SUPPORT_NEXT_TRACK)

    @property
    def state(self):
        """Return the state of the player."""
        return self._player_state

    @property
    def device_class(self):
        """Return the device class of the media player."""
        return self._device_class

    async def async_media_play(self):
        """Send play command."""
        result = await self._try_command(
            f"Setting property for {self._name} failed.",
            self._device.send,
            "play_fm",
            ["on"],
        )

        if result:
            self._player_state = STATE_PLAYING
            self._delay_update = 3
            self.schedule_update_ha_state()

    async def async_media_pause(self):
        """Send pause command."""
        result = await self._try_command(
            f"Setting property for {self._name} failed.",
            self._device.send,
            "play_fm",
            ["off"],
        )
        if result:
            self._player_state = STATE_PAUSED
            self.schedule_update_ha_state()


    async def async_media_previous_track(self):
        """Send previous track command."""
        result = await self._try_command(
            f"Setting property for {self._name} failed.",
            self._device.send,
            "play_fm",
            ["next"],
        )
        if result:
            self._player_state = STATE_PLAYING
            self._delay_update = 3

    async def async_media_next_track(self):
        """Send next track command."""
        result = await self._try_command(
            f"Setting property for {self._name} failed.",
            self._device.send,
            "play_fm",
            ["prev"],
        )
        if result:
            self._player_state = STATE_PLAYING
            self._delay_update = 3

    async def async_set_volume_level(self, volume):
        """Set the volume level, range 0..1."""
        try:
            result = await self._try_command(
                f"Setting property for {self._name} failed.",
                self._device.send,
                "volume_ctrl_fm",
                [str(volume * 100)],
            )
        except KeyError:
            self._volume_level = volume
            self.schedule_update_ha_state()

    @property
    def volume_level(self):
        """Return the volume level of the media player (0..1)."""
        return self._volume_level

    @property
    def media_title(self):
        """Return the title of current playing media."""
        return self._state_attrs.get('current_program')

    async def async_update(self):
        if self._delay_update:
            await asyncio.sleep(self._delay_update)
            self._delay_update = 0
        try:
            state = await self.hass.async_add_job(
                self._device.send, "get_prop_fm", []
            )
            _LOGGER.debug("Got new state: %s", state)
            self._available = True
            self._player_state = STATE_PLAYING if state.get('current_status') == 'run' \
                else STATE_PAUSED if state.get('current_status') == 'pause' else STATE_UNKNOWN
            self._volume_level = state.get('current_volume', 0) / 100
            self._state_attrs.update(state)

        except DeviceException as ex:
            self._available = False
            _LOGGER.error("Got exception while fetching the state: %s", ex)
