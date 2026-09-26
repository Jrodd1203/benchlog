"""Known parts and their pinouts. Used to place a part without listing every pin by hand."""

# DOIT ESP32 DevKit V1 (30 pins), antenna end up, USB end down, top to bottom.
ESP32_DEVKIT_V1_30_LEFT = [
    "EN", "GPIO36", "GPIO39", "GPIO34", "GPIO35", "GPIO32", "GPIO33", "GPIO25",
    "GPIO26", "GPIO27", "GPIO14", "GPIO12", "GND1", "GPIO13", "VIN",
]  # fmt: skip
ESP32_DEVKIT_V1_30_RIGHT = [
    "GPIO23", "GPIO22", "GPIO1", "GPIO3", "GPIO21", "GPIO19", "GPIO18", "GPIO5",
    "GPIO17", "GPIO16", "GPIO4", "GPIO2", "GPIO15", "GND2", "3V3",
]  # fmt: skip


def esp32_devkit_v1_30_pins(top_row: int, left_col: str = "B", right_col: str = "I") -> dict[str, str]:
    """Pin -> hole map for the DevKit straddling the center gap.

    The pin rows are 0.9" apart, so on a standard board they land in B and I,
    leaving only columns A and J free next to the module.
    """
    pins = {}
    for i, name in enumerate(ESP32_DEVKIT_V1_30_LEFT):
        pins[name] = f"{left_col}{top_row + i}"
    for i, name in enumerate(ESP32_DEVKIT_V1_30_RIGHT):
        pins[name] = f"{right_col}{top_row + i}"
    return pins
