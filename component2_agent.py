
import os
import ssl
import json
import time
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

import llm_service
from app import analyze_sensor_data


# ============================================================
# 1. ENVIRONMENT CONFIGURATION
# ============================================================

load_dotenv()

AWS_IOT_ENDPOINT = os.getenv("AWS_IOT_ENDPOINT")
AWS_IOT_PORT = int(os.getenv("AWS_IOT_PORT", "8883"))

AWS_IOT_CLIENT_ID = os.getenv(
    "AWS_IOT_CLIENT_ID",
    "GreenPulse_Component2_PC"
)

INPUT_TOPIC = os.getenv(
    "AWS_IOT_INPUT_TOPIC",
    "greenpulse/GreenPulse01/telemetry"
)

OUTPUT_TOPIC = os.getenv(
    "AWS_IOT_OUTPUT_TOPIC",
    os.getenv("OUTPUT_TOPIC", "greenpulse/component2/care-tip")
)

QUERY_TOPIC = os.getenv(
    "AWS_IOT_QUERY_TOPIC",
    "greenpulse/GreenPulse01/query"
)

QUERY_RESPONSE_TOPIC = os.getenv(
    "AWS_IOT_QUERY_RESPONSE_TOPIC",
    "greenpulse/component2/query-response"
)

CA_FILE = os.getenv("AWS_IOT_CA_FILE")
CERT_FILE = os.getenv("AWS_IOT_CERT_FILE")
KEY_FILE = os.getenv("AWS_IOT_KEY_FILE")

# Default cooldown reduced to 15 seconds.
# An existing value in .env takes priority over this default.
CARE_TIP_COOLDOWN_SECONDS = max(
    0.0,
    float(os.getenv("CARE_TIP_COOLDOWN_SECONDS", "15"))
)


# ============================================================
# 2. CONFIGURATION VALIDATION
# ============================================================

if not AWS_IOT_ENDPOINT:
    raise ValueError("AWS_IOT_ENDPOINT is missing from .env")

if not all([CA_FILE, CERT_FILE, KEY_FILE]):
    raise ValueError(
        "AWS CA, certificate and private-key paths must be configured."
    )

for path in (CA_FILE, CERT_FILE, KEY_FILE):
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"AWS certificate file not found: {path}"
        )

if not os.getenv("GROQ_API_KEY"):
    raise ValueError("GROQ_API_KEY is missing from .env")


# ============================================================
# 3. SHARED STATE
# ============================================================

state_lock = threading.Lock()

previous_sensor_data = None
latest_sensor_data = None

last_care_trigger_sensor_data = None
last_care_request_time = None
last_published_care_message = None

worker = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="GreenPulseAI"
)

IGNORED_FIELDS = {
    "timestamp",
    "time",
    "device_id",
    "message_id",
    "event_id",
}


# ============================================================
# 4. SENSOR DATA HELPERS
# ============================================================

def flatten_sensor_data(data, prefix=""):
    """Flatten nested sensor fields for change detection."""

    flattened = {}

    if not isinstance(data, dict):
        return flattened

    for key, value in data.items():
        key = str(key)
        path = f"{prefix}.{key}" if prefix else key

        if key.lower() in IGNORED_FIELDS:
            continue

        if isinstance(value, dict):
            flattened.update(flatten_sensor_data(value, path))
        else:
            flattened[path] = value

    return flattened


def normalise_value(value):
    """Treat numeric strings and numbers consistently."""

    if value is None:
        return None

    if isinstance(value, str):
        value = value.strip()

    try:
        return ("number", round(float(value), 4))
    except (TypeError, ValueError):
        return ("text", str(value).casefold())


def to_float(value):
    """Convert a sensor value to a number if possible."""

    if value is None or isinstance(value, bool):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_sensor_value(flat_data, field_names):
    """Find a sensor field, including inside nested objects."""

    wanted = {name.lower() for name in field_names}

    for path, value in flat_data.items():
        if path.split(".")[-1].lower() in wanted:
            return value

    return None


def detect_sensor_changes(current, previous):
    """Detect all changed fields, including minor fluctuations."""

    current_flat = flatten_sensor_data(current)

    if previous is None:
        return [
            {
                "sensor": key,
                "previous": None,
                "current": value,
                "change_type": "initial_reading",
            }
            for key, value in sorted(current_flat.items())
        ]

    previous_flat = flatten_sensor_data(previous)
    changes = []

    for field in sorted(set(current_flat) | set(previous_flat)):
        if field not in current_flat:
            changes.append({
                "sensor": field,
                "previous": previous_flat[field],
                "current": None,
                "change_type": "field_missing",
            })

        elif field not in previous_flat:
            changes.append({
                "sensor": field,
                "previous": None,
                "current": current_flat[field],
                "change_type": "new_field",
            })

        elif (
            normalise_value(current_flat[field])
            != normalise_value(previous_flat[field])
        ):
            changes.append({
                "sensor": field,
                "previous": previous_flat[field],
                "current": current_flat[field],
                "change_type": "value_changed",
            })

    return changes


# ============================================================
# 5. MEANINGFUL SENSOR CHANGE RULES
# ============================================================

def moisture_band(value):
    if value < 20:
        return "very_dry"
    if value < 30:
        return "low"
    if value <= 40:
        return "moderate"
    if value <= 70:
        return "normal"
    if value <= 80:
        return "high"
    return "very_wet"


def temperature_band(value):
    if value < 15:
        return "cold"
    if value < 20:
        return "cool"
    if value <= 30:
        return "moderate"
    if value <= 35:
        return "hot"
    return "very_hot"


def humidity_band(value):
    if value < 30:
        return "low"
    if value <= 40:
        return "moderately_low"
    if value <= 70:
        return "normal"
    if value <= 85:
        return "high"
    return "very_high"


def relative_change_is_significant(
    old_value,
    new_value,
    fraction,
    minimum
):
    """Check whether a numeric change exceeds the configured limit."""

    old_number = to_float(old_value)
    new_number = to_float(new_value)

    if old_number is None or new_number is None:
        return False

    threshold = max(minimum, abs(old_number) * fraction)

    return abs(new_number - old_number) >= threshold


def meaningful_sensor_changes(current, baseline):
    """
    Determine whether current readings justify an AI care-tip request.
    Minor changes are still logged separately.
    """

    current_flat = flatten_sensor_data(current)

    if baseline is None:
        return ["Initial sensor reading"]

    baseline_flat = flatten_sensor_data(baseline)
    reasons = []

    def assess_banded_sensor(
        label,
        aliases,
        band_function,
        delta_limit
    ):
        old_value = get_sensor_value(baseline_flat, aliases)
        new_value = get_sensor_value(current_flat, aliases)

        if old_value is None and new_value is None:
            return

        if (old_value is None) != (new_value is None):
            reasons.append(
                f"{label} field appeared or disappeared"
            )
            return

        old_number = to_float(old_value)
        new_number = to_float(new_value)

        if old_number is None or new_number is None:
            if normalise_value(old_value) != normalise_value(new_value):
                reasons.append(f"{label} value changed")
            return

        old_band = band_function(old_number)
        new_band = band_function(new_number)

        if old_band != new_band:
            reasons.append(
                f"{label} condition changed: "
                f"{old_band} -> {new_band}"
            )
        elif abs(new_number - old_number) >= delta_limit:
            reasons.append(
                f"{label} changed significantly: "
                f"{old_number} -> {new_number}"
            )

    assess_banded_sensor(
        "Soil moisture",
        ["soil_moisture"],
        moisture_band,
        5.0
    )

    assess_banded_sensor(
        "Temperature",
        ["temperature"],
        temperature_band,
        2.0
    )

    assess_banded_sensor(
        "Humidity",
        ["humidity"],
        humidity_band,
        5.0
    )

    for label, aliases in (
        ("Light", ["light"]),
        ("Sunlight level", ["sunlight_level"]),
    ):
        old_value = get_sensor_value(baseline_flat, aliases)
        new_value = get_sensor_value(current_flat, aliases)

        if old_value is None and new_value is None:
            continue

        if (old_value is None) != (new_value is None):
            reasons.append(
                f"{label} field appeared or disappeared"
            )

        elif normalise_value(old_value) != normalise_value(new_value):
            reasons.append(
                f"{label} changed: {old_value} -> {new_value}"
            )

    old_status = get_sensor_value(
        baseline_flat,
        ["plant_status"]
    )
    new_status = get_sensor_value(
        current_flat,
        ["plant_status"]
    )

    if (old_status is None) != (new_status is None):
        reasons.append(
            "Plant status field appeared or disappeared"
        )

    elif (
        old_status is not None
        and normalise_value(old_status) != normalise_value(new_status)
    ):
        reasons.append(
            f"Plant status changed: {old_status} -> {new_status}"
        )

    # Use raw soil only if percentage moisture is unavailable.
    old_moisture = get_sensor_value(
        baseline_flat,
        ["soil_moisture"]
    )
    new_moisture = get_sensor_value(
        current_flat,
        ["soil_moisture"]
    )

    if old_moisture is None and new_moisture is None:
        old_raw = get_sensor_value(
            baseline_flat,
            ["soil_raw"]
        )
        new_raw = get_sensor_value(
            current_flat,
            ["soil_raw"]
        )

        if (old_raw is None) != (new_raw is None):
            reasons.append(
                "Raw soil sensor field appeared or disappeared"
            )

        elif (
            old_raw is not None
            and new_raw is not None
            and relative_change_is_significant(
                old_raw,
                new_raw,
                0.10,
                100
            )
        ):
            reasons.append(
                "Raw soil sensor reading changed significantly"
            )

    # air_raw is logged but is not used to infer air quality.
    return reasons


def urgent_sensor_condition_transition(current, baseline):
    """
    Bypass cooldown only when a sensor enters a critical band.
    Repeated readings within the same critical band do not repeatedly
    bypass cooldown.
    """

    if baseline is None:
        return False

    current_flat = flatten_sensor_data(current)
    baseline_flat = flatten_sensor_data(baseline)

    checks = [
        (
            "soil_moisture",
            moisture_band,
            {"very_dry", "low"},
        ),
        (
            "temperature",
            temperature_band,
            {"cold", "very_hot"},
        ),
        (
            "humidity",
            humidity_band,
            {"low", "very_high"},
        ),
    ]

    for field, band_function, critical_bands in checks:
        old_value = get_sensor_value(baseline_flat, [field])
        new_value = get_sensor_value(current_flat, [field])

        old_number = to_float(old_value)
        new_number = to_float(new_value)

        if old_number is None or new_number is None:
            continue

        old_band = band_function(old_number)
        new_band = band_function(new_number)

        # Bypass only when entering a critical band from a
        # non-critical band.
        if (
            new_band in critical_bands
            and old_band not in critical_bands
        ):
            return True

    return False


def json_safe(value):
    """Convert values into JSON-compatible representations."""

    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


# ============================================================
# 6. MQTT JSON PUBLISHING
# ============================================================

def publish_json(topic, payload):
    """Publish JSON and check the MQTT result."""

    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False
        )

        info = client.publish(
            topic,
            payload=encoded,
            qos=1,
            retain=False
        )

        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            print(
                "[PUBLISH ERROR] MQTT return code:",
                info.rc
            )
            return False

        info.wait_for_publish(timeout=10)

        if not info.is_published():
            print("[PUBLISH ERROR] Publish was not confirmed.")
            return False

        print("[PUBLISH SUCCESS] Topic:", topic)
        print(json.dumps(payload, indent=2, ensure_ascii=False))

        return True

    except Exception as exc:
        print("[PUBLISH ERROR]", type(exc).__name__, exc)
        return False


# ============================================================
# 7. AUTOMATIC AI CARE-TIP GENERATION
# ============================================================

def process_telemetry(sensor_data, changes, trigger_reasons):
    """Generate and publish a validated Groq AI care message."""

    global last_published_care_message

    print("\n" + "=" * 60)
    print("[TELEMETRY PROCESSING]")
    print("[DETECTED FIELD CHANGES]", len(changes))
    print("[CARE-TIP TRIGGER REASONS]", trigger_reasons)

    for change in changes:
        print(
            f"[SENSOR CHANGE] {change['sensor']}: "
            f"{change['previous']} -> {change['current']}"
        )

    try:
        observations = analyze_sensor_data(sensor_data)

        print("\n[OBSERVATIONS]")
        print(observations)

        with state_lock:
            previous_message = last_published_care_message

        ai_observations = list(observations)

        if previous_message:
            ai_observations.append(
                "Previous published message for context only: "
                f"{previous_message}. Current sensor readings have "
                "changed meaningfully. Prioritise the current observations "
                "and avoid repeating previous wording unless it remains "
                "the correct advice."
            )

        print("\n[GROQ AI] Generating care guidance...")

        care_message = llm_service.generate_care_message(
            sensor_data,
            ai_observations
        )

        if not isinstance(care_message, str) or not care_message.strip():
            print("[PUBLISH CANCELLED] No valid AI message.")
            return

        if (
            getattr(llm_service, "LAST_MESSAGE_SOURCE", None)
            != "GROQ_AI"
        ):
            print(
                "[PUBLISH CANCELLED] Source is not verified Groq AI."
            )
            return

        care_message = care_message.strip()

        with state_lock:
            previous_message = last_published_care_message

        if (
            previous_message
            and care_message.casefold() == previous_message.casefold()
        ):
            print(
                "[PUBLISH SKIPPED] Groq returned the same care message "
                "as the previous published message."
            )
            return

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sensor_data": json_safe(sensor_data),
            "observations": json_safe(observations),
            "changes": json_safe(changes),
            "trigger_reasons": json_safe(trigger_reasons),
            "care_message": care_message,
            "message_source": "GROQ_AI",
        }

        print("\n[AI CARE MESSAGE]")
        print(care_message)

        if publish_json(OUTPUT_TOPIC, payload):
            with state_lock:
                last_published_care_message = care_message

    except Exception as exc:
        print("[CARE-TIP ERROR]", type(exc).__name__, exc)
        print(
            "[PUBLISH CANCELLED] No fallback message will be published."
        )

    finally:
        print("=" * 60)


# ============================================================
# 8. USER QUERY ANSWERING
# ============================================================

def handle_query(received):
    """Answer dashboard questions using Groq AI only."""

    if isinstance(received, dict):
        query = (
            received.get("query")
            or received.get("question")
            or received.get("message")
        )
        device_id = received.get(
            "device_id",
            "GreenPulse01"
        )

    elif isinstance(received, str):
        query = received
        device_id = "GreenPulse01"

    else:
        print("[QUERY ERROR] Unsupported payload format.")
        return

    if not isinstance(query, str) or not query.strip():
        print("[QUERY ERROR] No question was supplied.")
        return

    with state_lock:
        sensor_snapshot = (
            json.loads(json.dumps(latest_sensor_data))
            if latest_sensor_data is not None
            else None
        )

    print("\n" + "=" * 60)
    print("[USER QUERY]", query)

    try:
        answer = llm_service.answer_user_query(
            query.strip(),
            sensor_data=sensor_snapshot
        )

        if not isinstance(answer, str) or not answer.strip():
            print("[QUERY CANCELLED] No valid AI answer.")
            return

        if (
            getattr(llm_service, "LAST_QUERY_SOURCE", None)
            != "GROQ_AI"
        ):
            print(
                "[QUERY CANCELLED] Answer source is not verified Groq AI."
            )
            return

        response_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "device_id": device_id,
            "query": query.strip(),
            "answer": answer.strip(),
            "sensor_data": json_safe(sensor_snapshot),
            "message_source": "GROQ_AI",
        }

        print("\n[AI QUERY ANSWER]")
        print(answer.strip())

        publish_json(
            QUERY_RESPONSE_TOPIC,
            response_payload
        )

    except Exception as exc:
        print("[QUERY ERROR]", type(exc).__name__, exc)
        print(
            "[QUERY CANCELLED] No fallback answer will be published."
        )

    finally:
        print("=" * 60)


# ============================================================
# 9. MQTT CALLBACKS
# ============================================================

def on_connect(
    client,
    userdata,
    flags,
    reason_code,
    properties
):
    if reason_code.is_failure:
        print("[AWS MQTT] Connection failed:", reason_code)
        return

    print("\n[AWS MQTT] Connected successfully.")

    result, _ = client.subscribe(
        [
            (INPUT_TOPIC, 1),
            (QUERY_TOPIC, 1),
        ]
    )

    if result == mqtt.MQTT_ERR_SUCCESS:
        print("[SUBSCRIBE] Telemetry:", INPUT_TOPIC)
        print("[SUBSCRIBE] User queries:", QUERY_TOPIC)
    else:
        print("[SUBSCRIBE ERROR]", result)


def on_subscribe(
    client,
    userdata,
    mid,
    reason_code_list,
    properties
):
    print(
        "[AWS MQTT] Subscription acknowledged:",
        reason_code_list
    )
    print("[AGENT] Waiting for telemetry and user queries.")


def on_disconnect(
    client,
    userdata,
    disconnect_flags,
    reason_code,
    properties
):
    print("[AWS MQTT] Disconnected:", reason_code)


def on_message(client, userdata, message):
    """
    Log every sensor change. Queue meaningful changes after the
    cooldown, but bypass it when a sensor enters a critical band.
    """

    global previous_sensor_data
    global latest_sensor_data
    global last_care_trigger_sensor_data
    global last_care_request_time

    try:
        decoded = message.payload.decode("utf-8")

        try:
            received = json.loads(decoded)
        except json.JSONDecodeError:
            if message.topic == QUERY_TOPIC:
                received = decoded
            else:
                print(
                    "[INPUT ERROR] Telemetry must be valid JSON."
                )
                return

        if message.topic == QUERY_TOPIC:
            print("\n[MQTT] User query received.")
            worker.submit(handle_query, received)
            return

        if message.topic != INPUT_TOPIC:
            print(
                "[MQTT] Ignoring unexpected topic:",
                message.topic
            )
            return

        if not isinstance(received, dict):
            print(
                "[INPUT ERROR] Telemetry must be a JSON object."
            )
            return

        sensor_data = received.get("sensor_data", received)

        if not isinstance(sensor_data, dict):
            print(
                "[INPUT ERROR] sensor_data must be a JSON object."
            )
            return

        with state_lock:
            changes = detect_sensor_changes(
                sensor_data,
                previous_sensor_data
            )

            previous_sensor_data = json.loads(
                json.dumps(sensor_data)
            )

            latest_sensor_data = json.loads(
                json.dumps(sensor_data)
            )

        print("\n[MQTT] Telemetry received.")
        print("[INPUT TOPIC]", message.topic)
        print(
            "[RECEIVED AT]",
            datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
        )

        if not changes:
            print(
                "[CHANGE DETECTION] No tracked values changed."
            )
            print(
                "[ACTION] No additional care-tip request."
            )
            return

        print(
            "[CHANGE DETECTION] Changes recorded:",
            len(changes)
        )

        for change in changes:
            print(
                f"[SENSOR CHANGE] {change['sensor']}: "
                f"{change['previous']} -> {change['current']}"
            )

        with state_lock:
            trigger_reasons = meaningful_sensor_changes(
                sensor_data,
                last_care_trigger_sensor_data
            )

            if not trigger_reasons:
                should_queue = False
                skip_reason = "Only minor sensor fluctuations"
                urgent_change = False

            else:
                urgent_change = urgent_sensor_condition_transition(
                    sensor_data,
                    last_care_trigger_sensor_data
                )

                now = time.monotonic()

                elapsed = (
                    now - last_care_request_time
                    if last_care_request_time is not None
                    else None
                )

                cooldown_active = (
                    elapsed is not None
                    and elapsed < CARE_TIP_COOLDOWN_SECONDS
                )

                if cooldown_active and not urgent_change:
                    should_queue = False
                    remaining = CARE_TIP_COOLDOWN_SECONDS - elapsed

                    skip_reason = (
                        "Cooldown active; approximately "
                        f"{max(0, int(remaining))} seconds remaining"
                    )

                else:
                    should_queue = True
                    skip_reason = None

                    last_care_trigger_sensor_data = json.loads(
                        json.dumps(sensor_data)
                    )
                    last_care_request_time = now

        if not should_queue:
            print("[CARE TIP SKIPPED]", skip_reason)
            print(
                "[ACTION] Telemetry received and changes logged."
            )
            return

        print("[MEANINGFUL CHANGE DETECTED]")

        for reason in trigger_reasons:
            print(" -", reason)

        if urgent_change:
            print(
                "[URGENT CHANGE] Critical sensor condition detected. "
                "Cooldown bypassed."
            )

        worker.submit(
            process_telemetry,
            json.loads(json.dumps(sensor_data)),
            json.loads(json.dumps(changes)),
            json.loads(json.dumps(trigger_reasons))
        )

        print(
            "[WORKER] Meaningful reading queued for AI processing."
        )

    except Exception as exc:
        print("[MESSAGE ERROR]", type(exc).__name__, exc)


# ============================================================
# 10. AWS IOT MQTT CLIENT
# ============================================================

client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id=AWS_IOT_CLIENT_ID,
    protocol=mqtt.MQTTv311
)

client.tls_set(
    ca_certs=CA_FILE,
    certfile=CERT_FILE,
    keyfile=KEY_FILE,
    cert_reqs=ssl.CERT_REQUIRED,
    tls_version=ssl.PROTOCOL_TLS_CLIENT
)

client.on_connect = on_connect
client.on_subscribe = on_subscribe
client.on_disconnect = on_disconnect
client.on_message = on_message


# ============================================================
# 11. START COMPONENT 2
# ============================================================

def main():
    print("=" * 60)
    print("GREENPULSE - COMPONENT 2")
    print("LLM CARE-TIP GENERATION AGENT")
    print("=" * 60)

    print("AWS endpoint:", AWS_IOT_ENDPOINT)
    print("AWS port:", AWS_IOT_PORT)
    print("Client ID:", AWS_IOT_CLIENT_ID)
    print("Telemetry input:", INPUT_TOPIC)
    print("Care-tip output:", OUTPUT_TOPIC)
    print("Query input:", QUERY_TOPIC)
    print("Query response:", QUERY_RESPONSE_TOPIC)
    print("Care generation: Groq AI only")
    print("Query answers: Groq AI only")
    print("Meaningful-change detection: enabled")
    print(
        "Care-tip cooldown:",
        CARE_TIP_COOLDOWN_SECONDS,
        "seconds"
    )
    print("Critical-condition cooldown bypass: enabled")
    print("All changed fields: logged")
    print("Fallback messages: disabled")
    print("=" * 60)

    try:
        client.connect(
            AWS_IOT_ENDPOINT,
            AWS_IOT_PORT,
            keepalive=60
        )

        print("[AWS MQTT] Starting listener...")
        client.loop_forever()

    except KeyboardInterrupt:
        print("\n[STOP] Stopping Component 2.")

    except Exception as exc:
        print("[FATAL ERROR]", type(exc).__name__, exc)

    finally:
        try:
            client.disconnect()
        except Exception:
            pass

        worker.shutdown(
            wait=False,
            cancel_futures=True
        )

        print("[STOP] Component 2 stopped.")


if __name__ == "__main__":
    main()
