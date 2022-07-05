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

        /**
         * Sends key
         * @param code ???
         * @param state true pressed, false released
         */
        virtual void sendKey(uint8_t code, bool state) {}

        virtual void periodic() {}

        /**
         * False if offline or unknown. Otherwise true..
         */
        virtual bool isOffline() { return false; }

        /**
         * Enabled leds @ref PROTO::PONG::CAPS, PROTO::PONG::SCROLL, PROTO::PONG::NUM;
         */
        virtual uint8_t getLedsAs() { return 0; }

        /**
         * Device type
         */
        virtual uint8_t getType() { return 0; }
    };

}