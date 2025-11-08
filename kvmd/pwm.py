# ========================================================================== #
#                                                                            #
#    KVMD - The main PiKVM daemon.                                           #
#                                                                            #
#    Copyright (C) 2018-2024  Maxim Devaev <mdevaev@gmail.com>               #
#                                                                            #
#    This program is free software: you can redistribute it and/or modify    #
#    it under the terms of the GNU General Public License as published by    #
#    the Free Software Foundation, either version 3 of the License, or       #
#    (at your option) any later version.                                     #
#                                                                            #
#    This program is distributed in the hope that it will be useful,         #
#    but WITHOUT ANY WARRANTY; without even the implied warranty of          #
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           #
#    GNU General Public License for more details.                            #
#                                                                            #
#    You should have received a copy of the GNU General Public License       #
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.  #
#                                                                            #
# ========================================================================== #


import os
import errno
import time

from . import env


# =====
class PwmError(IOError):
    pass


# =====
class Pwm:
    # Based on https://github.com/vsergeev/python-periphery
    # Copyright (c) 2015-2023 vsergeev / Ivan (Vanya) A. Sergeev

    __STAT_RETRIES = 10  # Number of retries to check for successful PWM export on open
    __STAT_DELAY = 0.1  # Delay between check for scucessful PWM export on open (100ms)

    def __init__(self, chip: int, ch: int) -> None:
        """
        Instantiate a PWM object and open the sysfs PWM corresponding to the
        specified chip and channel.

        Args:
            chip (int): PWM chip number.
            ch (int): PWM channel number.

        Returns:
            PWM: PWM object.

        Raises:
            PwmError: if an I/O or OS error occurs.
            TypeError: if `chip` or `ch` types are invalid.
            LookupError: if PWM chip does not exist.
            TimeoutError: if waiting for PWM export times out.
        """

        self.__chip = -1
        self.__ch = -1
        self.__open(chip, ch)

    def __del__(self) -> None:
        self.close()

    def __open(self, chip: int, ch: int) -> None:
        chip_path = f"{env.SYSFS_PREFIX}/sys/class/pwm/pwmchip{chip}"
        ch_path = f"{env.SYSFS_PREFIX}/sys/class/pwm/pwmchip{chip}/pwm{ch}"

        if not os.path.isdir(chip_path):
            raise LookupError(f"Opening PWM: PWM chip {chip} is not found")

        if not os.path.isdir(ch_path):
            # Export the PWM
            try:
                with open(os.path.join(chip_path, "export"), "w") as file:
                    file.write(f"{ch}\n")
            except IOError as ex:
                raise PwmError(ex.errno, f"Exporting PWM channel: {ex.strerror}")

            # Loop until PWM is exported
            exported = False
            for _ in range(self.__STAT_RETRIES):
                if os.path.isdir(ch_path):
                    exported = True
                    break
                time.sleep(self.__STAT_DELAY)

            if not exported:
                raise TimeoutError(f"Exporting PWM: waiting for {ch_path!r} timed out")

            # Loop until period is writable. This could take some time after
            # export as application of udev rules after export is asynchronous.
            for retry in range(self.__STAT_RETRIES):
                try:
                    with open(os.path.join(ch_path, "period"), "w"):
                        break
                except IOError as ex:
                    if ex.errno != errno.EACCES or (ex.errno == errno.EACCES and retry == self.__STAT_RETRIES - 1):
                        raise PwmError(ex.errno, f"Opening PWM period: {ex.strerror}")
                time.sleep(self.__STAT_DELAY)

        self.__chip = chip
        self.__ch = ch

    def close(self) -> None:
        if self.__chip >= 0 and self.__ch >= 0:
            # Unexport the PWM channel
            try:
                with open(f"{env.SYSFS_PREFIX}/sys/class/pwm/pwmchip{self.__chip}/unexport", "w") as file:
                    file.write(f"{self.__ch}\n")
            except OSError as ex:
                raise PwmError(ex.errno, f"Unexporting PWM: {ex.strerror}")
        self.__chip = -1
        self.__ch = -1

    def __write_channel_attr(self, attr: str, value: str) -> None:
        path = f"{env.SYSFS_PREFIX}/sys/class/pwm/pwmchip{self.__chip}/pwm{self.__ch}/{attr}"
        with open(path, "w") as file:
            file.write(value + "\n")

    def __read_channel_attr(self, attr: str) -> str:
        path = f"{env.SYSFS_PREFIX}/sys/class/pwm/pwmchip{self.__chip}/pwm{self.__ch}/{attr}"
        with open(path, "r") as file:
            return file.read().strip()

    # =====

    def get_period_ns(self) -> int:
        period_ns_str = self.__read_channel_attr("period")
        try:
            period_ns = int(period_ns_str)
        except ValueError:
            raise PwmError(None, f"Unknown period value: {period_ns_str!r}")
        return period_ns

    def set_period_ns(self, period_ns: int) -> None:
        self.__write_channel_attr("period", str(period_ns))

    # =====

    def get_duty_cycle_ns(self) -> int:
        duty_cycle_ns_str = self.__read_channel_attr("duty_cycle")
        try:
            return int(duty_cycle_ns_str)
        except ValueError:
            raise PwmError(None, f"Unknown duty cycle value: {duty_cycle_ns_str!r}")

    def set_duty_cycle_ns(self, duty_cycle_ns: int) -> None:
        self.__write_channel_attr("duty_cycle", str(duty_cycle_ns))

    # =====

    def get_polarity(self) -> str:
        return self.__read_channel_attr("polarity")

    def set_polarity(self, polarity: str) -> None:
        polarity = polarity.lower()
        if polarity not in ["normal", "inversed"]:
            raise ValueError("Invalid polarity, can be: 'normal' or 'inversed'")
        self.__write_channel_attr("polarity", polarity)

    # =====

    def is_enabled(self) -> bool:
        enabled = self.__read_channel_attr("enable")
        try:
            return bool(int(enabled))
        except ValueError:
            raise PwmError(None, f"Unknown enabled value: {enabled!r}")

    def set_enabled(self, value: bool) -> None:
        self.__write_channel_attr("enable", str(int(value)))
