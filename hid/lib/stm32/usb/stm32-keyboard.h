#pragma once

#include "keyboard.h"
#include "proto.h"
#include <USBComposite.h>

// TODO implement

namespace kvmd::stm32
{
    class UsbKeyboard : public Keyboard
    {
    public:
        UsbKeyboard() = delete;

        UsbKeyboard(USBHID *_hid) : _hid(_hid), _kbd(*_hid, 0)
        {
        }

        uint8_t getType() { return PROTO::OUTPUTS1::KEYBOARD::USB; }

        void clear()
        {
            _kbd.releaseAll();
        }

        void begin()
        {
            _hid->begin(HID_BOOT_KEYBOARD);
            _kbd.begin();
        }

    private:
        USBHID *_hid;
        HIDKeyboard _kbd;
    };

}
