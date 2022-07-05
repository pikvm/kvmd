#pragma once

#include <stdint.h>

namespace kvmd
{

    struct Keyboard
    {
        virtual void begin() {}
        /**
         * Release all keys
         */
        virtual void clear() {}
        virtual void sendKey(uint8_t code, bool state) {}
        virtual void periodic() {}
        virtual bool isOffline() { return false; }
        virtual uint8_t getLedsAs(uint8_t caps, uint8_t scroll, uint8_t num) { return 0; }
        virtual uint8_t getType() { return 0; }
    };

}