from datetime import datetime, timezone

import llm_service


def get_number(sensor_data, field):
    """Return a valid numeric reading, or None if unavailable."""
    value = sensor_data.get(field)

    if value is None or isinstance(value, bool):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def analyze_sensor_data(sensor_data):
    """Analyse current sensor readings and create observations."""

    if not isinstance(sensor_data, dict):
        return ["Sensor data is unavailable or invalid."]

    soil_moisture = get_number(sensor_data, "soil_moisture")
    temperature = get_number(sensor_data, "temperature")
    humidity = get_number(sensor_data, "humidity")

    light = str(sensor_data.get("light", "")).strip().upper()
    sunlight_level = str(
        sensor_data.get("sunlight_level", "")
    ).strip().upper()
    plant_status = str(
        sensor_data.get("plant_status", "")
    ).strip().upper()

    observations = []

    # PRIORITY 1: Soil moisture
    if soil_moisture is None:
        observations.append(
            "Soil moisture is unavailable. Do not assume the soil is dry or wet."
        )
    elif soil_moisture < 30:
        observations.append(
            "PRIORITY 1 - Soil moisture is below the configured 30% threshold. "
            "Check the actual soil and water only if it is dry."
        )
    elif soil_moisture > 80:
        observations.append(
            "PRIORITY 1 - Soil moisture is above the configured 80% threshold. "
            "Avoid adding more water and check whether the soil remains too wet."
        )
    else:
        observations.append(
            "Soil moisture is within the configured 30% to 80% range. "
            "Do not recommend watering based on this reading alone."
        )

    # PRIORITY 2: Temperature
    if temperature is None:
        observations.append("Temperature reading is unavailable.")
    elif temperature > 32:
        observations.append(
            "Temperature is above the configured 32 C threshold. "
            "Consider reducing excessive heat exposure."
        )
    elif temperature < 12:
        observations.append(
            "Temperature is below the configured 12 C threshold. "
            "Check whether the plant needs protection from cold."
        )

    # PRIORITY 3: Humidity
    if humidity is None:
        observations.append("Humidity reading is unavailable.")
    elif humidity > 85:
        observations.append(
            "Air humidity is above the configured 85% threshold. "
            "Check airflow around the plant."
        )
    elif humidity < 30:
        observations.append(
            "Air humidity is low compared with the configured threshold. "
            "Consider the plant's known humidity needs."
        )

    # PRIORITY 4: Light
    low_light = (
        light in {"DARK", "VERY_WEAK", "LOW"}
        or sunlight_level in {"DARK", "VERY_WEAK", "LOW"}
    )

    if low_light:
        observations.append(
            "PRIORITY 2 - The supplied light category indicates weak light. "
            "Consider suitable brighter indirect light if appropriate for the plant."
        )
    elif light in {"LIGHT", "BRIGHT"} or sunlight_level in {
        "MODERATE", "NORMAL", "BRIGHT", "HIGH"
    }:
        observations.append(
            "The supplied light category does not indicate very weak light. "
            "Do not recommend moving the plant based on this reading alone."
        )
    else:
        observations.append(
            "A reliable light category is unavailable. "
            "Do not infer actual light intensity from light_raw alone."
        )

    # Plant status is contextual, not a diagnosis.
    if plant_status in {"LOW", "MEDIUM", "HIGH"}:
        observations.append(
            f"Device plant status is {plant_status}. "
            "Use the sensor readings to identify relevant care actions; "
            "do not treat this status alone as a plant disease diagnosis."
        )

    # Raw air readings are not enough to determine air quality.
    if "air_raw" in sensor_data:
        observations.append(
            "air_raw is an uncalibrated raw reading. "
            "Do not infer air quality from this value alone."
        )

    return observations


def process_sensor_data(sensor_data):
    """Generate a care message for standalone testing."""

    observations = analyze_sensor_data(sensor_data)

    print("\n" + "=" * 55)
    print("GREENPULSE - CARE TIP GENERATION")
    print("=" * 55)
    print("Generating care message...")

    care_message = llm_service.generate_care_message(
        sensor_data,
        observations
    )

    message_source = llm_service.LAST_MESSAGE_SOURCE

    print("\nMessage source:", message_source)
    print("\nCare message:")
    print("-" * 55)
    print(
        care_message
        if care_message
        else "No valid AI care message generated."
    )
    print("-" * 55)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sensor_data": sensor_data,
        "observations": observations,
        "care_message": care_message,
        "message_source": message_source,
    }
