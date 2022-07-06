#pragma once

#include "usb/hid.h"
#include "proto.h"

//TODO implement

namespace kvmd::avr
{
    class UsbMouseAbsolute : public ::UsbMouseAbsolute
    {
    public:
        uint8_t getType() override { return PROTO::OUTPUTS1::MOUSE::USB_ABS; }
    };
}
