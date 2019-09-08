import datetime
import logging
import time
from functools import reduce

from pyrainbird.data import (
    ModelAndVersion,
    AvailableStations,
    CommandSupport,
    States,
    WaterBudget,
    _DEFAULT_PAGE,
)
from . import rainbird
from .client import RainbirdClient
from .rainbird import RAIBIRD_COMMANDS


class RainbirdController:
    def __init__(
        self,
        server,
        password,
        update_delay=20,
        retry=3,
        retry_sleep=10,
        logger=logging.getLogger(__name__),
    ):
        self.rainbird_client = RainbirdClient(
            server, password, retry, retry_sleep, logger
        )
        self.logger = logger
        self.zones = States("00")
        self.rain_sensor = None
        self.update_delay = update_delay
        self.zone_update_time = None
        self.sensor_update_time = None

    def get_model_and_version(self) -> ModelAndVersion:
        return self._process_command(
            lambda response: ModelAndVersion(
                response["modelID"],
                response["protocolRevisionMajor"],
                response["protocolRevisionMinor"],
            ),
            "ModelAndVersion",
        )

    def get_available_stations(self, page=_DEFAULT_PAGE) -> AvailableStations:
        return self._process_command(
            lambda resp: AvailableStations(
                "%08X" % resp["setStations"], page=resp["pageNumber"]
            ),
            "AvailableStations",
            page,
        )

    def get_command_support(self, command) -> CommandSupport:
        return self._process_command(
            lambda resp: CommandSupport(
                resp["support"], echo=resp["commandEcho"]
            ),
            "CommandSupport",
            command,
        )

    def get_serial_number(self) -> int:
        return self._process_command(
            lambda resp: resp["serialNumber"], "SerialNumber"
        )

    def get_current_time(self) -> datetime.time:
        return self._process_command(
            lambda resp: datetime.time(
                resp["hour"], resp["minute"], resp["second"]
            ),
            "CurrentTime",
        )

    def get_current_date(self) -> datetime.date:
        return self._process_command(
            lambda resp: datetime.date(
                resp["year"], resp["month"], resp["day"]
            ),
            "CurrentDate",
        )

    def water_budget(self, budget: int) -> WaterBudget:
        return self._process_command(
            lambda resp: WaterBudget(
                resp["programCode"], resp["highByte"], resp["lowByte"]
            ),
            "WaterBudget",
            budget,
        )

    def get_rain_sensor_state(self) -> bool:
        if _check_delay(self.sensor_update_time, self.update_delay):
            self.logger.debug("Requesting current Rain Sensor State")
            response = self._process_command(
                lambda resp: bool(resp["sensorState"]),
                "CurrentRainSensorState",
            )
            if isinstance(response, bool):
                self.rain_sensor = response
                self.sensor_update_time = time.time()
            else:
                self.rain_sensor = None
        return self.rain_sensor

    def get_zone_state(self, zone, page=_DEFAULT_PAGE) -> bool:
        if _check_delay(self.zone_update_time, self.update_delay):
            response = self._update_irrigation_state(page)
            if not isinstance(response, States):
                self.zones = States("00")
                return None
            else:
                self.zone_update_time = time.time()
        return self.zones.active(zone)

    def set_program(self, program: int) -> bool:
        return self._process_command(
            lambda resp: True, "ManuallyRunProgram", program
        )

    def test_zone(self, zone: int) -> bool:
        return self._process_command(lambda resp: True, "TestStations", zone)

    def irrigate_zone(self, zone, minutes) -> bool:
        response = self._process_command(
            lambda resp: True, "ManuallyRunStation", zone, minutes
        )
        self._update_irrigation_state()
        return response == True and self.zones.active(zone)

    def stop_irrigation(self) -> bool:
        response = self._process_command(lambda resp: True, "StopIrrigation")
        self._update_irrigation_state()
        return response == True and not reduce(
            (lambda x, y: x or y), self.zones.states
        )

    def get_rain_delay(self) -> int:
        return self._process_command(
            lambda resp: resp["delaySetting"], "RainDelayGet"
        )

    def set_rain_delay(self, days) -> bool:
        return self._process_command(lambda resp: True, "RainDelaySet", days)

    def advance_zone(self, param) -> bool:
        return self._process_command(
            lambda resp: True, "AdvanceStation", param
        )

    def get_current_irrigation(self) -> int:
        return self._process_command(
            lambda resp: resp["irrigationState"], "CurrentIrrigationState"
        )

    def schedule(self, param1, param2):
        return self._process_command(
            lambda resp: resp, "CurrentSchedule", param1, param2
        )

    def command(self, command, *args):
        data = rainbird.encode(command, *args)
        self.logger.debug("Request to line: " + str(data))
        decrypted_data = self.rainbird_client.request(
            data,
            RAIBIRD_COMMANDS["ControllerCommands"]["%sRequest" % command][
                "length"
            ],
        )
        self.logger.debug("Response from line: " + str(decrypted_data))
        if decrypted_data is None:
            self.logger.warn("Empty response from controller")
            return None
        decoded = rainbird.decode(decrypted_data)
        if (
            decrypted_data[:2]
            != RAIBIRD_COMMANDS["ControllerCommands"]["%sRequest" % command][
                "response"
            ]
        ):
            raise Exception(
                "Status request failed with wrong response! Requested %s but got %s:\n%s"
                % (
                    RAIBIRD_COMMANDS["ControllerCommands"][
                        "%sRequest" % command
                    ]["response"],
                    decrypted_data[:2],
                    decoded,
                )
            )
        self.logger.debug("Response: %s" % decoded)
        return decoded

    def _process_command(self, funct, cmd, *args):
        response = self.command(cmd, *args)
        return (
            funct(response)
            if response is not None
            and response["type"]
            == RAIBIRD_COMMANDS["ControllerResponses"][
                RAIBIRD_COMMANDS["ControllerCommands"][cmd + "Request"][
                    "response"
                ]
            ]["type"]
            else response
        )

    def _update_irrigation_state(self, page=_DEFAULT_PAGE):
        result = self._process_command(
            lambda resp: States(("%08X" % resp["activeStations"])[:2]),
            "CurrentStationsActive",
            page,
        )
        if isinstance(result, States):
            self.zones = result
        return result


def _check_delay(update_time, update_delay):
    return update_time is None or time.time() > (update_time + update_delay)
