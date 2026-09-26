// benchlog ESP32 continuous pin-state reporter.
// Emits one JSON line per INTERVAL_MS over Serial at 115200 baud.
// Format: {"t":<millis>,"d":{<pin>:<0|1>,...},"a":{<pin>:<0-4095>,...}}
// "d" = digital reads (all safe GPIOs), "a" = ADC-capable pins only.

#define INTERVAL_MS 100

// Digital-readable GPIOs on DOIT DevKit V1 (skipping strapping/flash pins).
const int D_PINS[] = {4, 5, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33};
const int D_COUNT  = sizeof(D_PINS) / sizeof(D_PINS[0]);

// ADC-capable input-only pins (safe to analogRead without pullup).
const int A_PINS[] = {34, 35, 36, 39};
const int A_COUNT  = sizeof(A_PINS) / sizeof(A_PINS[0]);

void setup() {
  Serial.begin(115200);
  for (int i = 0; i < D_COUNT; i++) {
    // INPUT_PULLDOWN so unconnected pins read LOW (stable, not floating).
    pinMode(D_PINS[i], INPUT_PULLDOWN);
  }
}

void loop() {
  static unsigned long last = 0;
  unsigned long now = millis();
  if (now - last < INTERVAL_MS) return;
  last = now;

  Serial.print("{\"t\":");
  Serial.print(now);

  // Digital states
  Serial.print(",\"d\":{");
  for (int i = 0; i < D_COUNT; i++) {
    Serial.print("\"");
    Serial.print(D_PINS[i]);
    Serial.print("\":");
    Serial.print(digitalRead(D_PINS[i]));
    if (i < D_COUNT - 1) Serial.print(",");
  }
  Serial.print("}");

  // ADC states
  Serial.print(",\"a\":{");
  for (int i = 0; i < A_COUNT; i++) {
    Serial.print("\"");
    Serial.print(A_PINS[i]);
    Serial.print("\":");
    Serial.print(analogRead(A_PINS[i]));
    if (i < A_COUNT - 1) Serial.print(",");
  }
  Serial.print("}}");
  Serial.print("\n");
}
