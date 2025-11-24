#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ArduinoJson.h>

const char* ssid = "TP-Link";
const char* password = "12345679";

ESP8266WebServer server(80);

// Пины
const int relayPin = 5;     // D1
const int sensorPin = A0;

// Состояние реле
const int RELAY_ON = LOW;
const int RELAY_OFF = HIGH;

// Калибровка датчика
const int AIR_VALUE = 700;
const int VERY_DRY_THRESHOLD = 530;
const int DRY_THRESHOLD = 430;
const int NORMAL_MAX = 350;
const int IDEAL_MAX = 330;
const int IDEAL_MIN = 320;
const int WET_THRESHOLD = 310;
const int TOO_WET = 305;

// Переменные состояния
bool autoMode = false;
unsigned long lastWateringTime = 0;
const unsigned long MIN_WATERING_INTERVAL = 24 * 60 * 60 * 1000;

void setup() {
  Serial.begin(115200);
  
  // Настройка пинов
  pinMode(LED_BUILTIN, OUTPUT);
  pinMode(relayPin, OUTPUT);
  digitalWrite(relayPin, RELAY_OFF);
  digitalWrite(LED_BUILTIN, HIGH);
  
  // Подключение к Wi-Fi
  WiFi.begin(ssid, password);
  Serial.print("Подключение к Wi-Fi");
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(1000);
    Serial.print(".");
    attempts++;
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN)); // Мигаем LED
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✅ Подключено к Wi-Fi!");
    Serial.print("📡 IP адрес: ");
    Serial.println(WiFi.localIP());
    digitalWrite(LED_BUILTIN, HIGH); // Постоянный свет при подключении
  } else {
    Serial.println("\n❌ Ошибка подключения к Wi-Fi");
    digitalWrite(LED_BUILTIN, LOW); // Выключаем LED при ошибке
  }
  
  // Настройка HTTP маршрутов
  server.on("/", handleRoot);
  server.on("/status", handleStatus);
  server.on("/water", handleWater);
  server.on("/force_water", handleForceWater);
  server.on("/reset", handleReset);
  server.on("/auto", handleAutoMode);
  
  server.begin();
  Serial.println("🌐 HTTP сервер запущен");
}

void loop() {
  server.handleClient();
  
  if (autoMode) {
    checkAutoWatering();
  }
  
  delay(1000);
}

void handleRoot() {
  String html = "<html><head><meta charset='UTF-8'><title>Умный полив</title></head><body>";
  html += "<h1>🌿 Система умного полива</h1>";
  html += "<p><strong>IP:</strong> " + WiFi.localIP().toString() + "</p>";
  html += "<p><a href='/status'>📊 Статус</a></p>";
  html += "<p><a href='/water?duration=3'>💦 Полить 3 сек</a></p>";
  html += "<p><a href='/force_water'>🔧 Принудительный полив</a></p>";
  html += "</body></html>";
  
  server.send(200, "text/html; charset=UTF-8", html);
}

void handleStatus() {
  int moisture = readMoisture();
  String status = getMoistureStatus(moisture);
  
  DynamicJsonDocument doc(512);
  doc["moisture"] = moisture;
  doc["status"] = status;
  doc["auto_mode"] = autoMode;
  doc["ip"] = WiFi.localIP().toString();
  doc["rssi"] = WiFi.RSSI();
  
  if (lastWateringTime > 0) {
    doc["last_watering"] = lastWateringTime;
    doc["seconds_since_last"] = (millis() - lastWateringTime) / 1000;
  } else {
    doc["last_watering"] = "never";
  }
  
  String response;
  serializeJson(doc, response);
  
  server.send(200, "application/json", response);
}

void handleWater() {
  if (server.hasArg("duration")) {
    int duration = server.arg("duration").toInt();
    
    // Проверки перед поливом
    int currentMoisture = readMoisture();
    if (currentMoisture < WET_THRESHOLD) {
      server.send(400, "application/json", "{\"error\":\"Слишком влажно\"}");
      return;
    }
    
    if (lastWateringTime > 0 && (millis() - lastWateringTime < MIN_WATERING_INTERVAL)) {
      server.send(400, "application/json", "{\"error\":\"Не прошло 24 часа\"}");
      return;
    }
    
    waterPlant(duration);
    server.send(200, "application/json", "{\"success\":\"Полив завершен\"}");
  } else {
    server.send(400, "application/json", "{\"error\":\"Не указана длительность\"}");
  }
}

void handleForceWater() {
  forceWaterPlant(3);
  server.send(200, "application/json", "{\"success\":\"Принудительный полив завершен\"}");
}

void handleReset() {
  lastWateringTime = 0;
  server.send(200, "application/json", "{\"success\":\"Время сброшено\"}");
}

void handleAutoMode() {
  if (server.hasArg("enable")) {
    autoMode = server.arg("enable").toInt();
    String mode = autoMode ? "включен" : "выключен";
    server.send(200, "application/json", "{\"success\":\"Авторежим " + mode + "\"}");
  } else {
    server.send(400, "application/json", "{\"error\":\"Не указан параметр enable\"}");
  }
}

// Существующие функции из вашего кода
int readMoisture() {
  int total = 0;
  for (int i = 0; i < 3; i++) {
    total += analogRead(sensorPin);
    delay(100);
  }
  return total / 3;
}

String getMoistureStatus(int moisture) {
  if (moisture >= VERY_DRY_THRESHOLD) return "VERY_DRY";
  if (moisture >= DRY_THRESHOLD) return "DRY";
  if (moisture >= NORMAL_MAX) return "NORMAL";
  if (moisture >= IDEAL_MIN) return "IDEAL";
  if (moisture >= WET_THRESHOLD) return "WET";
  if (moisture >= TOO_WET) return "TOO_WET";
  return "OVERWATERED";
}

void waterPlant(int duration) {
  Serial.println("💦 Запуск полива: " + String(duration) + " сек");
  
  // Толчковый запуск для помпы
  for (int i = 0; i < 2; i++) {
    digitalWrite(relayPin, RELAY_ON);
    delay(100);
    digitalWrite(relayPin, RELAY_OFF);
    delay(150);
  }
  
  delay(300);
  
  // Основной полив
  digitalWrite(relayPin, RELAY_ON);
  digitalWrite(LED_BUILTIN, LOW);
  
  for (int i = 0; i < duration; i++) {
    delay(1000);
  }
  
  digitalWrite(relayPin, RELAY_OFF);
  digitalWrite(LED_BUILTIN, HIGH);
  lastWateringTime = millis();
  
  Serial.println("✅ Полив завершен");
}

void forceWaterPlant(int duration) {
  Serial.println("🔧 Принудительный полив: " + String(duration) + " сек");
  
  // Толчковый запуск
  for (int i = 0; i < 3; i++) {
    digitalWrite(relayPin, RELAY_ON);
    delay(150);
    digitalWrite(relayPin, RELAY_OFF);
    delay(200);
  }
  
  delay(500);
  
  digitalWrite(relayPin, RELAY_ON);
  digitalWrite(LED_BUILTIN, LOW);
  
  for (int i = 0; i < duration; i++) {
    delay(1000);
  }
  
  digitalWrite(relayPin, RELAY_OFF);
  digitalWrite(LED_BUILTIN, HIGH);
  lastWateringTime = millis();
  
  Serial.println("✅ Принудительный полив завершен");
}

void checkAutoWatering() {
  int moisture = readMoisture();
  
  if (moisture > DRY_THRESHOLD) {
    if (lastWateringTime == 0 || (millis() - lastWateringTime > MIN_WATERING_INTERVAL)) {
      Serial.println("🤖 Автополив: запуск");
      waterPlant(3);
    }
  }
}