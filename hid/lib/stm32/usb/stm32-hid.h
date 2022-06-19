#pragma once

class UsbKeyboard
{
public:
    UsbKeyboard();

    void begin();

    void periodic();

    void clear();

    void sendKey(uint8_t code, bool state);

    uint8_t getOfflineAs(uint8_t offline);

    uint8_t getLedsAs(uint8_t caps, uint8_t scroll, uint8_t num);
};

class UsbMouseAbsolute
{
public:
    UsbMouseAbsolute();

    void begin(bool win98_fix);

    bool isWin98FixEnabled();

    void clear();

    void sendMove(int x, int y);

    void sendWheel(int delta_y);

    uint8_t getOfflineAs(uint8_t offline);

    void sendButtons(
        bool left_select, bool left_state,
        bool right_select, bool right_state,
        bool middle_select, bool middle_state,
        bool up_select, bool up_state,
        bool down_select, bool down_state);
};

class UsbMouseRelative
{
public:
    UsbMouseRelative();

    void begin();

    void clear();

    void sendRelative(int x, int y);

    void sendWheel(int delta_y);

    uint8_t getOfflineAs(uint8_t offline);

    void sendButtons(
        bool left_select, bool left_state,
        bool right_select, bool right_state,
        bool middle_select, bool middle_state,
        bool up_select, bool up_state,
        bool down_select, bool down_state);
};