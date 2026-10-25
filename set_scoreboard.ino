#include <LiquidCrystal.h>
#include <Servo.h>

// --- LCD pins (RS, E, D4, D5, D6, D7) ---
LiquidCrystal lcd(12, 11, 5, 4, 3, 2);

// --- Servo (flag) ---
Servo flagServo;
const int SERVO_PIN = 9;
const int FLAG_DOWN = 0;
const int FLAG_UP   = 110;

// --- LEDs + buzzer ---
const int RED  = 6;
const int BLUE = 7;
const int BUZ  = 8;

// --- State ---
float angle = FLAG_DOWN;
int target = FLAG_DOWN;
int scoreA = 0, scoreB = 0;
unsigned long holdUntil = 0;

char buf[32];
byte n = 0;

// ---------- LCD ----------
void showScore() {
  lcd.setCursor(0, 0);
  lcd.print("Team A: ");
  lcd.print(scoreA);
  lcd.print("     ");

  lcd.setCursor(0, 1);
  lcd.print("Team B: ");
  lcd.print(scoreB);
  lcd.print("     ");
}

// ---------- Serial line handler ----------
// Expected formats from Python:
//   "S,<scoreA>,<scoreB>,<team>"  team 0 = A, 1 = B
//   "R"                            reset scores
void handleLine() {
  if (buf[0] == 'S') {
    int a, b, t;
    if (sscanf(buf + 2, "%d,%d,%d", &a, &b, &t) == 3) {
      scoreA = a;
      scoreB = b;
      showScore();

      digitalWrite(t == 0 ? RED : BLUE, HIGH);
      tone(BUZ, 1000, 300);

      target = FLAG_UP;
      holdUntil = millis() + 2000;
    }
  } else if (buf[0] == 'R') {
    scoreA = 0;
    scoreB = 0;
    showScore();
  }
}

// ---------- Setup ----------
void setup() {
  Serial.begin(115200);

  pinMode(RED, OUTPUT);
  pinMode(BLUE, OUTPUT);
  pinMode(BUZ, OUTPUT);

  lcd.begin(16, 2);
  showScore();

  flagServo.attach(SERVO_PIN);
  flagServo.write(FLAG_DOWN);

  Serial.println("[tracker] ready");
}

// ---------- Loop ----------
void loop() {
  // Read serial one byte at a time
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      buf[n] = 0;
      if (n > 0) handleLine();
      n = 0;
    } else if (c != '\r' && n < sizeof(buf) - 1) {
      buf[n++] = c;
    }
  }

  // After a score, lower the flag and LEDs
  if (holdUntil && millis() > holdUntil) {
    digitalWrite(RED, LOW);
    digitalWrite(BLUE, LOW);
    target = FLAG_DOWN;
    holdUntil = 0;
  }

  // Smooth servo motion
  if (abs(target - angle) > 1) {
    angle += (target - angle) * 0.2;
    flagServo.write((int)angle);
  }

  delay(15);
}