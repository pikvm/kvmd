#pragma once

#include "keyboard.h"
#include "proto.h"
#include "avr-hid.h"

//TODO implement

namespace kvmd::avr
{
    class UsbKeyboard : public ::UsbKeyboard
    {
    public:
        uint8_t getType() override { return PROTO::OUTPUTS1::KEYBOARD::USB; }
    };
}
