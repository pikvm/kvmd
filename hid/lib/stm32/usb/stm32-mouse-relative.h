#pragma once

#include "stm32-mouse-absolute.h"

//TODO implement

namespace kvmd::stm32
{
    class UsbMouseRelative : public UsbMouse
    {
    public:
        uint8_t getType() { return PROTO::OUTPUTS1::MOUSE::USB_REL; }
    };

}
