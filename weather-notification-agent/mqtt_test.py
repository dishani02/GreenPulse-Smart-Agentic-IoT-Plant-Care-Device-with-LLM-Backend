import json
import ssl
import time
import paho.mqtt.client as mqtt


MQTT_BROKER = "a1hi3ylrb6ft72-ats.iot.us-east-1.amazonaws.com"
MQTT_PORT = 8883

MQTT_CLIENT_ID = "GreenPulseNotificationAgent"

MQTT_TOPIC = "greenpulse/GreenPulse01/care"

CA_FILE = "AmazonRootCA1.pem"
CERT_FILE = "notification-agent-certificate.pem.crt"
PRIVATE_KEY_FILE = "notification-agent-private.pem.key"


def on_connect(client, userdata, flags, reason_code, properties):
    print("Connected to AWS IoT Core.")
    print("Connection result:", reason_code)


client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=MQTT_CLIENT_ID
)

client.on_connect = on_connect

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

client.loop_start()

time.sleep(2)

payload = {
    "device_id": "GreenPulse01",
    "summary": "GreenPulse Component 3 MQTT connection test.",
    "test": True
}

message = json.dumps(payload)

print("Publishing test message...")

result = client.publish(
    MQTT_TOPIC,
    message,
    qos=1
)

result.wait_for_publish()

print("Published to:")
print(MQTT_TOPIC)

print("Payload:")
print(message)

client.loop_stop()
client.disconnect()