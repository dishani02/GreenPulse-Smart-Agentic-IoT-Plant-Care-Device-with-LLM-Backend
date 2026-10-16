import requests


def weather_code_to_text(code):
    weather_codes = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        71: "Slight snow",
        73: "Moderate snow",
        75: "Heavy snow",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail"
    }

    return weather_codes.get(code, "Unknown weather condition")


# Colombo coordinates
LATITUDE = 6.9271
LONGITUDE = 79.8612

url = "https://api.open-meteo.com/v1/forecast"

params = {
    "latitude": LATITUDE,
    "longitude": LONGITUDE,

    "current": (
        "temperature_2m,"
        "relative_humidity_2m,"
        "precipitation,"
        "weather_code,"
        "wind_speed_10m"
    ),

    "daily": (
        "temperature_2m_max,"
        "temperature_2m_min,"
        "precipitation_probability_max,"
        "weather_code"
    ),

    "forecast_days": 3,
    "timezone": "auto"
}


try:
    response = requests.get(
        url,
        params=params,
        timeout=10
    )

    if response.status_code == 200:

        data = response.json()

        current = data["current"]
        daily = data["daily"]

        print("Weather API connected successfully")

        print("\n==============================")
        print("CURRENT WEATHER")
        print("==============================")

        print(
            "Temperature:",
            current["temperature_2m"],
            "C"
        )

        print(
            "Humidity:",
            current["relative_humidity_2m"],
            "%"
        )

        print(
            "Precipitation:",
            current["precipitation"],
            "mm"
        )

        print(
            "Weather:",
            weather_code_to_text(
                current["weather_code"]
            )
        )

        print(
            "Wind Speed:",
            current["wind_speed_10m"],
            "km/h"
        )

        print("\n==============================")
        print("3-DAY FORECAST")
        print("==============================")

        for i in range(len(daily["time"])):

            print("\n------------------------------")

            print(
                "Date:",
                daily["time"][i]
            )

            print(
                "Maximum Temperature:",
                daily["temperature_2m_max"][i],
                "C"
            )

            print(
                "Minimum Temperature:",
                daily["temperature_2m_min"][i],
                "C"
            )

            print(
                "Rain Probability:",
                daily["precipitation_probability_max"][i],
                "%"
            )

            print(
                "Weather:",
                weather_code_to_text(
                    daily["weather_code"][i]
                )
            )

    else:
        print("Weather API failed")
        print("Status code:", response.status_code)
        print(response.text)


except requests.exceptions.RequestException as error:

    print("Weather API connection error")
    print(error)