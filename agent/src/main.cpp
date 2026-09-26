// Benchlog ESP32 serial agent.
//
// The laptop sends one line: "<id> <COMMAND> [args]". The agent replies with
// exactly one line of compact JSON that always includes "id" and "ok". Apart
// from the boot line it never prints anything on its own.
//
// INVARIANT: this firmware never configures any pin as OUTPUT. It only senses.

#include <Arduino.h>
#include <Wire.h>
#include <stdlib.h>
#include <string.h>

static const char *AGENT_VERSION = "0.1.0";
static const size_t MAX_LINE = 128;

// Pins the agent may probe (ESP32-WROOM DevKit).
// Never touch: 0, 2, 5, 12, 15 (strapping pins, affect boot),
//              6-11 (wired to the SPI flash), 1, 3 (UART0 to the laptop).
// 34-39 are input-only and have no internal pull-ups/downs, so they can't be probed.
// WROVER boards use 16 and 17 for PSRAM: remove them from this list there.
static const uint8_t SAFE_PINS[] = {4, 13, 14, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33};
static const size_t SAFE_PIN_COUNT = sizeof(SAFE_PINS) / sizeof(SAFE_PINS[0]);

static char lineBuf[MAX_LINE + 1];
static size_t lineLen = 0;
static bool lineTooLong = false;

static void replyBadRequest() {
  Serial.println("{\"id\":null,\"ok\":false,\"error\":\"bad_request\"}");
}

static void replyError(long id, const char *error) {
  char out[96];
  snprintf(out, sizeof(out), "{\"id\":%ld,\"ok\":false,\"error\":\"%s\"}", id, error);
  Serial.println(out);
}

static void cmdPing(long id) {
  char out[96];
  snprintf(out, sizeof(out), "{\"id\":%ld,\"ok\":true,\"cmd\":\"PING\",\"uptime_ms\":%lu}",
           id, (unsigned long)millis());
  Serial.println(out);
}

static void cmdHello(long id) {
  char out[192];
  int n = snprintf(out, sizeof(out),
                   "{\"id\":%ld,\"ok\":true,\"cmd\":\"HELLO\",\"board\":\"esp32\","
                   "\"agent\":\"%s\",\"pins\":[",
                   id, AGENT_VERSION);
  for (size_t i = 0; i < SAFE_PIN_COUNT; i++) {
    n += snprintf(out + n, sizeof(out) - n, i == 0 ? "%u" : ",%u", SAFE_PINS[i]);
  }
  snprintf(out + n, sizeof(out) - n, "]}");
  Serial.println(out);
}

static bool isSafePin(long pin) {
  for (size_t i = 0; i < SAFE_PIN_COUNT; i++) {
    if (SAFE_PINS[i] == pin) {
      return true;
    }
  }
  return false;
}

// Reads the pin 3 times with the given internal pull. Returns how many reads were HIGH.
static int readWithPull(uint8_t pin, uint8_t mode) {
  pinMode(pin, mode);
  delay(5);  // let the pin settle
  int highs = 0;
  for (int i = 0; i < 3; i++) {
    highs += digitalRead(pin) == HIGH ? 1 : 0;
    delayMicroseconds(200);
  }
  return highs;
}

// Classifies what is electrically attached to a safe pin, then leaves it as a plain INPUT.
static const char *probePin(uint8_t pin) {
  int upHighs = readWithPull(pin, INPUT_PULLUP);
  int downHighs = readWithPull(pin, INPUT_PULLDOWN);
  pinMode(pin, INPUT);

  if (upHighs == 3 && downHighs == 0) return "floating";
  if (upHighs == 0 && downHighs == 0) return "pulled_low";
  if (upHighs == 3 && downHighs == 3) return "pulled_high";
  return "unstable";
}

// Parses a pin number token. Returns -1 if it is not a plain non-negative integer.
static long parsePin(const char *tok) {
  if (tok[0] < '0' || tok[0] > '9') return -1;
  char *end = NULL;
  long pin = strtol(tok, &end, 10);
  return *end == '\0' ? pin : -1;
}

static const size_t MAX_PROBE_PINS = 64;

// "PROBE" probes every safe pin; "PROBE 18 21 5" probes only the listed ones.
static void cmdProbe(long id, char *rest) {
  long pins[MAX_PROBE_PINS];
  size_t count = 0;

  char *pinTok = strtok_r(NULL, " \t", &rest);
  if (pinTok == NULL) {
    for (size_t i = 0; i < SAFE_PIN_COUNT; i++) {
      pins[count++] = SAFE_PINS[i];
    }
  } else {
    // Validate the whole list before touching any pin.
    for (; pinTok != NULL; pinTok = strtok_r(NULL, " \t", &rest)) {
      long pin = parsePin(pinTok);
      if (pin < 0 || count >= MAX_PROBE_PINS) {
        replyError(id, "bad_request");
        return;
      }
      pins[count++] = pin;
    }
  }

  char out[48];
  snprintf(out, sizeof(out), "{\"id\":%ld,\"ok\":true,\"cmd\":\"PROBE\",\"pins\":{", id);
  Serial.print(out);
  for (size_t i = 0; i < count; i++) {
    // Pins outside the safe list are never touched.
    const char *state = isSafePin(pins[i]) ? probePin((uint8_t)pins[i]) : "unsafe";
    snprintf(out, sizeof(out), i == 0 ? "\"%ld\":\"%s\"" : ",\"%ld\":\"%s\"", pins[i], state);
    Serial.print(out);
  }
  Serial.println("}}");
}

static const uint8_t I2C_SDA = 21;
static const uint8_t I2C_SCL = 22;

// Scans the I2C bus on SDA 21 / SCL 22. The I2C peripheral drives the lines open-drain
// (it only ever pulls them low), so this does not break the no-OUTPUT invariant.
static void cmdI2c(long id) {
  if (!Wire.begin(I2C_SDA, I2C_SCL)) {
    replyError(id, "i2c_init_failed");
    return;
  }

  char out[80];
  snprintf(out, sizeof(out), "{\"id\":%ld,\"ok\":true,\"cmd\":\"I2C\",\"sda\":%u,\"scl\":%u,\"devices\":[",
           id, I2C_SDA, I2C_SCL);
  Serial.print(out);
  bool first = true;
  for (uint8_t addr = 0x08; addr <= 0x77; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      snprintf(out, sizeof(out), first ? "\"0x%02x\"" : ",\"0x%02x\"", addr);
      Serial.print(out);
      first = false;
    }
  }
  Serial.println("]}");

  // Release the pins so they are plain inputs again.
  Wire.end();
  pinMode(I2C_SDA, INPUT);
  pinMode(I2C_SCL, INPUT);
}

// READ is allowed on safe pins plus the input-only pins 34-39.
static bool isReadablePin(long pin) {
  return isSafePin(pin) || (pin >= 34 && pin <= 39);
}

// ADC1 covers GPIO 32-39. ADC2 pins are left out because ADC2 is unreliable once WiFi is used.
static bool isAdc1Pin(long pin) {
  return pin >= 32 && pin <= 39;
}

static void cmdRead(long id, char *rest) {
  char *pinTok = strtok_r(NULL, " \t", &rest);
  if (pinTok == NULL || strtok_r(NULL, " \t", &rest) != NULL) {
    replyError(id, "bad_request");
    return;
  }
  long pin = parsePin(pinTok);
  if (pin < 0) {
    replyError(id, "bad_request");
    return;
  }
  if (!isReadablePin(pin)) {
    replyError(id, "unsafe_pin");
    return;
  }

  pinMode((uint8_t)pin, INPUT);
  int digital = digitalRead((uint8_t)pin);

  char analog[16];
  if (isAdc1Pin(pin)) {
    snprintf(analog, sizeof(analog), "%lu", (unsigned long)analogReadMilliVolts((uint8_t)pin));
  } else {
    snprintf(analog, sizeof(analog), "null");
  }

  char out[112];
  snprintf(out, sizeof(out), "{\"id\":%ld,\"ok\":true,\"cmd\":\"READ\",\"pin\":%ld,\"digital\":%d,\"analog_mv\":%s}",
           id, pin, digital, analog);
  Serial.println(out);
}

// Parses "<id> <COMMAND> [args]" and dispatches. `line` is modified in place.
static void handleLine(char *line) {
  char *rest = NULL;
  char *idTok = strtok_r(line, " \t", &rest);
  if (idTok == NULL) {
    return;  // whitespace-only line: ignore
  }

  // id must be a plain non-negative integer.
  char *end = NULL;
  long id = strtol(idTok, &end, 10);
  if (*idTok == '\0' || *end != '\0' || idTok[0] == '-' || idTok[0] == '+') {
    replyBadRequest();
    return;
  }

  char *cmd = strtok_r(NULL, " \t", &rest);
  if (cmd == NULL) {
    replyError(id, "bad_request");
    return;
  }

  if (strcmp(cmd, "PING") == 0) {
    cmdPing(id);
  } else if (strcmp(cmd, "HELLO") == 0) {
    cmdHello(id);
  } else if (strcmp(cmd, "PROBE") == 0) {
    cmdProbe(id, rest);
  } else if (strcmp(cmd, "I2C") == 0) {
    cmdI2c(id);
  } else if (strcmp(cmd, "READ") == 0) {
    cmdRead(id, rest);
  } else {
    replyError(id, "unknown_command");
  }
}

void setup() {
  Serial.begin(115200);
  delay(50);
  Serial.print("{\"ready\":true,\"agent\":\"");
  Serial.print(AGENT_VERSION);
  Serial.println("\"}");
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') {
      continue;  // accept both "\n" and "\r\n"
    }
    if (c == '\n') {
      if (lineTooLong) {
        replyBadRequest();
      } else if (lineLen > 0) {
        lineBuf[lineLen] = '\0';
        handleLine(lineBuf);
      }
      lineLen = 0;
      lineTooLong = false;
      continue;
    }
    if (lineLen < MAX_LINE) {
      lineBuf[lineLen++] = c;
    } else {
      lineTooLong = true;  // keep discarding until the newline
    }
  }
}
