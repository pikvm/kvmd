#pragma once
#include "keyboard.h"
#include "usb/hid.h"

namespace kvmd
{

    struct Factory
    {
        static Keyboard *makeKeyboard(uint8_t kbd);
        static UsbMouse *makeMouse(uint8_t mouse);
    };

}
