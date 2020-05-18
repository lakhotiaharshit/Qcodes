import textwrap
from typing import Optional, Union
from collections import defaultdict, namedtuple

from qcodes import VisaInstrument
from qcodes.utils.helpers import create_on_off_val_mapping
import qcodes.utils.validators as vals
from .KeysightB1530A import B1530A
from .KeysightB1520A import B1520A
from .KeysightB1517A import B1517A
from .KeysightB1500_module import B1500Module, parse_module_query_response, \
    parse_spot_measurement_response
from . import constants
from .constants import ChannelList
from .message_builder import MessageBuilder

_AITResponse = namedtuple('AITResponse', ['type', 'mode', 'time'])


class KeysightB1500(VisaInstrument):
    """Driver for Keysight B1500 Semiconductor Parameter Analyzer.

    For the list of supported modules, refer to :meth:`from_model_name`.
    """
    calibration_time_out = 60  # 30 seconds suggested by manual

    def __init__(self, name, address, **kwargs):
        super().__init__(name, address, terminator="\r\n", **kwargs)

        self.by_slot = {}
        self.by_channel = {}
        self.by_kind = defaultdict(list)

        self._find_modules()

        self.add_parameter('autozero_enabled',
                           unit='',
                           label='Autozero enabled of the high-resolution ADC',
                           set_cmd=self._set_autozero,
                           get_cmd=None,
                           val_mapping=create_on_off_val_mapping(
                               on_val=True, off_val=False),
                           docstring=textwrap.dedent("""
            Enable or disable cancelling of the offset of the 
            high-resolution A/D converter (ADC).
    
            Set the function to OFF in cases that the measurement speed is 
            more important than the measurement accuracy. This roughly halves
            the integration time."""))

        self.add_parameter('use_nplc_for_high_speed_adc',
                           set_cmd=self._set_nplc_for_high_speed_adc,
                           get_cmd=self._get_nplc_for_high_speed_adc,
                           vals=vals.Numbers(0, 1023),
                           doctsring=textwrap.dedent("""
            Set the high-speed ADC to NPLC mode, with optionally defining 
            number of averaging samples via argument `n`.

            Args:
                n: Value that defines the number of averaging samples given by
                    the following formula:

                    ``Number of averaging samples = n / 128``.

                    n=1 to 100. Default setting is 1 (if `None` is passed).

                    The Keysight B1500 gets 128 samples in a power line cycle,
                    repeats this for the times you specify, and performs
                    averaging to get the measurement data. (For more info see
                    Table 4-21.).  Note that the integration time will not be
                    updated if a non-integer value is written to the B1500.
                    """))

        self.add_parameter('use_nplc_for_high_resolution_adc',
                           set_cmd=self._set_nplc_for_high_resolution_adc,
                           get_cmd=self._get_nplc_for_high_resolution_adc,
                           docstring=textwrap.dedent("""
            Set the high-resolution ADC to NPLC mode, with optionally defining
            the number of PLCs per sample via argument `n`.

            Args:
                n: Value that defines the integration time given by the
                    following formula:
    
                    ``Integration time = n / power line frequency``.
    
                    n=1 to 100. Default setting is 1 (if `None` is passed).
                    (For more info see Table 4-21.).  Note that the integration
                    time will not be updated if a non-integer value is written
                    to the B1500."""))

        self.add_parameter('use_manual_mode_for_high_speed_adc',
                           set_cmd=self._set_manual_mode_for_high_speed_adc,
                           get_cmd=self._get_manual_mode_for_high_speed_adc,
                           docstring=textwrap.dedent("""
            Set the high-speed ADC to manual mode, with optionally defining 
            number of averaging samples via argument `n`.

            Use ``n=1`` to disable averaging (``n=None`` uses the default
            setting from the instrument which is also ``n=1``).

            Args:
                n: Number of averaging samples, between 1 and 1023. Default
                    setting is 1. (For more info see Table 4-21.)
                    Note that the integration time will not be updated
                    if a non-integer value is written to the B1500."""))


        # Instrument is initialized with this setting having value of
        # `False`, hence let's set the parameter to this value since it is
        # not possible to request this value from the instrument.
        self.autozero_enabled.cache.set(False)

        self.connect_message()

    def add_module(self, name: str, module: B1500Module):
        super().add_submodule(name, module)

        self.by_kind[module.MODULE_KIND].append(module)
        self.by_slot[module.slot_nr] = module
        for ch in module.channels:
            self.by_channel[ch] = module

    def reset(self):
        """Performs an instrument reset.

        This does not reset error queue!
        """
        self.write("*RST")

    def get_status(self) -> int:
        return int(self.ask("*STB?"))

    # TODO: Data Output parser: At least for Format FMT1,0 and maybe for a
    # second (binary) format. 8 byte binary format would be nice because it
    # comes with time stamp
    # FMT1,0: ASCII (12 digits data with header) <CR/LF^EOI>

    def _find_modules(self):
        from .constants import UNT

        r = self.ask(MessageBuilder()
                     .unt_query(mode=UNT.Mode.MODULE_INFO_ONLY)
                     .message
                     )

        slot_population = parse_module_query_response(r)

        for slot_nr, model in slot_population.items():
            module = self.from_model_name(model, slot_nr, self)

            self.add_module(name=module.short_name, module=module)

    @staticmethod
    def from_model_name(model: str, slot_nr: int, parent: 'KeysightB1500',
                        name: Optional[str] = None) -> 'B1500Module':
        """Creates the correct instance of instrument module by model name.

        Args:
            model: Model name such as 'B1517A'
            slot_nr: Slot number of this module (not channel number)
            parent: Reference to B1500 mainframe instance
            name: Name of the instrument instance to create. If `None`
                (Default), then the name is autogenerated from the instrument
                class.

        Returns:
            A specific instance of :class:`.B1500Module`
        """
        if model == "B1517A":
            return B1517A(slot_nr=slot_nr, parent=parent, name=name)
        elif model == "B1520A":
            return B1520A(slot_nr=slot_nr, parent=parent, name=name)
        elif model == "B1530A":
            return B1530A(slot_nr=slot_nr, parent=parent, name=name)
        else:
            raise NotImplementedError("Module type not yet supported.")

    def enable_channels(self, channels: ChannelList = None):
        """Enable specified channels.

        If channels is omitted or `None`, then all channels are enabled.
        """
        msg = MessageBuilder().cn(channels)

        self.write(msg.message)

    def disable_channels(self, channels: ChannelList = None):
        """Disable specified channels.

        If channels is omitted or `None`, then all channels are disabled.
        """
        msg = MessageBuilder().cl(channels)

        self.write(msg.message)

    # Response parsing functions as static methods for user convenience
    parse_spot_measurement_response = parse_spot_measurement_response
    parse_module_query_response = parse_module_query_response

    def _setup_integration_time(self,
                                adc_type: constants.AIT.Type,
                                mode: Union[constants.AIT.Mode, int],
                                coeff: Optional[int] = None
                                ) -> None:
        """See :meth:`MessageBuilder.ait` for information"""
        if coeff is not None:
            coeff = int(coeff)
        self.write(MessageBuilder()
                   .ait(adc_type=adc_type, mode=mode, coeff=coeff)
                   .message
                   )

    def _set_nplc_for_high_speed_adc(
            self, n: Optional[int] = None) -> None:
        """
          Set the high-speed ADC to NPLC mode, with optionally defining number
          of averaging samples via argument `n`.

          Args:
              n: Value that defines the number of averaging samples given by
                  the following formula:

                  ``Number of averaging samples = n / 128``.

                  n=1 to 100. Default setting is 1 (if `None` is passed).

                  The Keysight B1500 gets 128 samples in a power line cycle,
                  repeats this for the times you specify, and performs
                  averaging to get the measurement data. (For more info see
                  Table 4-21.).  Note that the integration time will not be
                  updated if a non-integer value is written to the B1500.
          """
        self._setup_integration_time(
            adc_type=constants.AIT.Type.HIGH_SPEED,
            mode=constants.AIT.Mode.NPLC,
            coeff=n
        )

    def _get_nplc_for_high_speed_adc(self):
        """
        Use ``lrn_query`` to obtain the ADC averaging time or integration
        time setting
        """
        response = self.ask(
            MessageBuilder().lrn_query(self.channels[0]).message)
        high_speed_type = _AITResponse(
            response.split(";")[constants.AIT.Type.HIGH_SPEED].split(",")
        )
        if high_speed_type.mode != constants.AIT.Mode.NPLC:
            raise Warning("Not in NPLC mode")
        time = high_speed_type.time
        return time

    def _set_nplc_for_high_resolution_adc(
            self, n: Optional[int] = None) -> None:
        """
        Set the high-resolution ADC to NPLC mode, with optionally defining
        the number of PLCs per sample via argument `n`.

        Args:
            n: Value that defines the integration time given by the
                following formula:

                ``Integration time = n / power line frequency``.

                n=1 to 100. Default setting is 1 (if `None` is passed).
                (For more info see Table 4-21.).  Note that the integration
                time will not be updated if a non-integer value is written
                to the B1500.
        """
        self._setup_integration_time(
            adc_type=constants.AIT.Type.HIGH_RESOLUTION,
            mode=constants.AIT.Mode.NPLC,
            coeff=n
        )

    def _get_nplc_for_high_resolution_adc(self):
        """
        Use ``lrn_query`` to obtain the ADC averaging time or integration
        time setting
        """
        response = self.ask(
            MessageBuilder().lrn_query(self.channels[0]).message)
        high_resolution_type = _AITResponse(
            response.split(";")[constants.AIT.Type.HIGH_RESOLUTION].split(","))
        if high_resolution_type.mode != constants.AIT.Mode.NPLC:
            raise Warning("Not in NPLC mode")

        time = high_resolution_type.time
        return time

    def _set_manual_mode_for_high_speed_adc(
            self, n: Optional[int] = None) -> None:
        """
        Set the high-speed ADC to manual mode, with optionally defining number
        of averaging samples via argument `n`.

        Use ``n=1`` to disable averaging (``n=None`` uses the default
        setting from the instrument which is also ``n=1``).

        Args:
            n: Number of averaging samples, between 1 and 1023. Default
                setting is 1. (For more info see Table 4-21.)
                Note that the integration time will not be updated
                if a non-integer value is written to the B1500.
        """
        self._setup_integration_time(
            adc_type=constants.AIT.Type.HIGH_SPEED,
            mode=constants.AIT.Mode.MANUAL,
            coeff=n
        )

    def _get_manual_mode_for_high_speed_adc(self):
        response = self.ask(
            MessageBuilder().lrn_query(self.channels[0]).message)
        high_speed_type = _AITResponse(
            response.split(";")[constants.AIT.Type.HIGH_SPEED].split(","))
        if high_speed_type.mode != constants.AIT.Mode.MANUAL:
            raise Warning("Not in Manual mode")

        time = high_speed_type.time
        return time

    def _set_autozero(self, do_autozero: bool) -> None:
        self.write(MessageBuilder().az(do_autozero=do_autozero).message)

    def self_calibration(self,
                         slot: Optional[Union[constants.SlotNr, int]] = None
                         ) -> constants.CALResponse:
        """
        Performs the self calibration of the specified module (SMU) and
        returns the result. Failed modules are disabled, and can only be
        enabled by the ``RCV`` command.

        Calibration takes about 30 seconds (the visa timeout for it is
        controlled by :attr:`calibration_time_out` attribute).

        Execution Conditions: No SMU may be in the high voltage state
        (forcing more than ±42 V, or voltage compliance set to more than
        ±42 V). Before starting the calibration, open the measurement
        terminals.

        Args:
            slot: Slot number of the slot that installs the module to perform
                the self-calibration. For Ex:
                constants.SlotNr.ALL, MAINFRAME, SLOT01, SLOT02 ...SLOT10
                If not specified, the calibration is performed for all the
                modules and the mainframe.
        """
        msg = MessageBuilder().cal_query(slot=slot)
        with self.root_instrument.timeout.set_to(self.calibration_time_out):
            response = self.ask(msg.message)
        return constants.CALResponse(int(response))

    def error_message(self, mode: Optional[Union[constants.ERRX.Mode,
                                                 int]] = None) -> str:
        """
        This method reads one error code from the head of the error
        queue and removes that code from the queue. The read error is
        returned as the response of this method.

        Args:
            mode: If no valued passed returns both the error value and the
                error message. See :class:`.constants.ERRX.Mode` for possible
                arguments.

        Returns:
            In the default case response message contains an error message
            and a custom message containing additional information such as
            the slot number. They are separated by a semicolon (;). For
            example, if the error 305 occurs on the slot 1, this method
            returns the following response. 305,"Excess current in HPSMU.;
            SLOT1" If no error occurred, this command returns 0,"No Error."
        """

        msg = MessageBuilder().errx_query(mode=mode)
        response = self.ask(msg.message)
        return response
