import os
import ssl
import json
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

# TEMPORARY input topic
INPUT_TOPIC = "greenpulse/sensors/data"

sensor_data = {
    "plant_id": "plant_001",
    "soil_moisture": 25,
    "temperature": 29,
    "humidity": 65,
    "sunlight": 350,
    "ph": 6.5,
    "air_quality": 50
}


client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id="greenpulse_sensor_simulator"
)

client.username_pw_set(
    MQTT_USERNAME,
    MQTT_PASSWORD
)

client.tls_set(
    cert_reqs=ssl.CERT_REQUIRED
)

print("Connecting to EMQX Cloud...")

client.connect(
    MQTT_BROKER,
    MQTT_PORT,
    60
)

client.loop_start()

payload = json.dumps(sensor_data)

info = client.publish(
    INPUT_TOPIC,
    payload,
    qos=1
)

info.wait_for_publish()

print("\n--- SENSOR DATA PUBLISHED ---")
print("Topic:", INPUT_TOPIC)
print("Payload:")
print(json.dumps(sensor_data, indent=2))
print("-----------------------------")

client.loop_stop()
client.disconnect()

print("\nSensor simulation completed.")