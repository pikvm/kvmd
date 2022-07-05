#pragma once

#include "usb/hid.h"
#include "proto.h"

//TODO implement

namespace kvmd::stm32
{
    class UsbMouseAbsolute : public UsbMouse
    {
    public:
        uint8_t getType() { return PROTO::OUTPUTS1::MOUSE::USB_ABS; }
    };
}
