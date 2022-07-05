#include "factory.h"
#include "proto.h"
#include "usb/avr-keyboard.h"
#include "usb/avr-mouse-relative.h"
#include "usb/avr-mouse-win98.h"

#ifndef ARDUINO_ARCH_AVR
#error "This is supposed to be included only for AVR"
#endif

kvmd::Keyboard* kvmd::Factory::makeKeyboard(uint8_t kbd)
{
	switch (kbd) {
#	ifdef HID_WITH_USB
		case PROTO::OUTPUTS1::KEYBOARD::USB:
            return new avr::UsbKeyboard();
#	endif
#	ifdef HID_WITH_PS2
		case PROTO::OUTPUTS1::KEYBOARD::PS2:
            return new Ps2Keyboard();
#	endif
	}
	return new Keyboard();
}

kvmd::UsbMouse* kvmd::Factory::makeMouse(uint8_t mouse)
{
	switch (mouse) {
#	ifdef HID_WITH_USB
		case PROTO::OUTPUTS1::MOUSE::USB_ABS:
            return new avr::UsbMouseAbsolute();
		case PROTO::OUTPUTS1::MOUSE::USB_WIN98:
            return new avr::UsbMouseAbsoluteWin98();
		case PROTO::OUTPUTS1::MOUSE::USB_REL:
            return new avr::UsbMouseRelative();;
#	endif
	}    
	return new UsbMouse();
}

