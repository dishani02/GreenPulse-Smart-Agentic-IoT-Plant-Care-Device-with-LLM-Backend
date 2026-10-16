import json
import ssl
import paho.mqtt.client as mqtt

MQTT_BROKER = "a1hi3ylrb6ft72-ats.iot.us-east-1.amazonaws.com"
MQTT_PORT = 8883
MQTT_CLIENT_ID = "GreenPulseNotificationAgent"

TELEMETRY_TOPIC = "greenpulse/GreenPulse01/telemetry"

CA_FILE = "AmazonRootCA1.pem"
CERT_FILE = "notification-agent-certificate.pem.crt"
PRIVATE_KEY_FILE = "notification-agent-private.pem.key"


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print("Connected to AWS IoT Core.")
        print("Subscribing to:")
        print(TELEMETRY_TOPIC)

        client.subscribe(
            TELEMETRY_TOPIC,
            qos=1
        )

        print("\nWaiting for ESP32 sensor data...\n")
    else:
        print("Connection failed:", reason_code)


def on_message(client, userdata, message):
    try:
        payload = message.payload.decode("utf-8")
        sensor_data = json.loads(payload)

        print("==============================")
        print("SENSOR DATA RECEIVED")
        print("==============================")

        print("Device:", sensor_data.get("device_id"))
        print("Soil Raw:", sensor_data.get("soil_raw"))
        print("Soil Moisture:", sensor_data.get("soil_moisture"), "%")
        print("Temperature:", sensor_data.get("temperature"), "C")
        print("Humidity:", sensor_data.get("humidity"), "%")
        print("Air Raw:", sensor_data.get("air_raw"))
        print("Light:", sensor_data.get("light"))
        print("Plant Status:", sensor_data.get("plant_status"))

        print("==============================\n")

    except Exception as error:
        print("Could not process telemetry:", error)


client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=MQTT_CLIENT_ID
)

client.on_connect = on_connect
client.on_message = on_message

client.tls_set(
    ca_certs=CA_FILE,
    certfile=CERT_FILE,
    keyfile=PRIVATE_KEY_FILE,
    tls_version=ssl.PROTOCOL_TLS_CLIENT
)

print("Connecting to AWS IoT Core...")

client.connect(
    MQTT_BROKER,
    MQTT_PORT,
    keepalive=60
)

client.loop_forever()