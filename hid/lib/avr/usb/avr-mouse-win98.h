#pragma once

#include "avr-mouse-absolute.h"

//TODO implement

namespace kvmd::avr
{
    class UsbMouseAbsoluteWin98 : public UsbMouseAbsolute
    {
    public:
        uint8_t getType() override { return PROTO::OUTPUTS1::MOUSE::USB_WIN98; }
    };
}
