// Murmuration — UNO Q MCU sketch: job progress on the 8x13 LED matrix.
//
// Linux side (adapters/unoq/matrix.py) calls  show(job, pct, speed_milli)  ~every 0.3 s
// through the arduino-router.  job < 0 means "idle".
//
// Display:  top 5 rows = number (job id for 1.5 s when a job starts, then the %),
//           bottom 2 rows = progress bar (13 columns).
// Failsafe (SPEC §10): if no show() arrives for > 1 s, keep advancing the bar at the
// last speed until 100 % — the "rescue" carries on even if Linux/Python dies.
//
// Upload once with Arduino App Lab (or Arduino IDE), board: Arduino UNO Q.

#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>

Arduino_LED_Matrix matrix;

const int ROWS = 8;
const int COLS = 13;
const uint8_t ON = 255;
const uint8_t DIM = 25;
const unsigned long SHOW_JOB_MS = 1500;
const unsigned long FAILSAFE_MS = 1000;

uint8_t frame[ROWS * COLS];   // one byte per pixel, row-major (8 rows x 13 cols)

// 3x5 digits; each row is 3 bits, 4 = left column, 1 = right column
const uint8_t FONT[10][5] = {
  {7, 5, 5, 5, 7}, {2, 6, 2, 2, 7}, {7, 1, 7, 4, 7}, {7, 1, 7, 1, 7}, {5, 5, 7, 1, 1},
  {7, 4, 7, 1, 7}, {7, 4, 7, 5, 7}, {7, 1, 1, 1, 1}, {7, 5, 7, 5, 7}, {7, 5, 7, 1, 7},
};

// ---- state (written by show(), read by loop()) --------------------------------
volatile bool active = false;
volatile int job = -1;
float pct = 0.0;               // 0..100
int speedMilli = 0;            // progress per second * 1000  (0.12 -> 120)
unsigned long lastShow = 0;
unsigned long jobStartedAt = 0;
unsigned long lastTick = 0;

// ---- drawing (pure: frame only) ------------------------------------------------
void setPixel(int r, int c, uint8_t v) {
  if (r >= 0 && r < ROWS && c >= 0 && c < COLS) frame[r * COLS + c] = v;
}

void drawDigit(int d, int col) {
  for (int r = 0; r < 5; r++)
    for (int c = 0; c < 3; c++)
      if (FONT[d][r] & (4 >> c)) setPixel(r, col + c, ON);
}

void drawNumber(int n) {               // centred, up to 3 digits
  if (n < 0) n = 0;
  if (n > 999) n = 999;
  int digits[3];
  int count = 0;
  do { digits[count++] = n % 10; n /= 10; } while (n > 0 && count < 3);
  int width = count * 4 - 1;
  int col = (COLS - width) / 2;
  for (int i = count - 1; i >= 0; i--) { drawDigit(digits[i], col); col += 4; }
}

void drawBar(float percent) {          // rows 6-7
  int filled = (int)(percent * COLS / 100.0 + 0.5);
  for (int c = 0; c < COLS; c++) {
    uint8_t v = c < filled ? ON : DIM;
    setPixel(6, c, v);
    setPixel(7, c, v);
  }
}

void buildFrame(bool isActive, int jobId, float percent, bool showJobId) {
  for (int i = 0; i < ROWS * COLS; i++) frame[i] = 0;
  if (!isActive) {                      // idle: one dim line
    for (int c = 0; c < COLS; c++) setPixel(7, c, DIM);
    return;
  }
  drawNumber(showJobId ? jobId : (int)percent);
  if (showJobId) setPixel(0, 0, ON);    // corner dot = "this is the job number"
  drawBar(percent);
}

// ---- RPC from Linux ---------------------------------------------------------------
void show(int j, int p, int sp) {
  unsigned long now = millis();
  if (j < 0) {
    active = false;
    job = -1;
  } else {
    if (j != job || !active) jobStartedAt = now;
    job = j;
    pct = p;
    speedMilli = sp;
    active = true;
  }
  lastShow = now;
}

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(8);
  matrix.clear();
  // boot test pattern: full bar for 1 s (proves the matrix + orientation)
  buildFrame(true, 0, 100, false);
  matrix.draw(frame);
  delay(1000);
  Bridge.begin();
  Bridge.provide_safe("show", show);
  lastTick = millis();
  lastShow = lastTick;
}

void loop() {
  unsigned long now = millis();
  unsigned long dt = now - lastTick;
  lastTick = now;
  // failsafe: Linux went quiet -> keep going at the last known speed
  if (active && now - lastShow > FAILSAFE_MS && pct < 100.0) {
    pct += speedMilli * dt / 10000.0;   // speedMilli/1000 progress/s = /10 %/s
    if (pct > 100.0) pct = 100.0;
  }
  buildFrame(active, job, pct, now - jobStartedAt < SHOW_JOB_MS);
  matrix.draw(frame);
  delay(30);
}
