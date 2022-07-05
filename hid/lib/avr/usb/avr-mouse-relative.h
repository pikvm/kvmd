#pragma once

#include "usb/hid.h"
#include "proto.h"

//TODO implement

namespace kvmd::avr
{
    class UsbMouseRelative : public UsbMouse
    {
    public:
        uint8_t getType() { return PROTO::OUTPUTS1::MOUSE::USB_REL; }
    };

}
