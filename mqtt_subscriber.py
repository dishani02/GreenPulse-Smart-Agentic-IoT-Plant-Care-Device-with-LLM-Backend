
import os
import ssl
import csv
import json
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# Load settings from .env
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

AWS_ENDPOINT = os.getenv("AWS_IOT_ENDPOINT")
AWS_PORT = int(os.getenv("AWS_IOT_PORT", "8883"))

CA_FILE = os.getenv("AWS_IOT_CA_FILE", "AmazonRootCA1.pem")
CERT_FILE = os.getenv("AWS_IOT_CERT_FILE")
KEY_FILE = os.getenv("AWS_IOT_KEY_FILE")

INPUT_TOPIC = os.getenv(
    "AWS_IOT_INPUT_TOPIC",
    "greenpulse/GreenPulse01/telemetry"
)
OUTPUT_TOPIC = os.getenv(
    "OUTPUT_TOPIC",
    "greenpulse/component2/care-tip"
)

# Resolve certificate paths relative to project folder
def project_path(path):
    if not path:
        raise ValueError("A certificate path is missing in .env")
    if os.path.isabs(path):
        return path
    return os.path.join(BASE_DIR, path)

CA_FILE = project_path(CA_FILE)
CERT_FILE = project_path(CERT_FILE)
KEY_FILE = project_path(KEY_FILE)

# CSV file for real incoming messages
DATA_DIR = os.path.join(BASE_DIR, "accuracy_evaluation")
os.makedirs(DATA_DIR, exist_ok=True)

CSV_FILE = os.path.join(DATA_DIR, "live_evaluation_data.csv")

CSV_FIELDS = ["received_at_utc", "topic", "payload_json"]


def prepare_csv():
    if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0:
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
            writer.writeheader()


def on_connect(client, userdata, flags, reason_code, properties):
    print("AWS IoT connection result:", reason_code)

    if reason_code == 0:
        print("Connected to AWS IoT Core.")
        print("Subscribing to live sensor and care-tip topics...")

        client.subscribe([
            (INPUT_TOPIC, 1),
            (OUTPUT_TOPIC, 1)
        ])

        print("Sensor topic:", INPUT_TOPIC)
        print("Care-tip topic:", OUTPUT_TOPIC)
        print("Saving messages to:", CSV_FILE)
        print("Keep this terminal open while collecting data.")


def on_message(client, userdata, message):
    received_at = datetime.now(timezone.utc).isoformat()
    payload_text = message.payload.decode("utf-8", errors="replace")

    print("\n--- LIVE MQTT MESSAGE ---")
    print("Time (UTC):", received_at)
    print("Topic:", message.topic)
    print("Payload:", payload_text)

    try:
        payload = json.loads(payload_text)
        payload_json = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
    except json.JSONDecodeError:
        payload_json = json.dumps(
            {"raw_message": payload_text}, ensure_ascii=False
        )

    with open(CSV_FILE, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writerow({
            "received_at_utc": received_at,
            "topic": message.topic,
            "payload_json": payload_json
        })

    print("Saved to CSV.")
    print("-------------------------")


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    print("Disconnected from AWS IoT. Reason:", reason_code)


prepare_csv()

client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id="GreenPulse_Component2_Evaluation_PC"
)

client.tls_set(
    ca_certs=CA_FILE,
    certfile=CERT_FILE,
    keyfile=KEY_FILE,
    cert_reqs=ssl.CERT_REQUIRED,
    tls_version=ssl.PROTOCOL_TLSv1_2
)

client.on_connect = on_connect
client.on_message = on_message
client.on_disconnect = on_disconnect

print("Connecting to AWS IoT Core...")

client.connect(AWS_ENDPOINT, AWS_PORT, keepalive=60)
client.loop_forever()
