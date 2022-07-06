#pragma once

#include "usb/hid.h"
#include "proto.h"

//TODO implement

namespace kvmd::stm32
{
    class UsbMouseRelative : public UsbMouse
    {
    public:
        uint8_t getType() override { return PROTO::OUTPUTS1::MOUSE::USB_REL; }
    };

}
