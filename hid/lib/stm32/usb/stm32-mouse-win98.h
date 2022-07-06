#pragma once

#include "stm32-mouse-absolute.h"

//TODO implement

namespace kvmd::stm32
{
    class UsbMouseAbsoluteWin98 : public UsbMouseAbsolute
    {
    public:
        uint8_t getType() override { return PROTO::OUTPUTS1::MOUSE::USB_WIN98; }
    };
}
