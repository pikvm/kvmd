#pragma once

#include "keyboard.h"
#include "proto.h"

//TODO implement

namespace kvmd::avr
{
    class UsbKeyboard : public Keyboard
    {
    public:
        uint8_t getType() { return PROTO::OUTPUTS1::KEYBOARD::USB; }
    };
}
