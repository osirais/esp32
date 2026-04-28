#include <Arduino.h>
#include <Wire.h>
#include <math.h>

namespace
{
  constexpr uint8_t kMpu6050I2cAddress = 0x68;
  constexpr uint8_t kRegisterPowerManagement1 = 0x6B;
  constexpr uint8_t kRegisterGyroConfig = 0x1B;
  constexpr uint8_t kRegisterAccelConfig = 0x1C;
  constexpr uint8_t kRegisterWhoAmI = 0x75;
  constexpr uint8_t kRegisterAccelXoutHigh = 0x3B;

  constexpr int SDA_PIN = 21;
  constexpr int SCL_PIN = 22;

#ifdef LED_BUILTIN
  constexpr uint8_t kLedPin = LED_BUILTIN;
#else
  constexpr uint8_t kLedPin = 2;
#endif

  constexpr bool kLedActiveHigh = true;

  constexpr float ACCEL_SCALE = 16384.0f; // +/-2g
  constexpr float GYRO_SCALE = 131.0f;    // +/-250 dps

  constexpr float kComplementaryAlpha = 0.98f;
  constexpr uint16_t kCalibrationSamples = 600;
  constexpr uint32_t kPrintIntervalMs = 50;

  constexpr float kRollEnterDeg = 28.0f;
  constexpr float kRollExitDeg = 16.0f;
  constexpr float kPitchEnterDeg = 24.0f;
  constexpr float kPitchExitDeg = 12.0f;

  constexpr float kTwistAngleThresholdDeg = 28.0f;
  constexpr float kTwistRateThresholdDps = 100.0f;
  constexpr uint32_t kTwistCooldownMs = 700;
  constexpr uint32_t kTwistHoldMs = 450;
  constexpr float kNeutralResetRollPitchDeg = 8.0f;
  constexpr uint32_t kNeutralResetHoldMs = 350;

  struct RawImuData
  {
    int16_t ax;
    int16_t ay;
    int16_t az;
    int16_t gx;
    int16_t gy;
    int16_t gz;
  };

  struct Vector3f
  {
    float x;
    float y;
    float z;
  };

  struct OrientationState
  {
    float rollDeg;
    float pitchDeg;
    float yawDeg;
    float gyroXDps;
    float gyroYDps;
    float gyroZDps;
  };

  enum class HandAction : uint8_t
  {
    Neutral,
    RollPositive,
    RollNegative,
    PitchUp,
    PitchDown,
    TwistPositive,
    TwistNegative,
  };

  Vector3f gyroBiasRaw{0.0f, 0.0f, 0.0f};
  OrientationState orientation{0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f};

  HandAction persistentAction = HandAction::Neutral;
  HandAction transientAction = HandAction::Neutral;
  HandAction currentAction = HandAction::Neutral;

  bool orientationReady = false;
  float yawGestureAnchorDeg = 0.0f;

  uint32_t lastMicros = 0;
  uint32_t lastPrintMs = 0;
  uint32_t lastTwistMs = 0;
  uint32_t transientActionUntilMs = 0;
  uint32_t neutralPoseSinceMs = 0;

  void writeRegister(uint8_t reg, uint8_t value)
  {
    Wire.beginTransmission(kMpu6050I2cAddress);
    Wire.write(reg);
    Wire.write(value);
    Wire.endTransmission();
  }

  uint8_t readRegister(uint8_t reg)
  {
    Wire.beginTransmission(kMpu6050I2cAddress);
    Wire.write(reg);
    Wire.endTransmission(false);
    Wire.requestFrom(kMpu6050I2cAddress, static_cast<uint8_t>(1));
    return Wire.available() ? Wire.read() : 0;
  }

  bool readRawImu(RawImuData &out)
  {
    Wire.beginTransmission(kMpu6050I2cAddress);
    Wire.write(kRegisterAccelXoutHigh);
    if (Wire.endTransmission(false) != 0)
    {
      return false;
    }

    constexpr uint8_t bytesToRead = 14;
    if (Wire.requestFrom(kMpu6050I2cAddress, bytesToRead) != bytesToRead)
    {
      return false;
    }

    out.ax = (Wire.read() << 8) | Wire.read();
    out.ay = (Wire.read() << 8) | Wire.read();
    out.az = (Wire.read() << 8) | Wire.read();
    (void)Wire.read();
    (void)Wire.read();
    out.gx = (Wire.read() << 8) | Wire.read();
    out.gy = (Wire.read() << 8) | Wire.read();
    out.gz = (Wire.read() << 8) | Wire.read();
    return true;
  }

  void setupMpu6050()
  {
    writeRegister(kRegisterPowerManagement1, 0x00); // wake up
    writeRegister(kRegisterGyroConfig, 0x00);       // +/-250 dps
    writeRegister(kRegisterAccelConfig, 0x00);      // +/-2g
    delay(100);
  }

  float wrapAngleDeg(float angleDeg)
  {
    while (angleDeg > 180.0f)
    {
      angleDeg -= 360.0f;
    }

    while (angleDeg <= -180.0f)
    {
      angleDeg += 360.0f;
    }

    return angleDeg;
  }

  float angleDeltaDeg(float referenceDeg, float valueDeg)
  {
    return wrapAngleDeg(valueDeg - referenceDeg);
  }

  const char *actionToString(HandAction action)
  {
    switch (action)
    {
    case HandAction::Neutral:
      return "NEUTRAL";
    case HandAction::RollPositive:
      return "ROLL_POSITIVE";
    case HandAction::RollNegative:
      return "ROLL_NEGATIVE";
    case HandAction::PitchUp:
      return "PITCH_UP";
    case HandAction::PitchDown:
      return "PITCH_DOWN";
    case HandAction::TwistPositive:
      return "TWIST_POSITIVE";
    case HandAction::TwistNegative:
      return "TWIST_NEGATIVE";
    }

    return "UNKNOWN";
  }

  void writeLed(bool on)
  {
    const uint8_t level = (on == kLedActiveHigh) ? HIGH : LOW;
    digitalWrite(kLedPin, level);
  }

  void reportActionIfChanged(HandAction nextAction)
  {
    if (nextAction == currentAction)
    {
      return;
    }

    currentAction = nextAction;
    Serial.printf("Action: %s\n", actionToString(currentAction));
  }

  bool calibrateGyro()
  {
    Serial.println("Keep the sensor still for gyro calibration...");

    Vector3f gyroAccum{0.0f, 0.0f, 0.0f};
    Vector3f accelAccum{0.0f, 0.0f, 0.0f};
    uint16_t collected = 0;
    uint16_t attempts = 0;
    RawImuData raw{};

    while (collected < kCalibrationSamples && attempts < (kCalibrationSamples * 6U))
    {
      ++attempts;

      if (!readRawImu(raw))
      {
        delay(2);
        continue;
      }

      gyroAccum.x += static_cast<float>(raw.gx);
      gyroAccum.y += static_cast<float>(raw.gy);
      gyroAccum.z += static_cast<float>(raw.gz);

      accelAccum.x += static_cast<float>(raw.ax);
      accelAccum.y += static_cast<float>(raw.ay);
      accelAccum.z += static_cast<float>(raw.az);

      ++collected;
      delay(2);
    }

    if (collected != kCalibrationSamples)
    {
      return false;
    }

    gyroBiasRaw.x = gyroAccum.x / static_cast<float>(collected);
    gyroBiasRaw.y = gyroAccum.y / static_cast<float>(collected);
    gyroBiasRaw.z = gyroAccum.z / static_cast<float>(collected);

    const float ax = (accelAccum.x / static_cast<float>(collected)) / ACCEL_SCALE;
    const float ay = (accelAccum.y / static_cast<float>(collected)) / ACCEL_SCALE;
    const float az = (accelAccum.z / static_cast<float>(collected)) / ACCEL_SCALE;

    orientation.rollDeg = atan2f(ay, az) * RAD_TO_DEG;
    orientation.pitchDeg = atan2f(-ax, sqrtf((ay * ay) + (az * az))) * RAD_TO_DEG;
    orientation.yawDeg = 0.0f;
    orientation.gyroXDps = 0.0f;
    orientation.gyroYDps = 0.0f;
    orientation.gyroZDps = 0.0f;
    orientationReady = true;

    yawGestureAnchorDeg = 0.0f;
    neutralPoseSinceMs = millis();

    Serial.printf("Gyro bias raw counts: X=%0.2f Y=%0.2f Z=%0.2f\n",
                  gyroBiasRaw.x, gyroBiasRaw.y, gyroBiasRaw.z);
    return true;
  }

  void updateOrientation(const RawImuData &raw, float dt)
  {
    const float ax = static_cast<float>(raw.ax) / ACCEL_SCALE;
    const float ay = static_cast<float>(raw.ay) / ACCEL_SCALE;
    const float az = static_cast<float>(raw.az) / ACCEL_SCALE;

    const float gx = (static_cast<float>(raw.gx) - gyroBiasRaw.x) / GYRO_SCALE;
    const float gy = (static_cast<float>(raw.gy) - gyroBiasRaw.y) / GYRO_SCALE;
    const float gz = (static_cast<float>(raw.gz) - gyroBiasRaw.z) / GYRO_SCALE;

    const float accelRollDeg = atan2f(ay, az) * RAD_TO_DEG;
    const float accelPitchDeg = atan2f(-ax, sqrtf((ay * ay) + (az * az))) * RAD_TO_DEG;

    if (!orientationReady)
    {
      orientation.rollDeg = accelRollDeg;
      orientation.pitchDeg = accelPitchDeg;
      orientation.yawDeg = 0.0f;
      orientationReady = true;
    }

    orientation.rollDeg = wrapAngleDeg(
        (kComplementaryAlpha * (orientation.rollDeg + (gx * dt))) +
        ((1.0f - kComplementaryAlpha) * accelRollDeg));
    orientation.pitchDeg = wrapAngleDeg(
        (kComplementaryAlpha * (orientation.pitchDeg + (gy * dt))) +
        ((1.0f - kComplementaryAlpha) * accelPitchDeg));
    orientation.yawDeg = wrapAngleDeg(orientation.yawDeg + (gz * dt));

    orientation.gyroXDps = gx;
    orientation.gyroYDps = gy;
    orientation.gyroZDps = gz;
  }

  HandAction classifyPersistentAction()
  {
    switch (persistentAction)
    {
    case HandAction::RollPositive:
      if (orientation.pitchDeg < -kPitchExitDeg)
      {
        return HandAction::RollPositive;
      }
      break;
    case HandAction::RollNegative:
      if (orientation.pitchDeg > kPitchExitDeg)
      {
        return HandAction::RollNegative;
      }
      break;
    case HandAction::PitchUp:
      if (orientation.rollDeg < -kRollExitDeg)
      {
        return HandAction::PitchUp;
      }
      break;
    case HandAction::PitchDown:
      if (orientation.rollDeg > kRollExitDeg)
      {
        return HandAction::PitchDown;
      }
      break;
    default:
      break;
    }

    if (orientation.rollDeg > kRollEnterDeg &&
        fabsf(orientation.rollDeg) >= fabsf(orientation.pitchDeg))
    {
      return HandAction::PitchDown;
    }

    if (orientation.rollDeg < -kRollEnterDeg &&
        fabsf(orientation.rollDeg) >= fabsf(orientation.pitchDeg))
    {
      return HandAction::PitchUp;
    }

    if (orientation.pitchDeg > kPitchEnterDeg)
    {
      return HandAction::RollNegative;
    }

    if (orientation.pitchDeg < -kPitchEnterDeg)
    {
      return HandAction::RollPositive;
    }

    return HandAction::Neutral;
  }

  void triggerTwist(HandAction action, uint32_t nowMs)
  {
    transientAction = action;
    transientActionUntilMs = nowMs + kTwistHoldMs;
    lastTwistMs = nowMs;
    yawGestureAnchorDeg = orientation.yawDeg;
  }

  void updateYawGestureAnchor(uint32_t nowMs)
  {
    const bool inNeutralPose =
        fabsf(orientation.rollDeg) < kNeutralResetRollPitchDeg &&
        fabsf(orientation.pitchDeg) < kNeutralResetRollPitchDeg;

    if (!inNeutralPose)
    {
      neutralPoseSinceMs = 0;
      return;
    }

    if (neutralPoseSinceMs == 0)
    {
      neutralPoseSinceMs = nowMs;
      return;
    }

    if ((nowMs - neutralPoseSinceMs) >= kNeutralResetHoldMs)
    {
      yawGestureAnchorDeg = orientation.yawDeg;
    }
  }

  void updateActionState(uint32_t nowMs)
  {
    persistentAction = classifyPersistentAction();
    updateYawGestureAnchor(nowMs);

    if ((nowMs - lastTwistMs) >= kTwistCooldownMs)
    {
      const float yawOffsetDeg = angleDeltaDeg(yawGestureAnchorDeg, orientation.yawDeg);

      if (yawOffsetDeg >= kTwistAngleThresholdDeg &&
          orientation.gyroZDps >= kTwistRateThresholdDps)
      {
        triggerTwist(HandAction::TwistPositive, nowMs);
      }
      else if (yawOffsetDeg <= -kTwistAngleThresholdDeg &&
               orientation.gyroZDps <= -kTwistRateThresholdDps)
      {
        triggerTwist(HandAction::TwistNegative, nowMs);
      }
    }

    if (nowMs >= transientActionUntilMs)
    {
      transientAction = HandAction::Neutral;
    }

    const HandAction nextAction =
        (transientAction != HandAction::Neutral) ? transientAction : persistentAction;
    reportActionIfChanged(nextAction);
  }

  void updateLedForAction(uint32_t nowMs)
  {
    bool ledOn = false;

    switch (currentAction)
    {
    case HandAction::Neutral:
      ledOn = false;
      break;
    case HandAction::RollPositive:
      ledOn = ((nowMs / 180U) % 2U) == 0U;
      break;
    case HandAction::RollNegative:
      ledOn = ((nowMs / 700U) % 2U) == 0U;
      break;
    case HandAction::PitchUp:
      ledOn = true;
      break;
    case HandAction::PitchDown:
    {
      const uint32_t phaseMs = nowMs % 1100U;
      ledOn = phaseMs < 80U || (phaseMs >= 160U && phaseMs < 240U);
      break;
    }
    case HandAction::TwistPositive:
    {
      const uint32_t phaseMs = nowMs % 200U;
      ledOn = phaseMs < 60U;
      break;
    }
    case HandAction::TwistNegative:
    {
      const uint32_t phaseMs = nowMs % 300U;
      ledOn = phaseMs < 50U ||
              (phaseMs >= 100U && phaseMs < 150U) ||
              (phaseMs >= 200U && phaseMs < 250U);
      break;
    }
    }

    writeLed(ledOn);
  }
} // namespace

void setup()
{
  pinMode(kLedPin, OUTPUT);
  writeLed(false);

  Serial.begin(115200);
  delay(300);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);

  setupMpu6050();

  const uint8_t whoAmI = readRegister(kRegisterWhoAmI);
  if (whoAmI != 0x68)
  {
    Serial.printf("MPU6050 not detected. WHO_AM_I=0x%02X\n", whoAmI);
    while (true)
    {
      delay(1000);
    }
  }

  Serial.println("MPU6050 detected");

  if (!calibrateGyro())
  {
    Serial.println("Calibration failed");
    while (true)
    {
      delay(1000);
    }
  }

  Serial.println("Live motion control is ready");
  Serial.println("Rotate your hand to change the action and LED pattern");
  Serial.printf("Action: %s\n", actionToString(currentAction));

  lastMicros = micros();
}

void loop()
{
  RawImuData raw{};
  if (!readRawImu(raw))
  {
    Serial.println("read failed");
    delay(10);
    return;
  }

  const uint32_t nowUs = micros();
  float dt = (nowUs - lastMicros) / 1000000.0f;
  lastMicros = nowUs;

  if (dt < 0.0f || dt > 0.2f)
  {
    dt = 0.0f;
  }

  updateOrientation(raw, dt);

  const uint32_t nowMs = millis();
  updateActionState(nowMs);
  updateLedForAction(nowMs);

  if (nowMs - lastPrintMs >= kPrintIntervalMs)
  {
    lastPrintMs = nowMs;
    Serial.printf("Roll: %7.2f deg | Pitch: %7.2f deg | Yaw: %7.2f deg\n",
                  orientation.rollDeg, orientation.pitchDeg, orientation.yawDeg);
  }
}
