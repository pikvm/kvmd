#pragma once

#include "usb/hid.h"
#include "proto.h"

//TODO implement

namespace kvmd::avr
{
    class UsbMouseRelative : public ::UsbMouseRelative
    {
    public:
        uint8_t getType() override { return PROTO::OUTPUTS1::MOUSE::USB_REL; }
    };

}
