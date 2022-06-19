#pragma once
#ifdef ARDUINO_ARCH_AVR
#include "usb/avr-hid.h"
#else
#include "usb/stm32-hid.h"
#endif