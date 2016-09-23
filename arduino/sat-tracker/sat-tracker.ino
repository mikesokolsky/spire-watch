#include <SPI.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <gfxfont.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_HMC5883_U.h>
#include <Servo.h>

#define MAX_PACKET_LEN 24
#define HEADER '\n'
#define SAT_NAME '0'
#define TIME '1'
#define TRACKING '2'
#define MAG '3'
#define END '\0'
char buffer[MAX_PACKET_LEN];
char buffer_pos = 0;

#define MAX_STRING_LEN 22
char name[MAX_STRING_LEN];
char time[MAX_STRING_LEN];
//char debug[MAX_STRING_LEN];
unsigned char tracking;

Servo azimuth; //, elevation;

short mag_x, mag_y;

#define OLED_RESET 4
Adafruit_SSD1306 display(OLED_RESET);

Adafruit_HMC5883_Unified mag = Adafruit_HMC5883_Unified(12345);

void setup() {
  // put your setup code here, to run once:
  Serial.begin(57600);
  Serial.setTimeout(0);
  
  azimuth.attach(9);
  //elevation.attach(10);
  azimuth.write(0);
  //elevation.write(100);
  
  display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  display.clearDisplay();
  display.setTextColor(WHITE);
  display.setTextSize(0);
  display.setCursor(0,0);
  display.println("Loading...");
  display.display();
}

int check_for_packet() {
  while(Serial.available()) {
    buffer[buffer_pos] = Serial.read();
    buffer_pos++;
    switch(buffer_pos) {
      case 1:
        if(buffer[0] != HEADER) {
          buffer_pos = 0;
        }
        break;
      case 2:
        switch(buffer[1]) {
          case SAT_NAME:
          case TIME:
          case TRACKING:
          case MAG:
            break;
          default:
            buffer_pos = 0;
        }
        break;
      case MAX_PACKET_LEN + 1:
        buffer_pos = 0;
        break;
      default:
        if(buffer[buffer_pos - 1] == END) {
          buffer_pos = 0;
          return buffer[1];
        }
    }
  }
  return 0;
}

void update_display() {
  display.clearDisplay();
  display.setTextSize(2);
  display.setCursor(0,0);
  if(tracking) {
    display.println("Tracking");
  } else {
    display.println("Next Pass");
    display.setCursor(0,20);
    display.setTextSize(1);
    display.println(time);
  }
  display.setTextSize(1);
  display.setCursor(0,40);
  display.println(name);
  //display.setCursor(0,55);
  //display.println(debug);
  display.display();
}

void loop() {
  switch(check_for_packet()) {
    case SAT_NAME:
      strcpy(name, buffer + 2);
      update_display();
      Serial.print(HEADER);
      Serial.print(END);
      break;
    case TIME:
      strcpy(time, buffer + 2);
      update_display();
      Serial.print(HEADER);
      Serial.print(END);
      break;
    case TRACKING:
      if(buffer[2] == 'a' || buffer[2] == 'e') {
        tracking = 1;
        
        if(buffer[2] == 'a') {
          azimuth.writeMicroseconds(atoi(buffer + 3));
        } else {
          //elevation.writeMicroseconds(atoi(buffer + 3));
        }
      } else {
        azimuth.write(0);
        //elevation.write(90);
        tracking = 0;
      }
      update_display();
      Serial.print(HEADER);
      Serial.print(END);
      break;
    case MAG:
      Serial.print(HEADER);
      Serial.print(mag_x);
      Serial.print(',');
      Serial.print(mag_y);
      Serial.print(END);
      break;
    default:
      break;
  }
  sensors_event_t event;
  mag.getEvent(&event);
  mag_x = short(event.magnetic.x * 10);
  mag_y = short(event.magnetic.y * 10);
  //sprintf(debug, "%d", mag_x);
  //update_display();
}
