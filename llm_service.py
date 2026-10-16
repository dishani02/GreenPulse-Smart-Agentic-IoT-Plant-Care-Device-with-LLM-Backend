import os
import re

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq


load_dotenv()

LAST_MESSAGE_SOURCE = None
LAST_QUERY_SOURCE = None

SENSOR_FIELDS = (
    "soil_moisture",
    "temperature",
    "humidity",
    "light",
    "plant_status",
    "soil_raw",
    "air_raw",
    "light_raw",
    "sunlight_level",
)


def _get_current_reading(sensor_data):
    """Extract available sensor readings without inventing values."""

    if not isinstance(sensor_data, dict):
        return {}

    nested = sensor_data.get("sensor_data")

    if isinstance(nested, dict):
        sensor_data = nested

    return {
        field: sensor_data[field]
        for field in SENSOR_FIELDS
        if field in sensor_data
    }


def _fmt(value):
    if value is None or value == "":
        return "Unavailable"

    return str(value)


def _response_text(response):
    """Extract the final text from a LangChain response."""

    if response is None:
        return ""

    content = getattr(response, "content", None)

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []

        for item in content:
            if isinstance(item, str):
                if item.strip():
                    parts.append(item.strip())

            elif isinstance(item, dict):
                text_part = item.get("text")

                if text_part:
                    parts.append(str(text_part).strip())

        return "\n".join(parts).strip()

    if content is not None:
        return str(content).strip()

    return ""


def _create_llm(max_tokens=300):
    """Create the Groq AI client."""

    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is missing. Check the project's .env file."
        )

    model = os.getenv(
        "GROQ_MODEL",
        "openai/gpt-oss-20b",
    ).strip()

    settings = {
        "model": model,
        "api_key": api_key,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "timeout": 30,
        "max_retries": 1,
    }

    if model.lower().startswith("openai/gpt-oss"):
        settings["reasoning_format"] = "parsed"
        settings["reasoning_effort"] = "low"

    return ChatGroq(**settings)


def _count_sentences(text):
    """Count sentence endings while protecting decimals and abbreviations."""

    text = re.sub(r"\s+", " ", text.strip())

    if not text:
        return 0

    text = re.sub(r"(?<=\d)\.(?=\d)", "<DECIMAL>", text)

    text = re.sub(
        r"\b(e\.g|i\.e|Mr|Mrs|Ms|Dr|etc)\.",
        lambda match: match.group(0).replace(".", "<DOT>"),
        text,
        flags=re.IGNORECASE,
    )

    sentences = re.findall(r"[^.!?]+[.!?]+", text)
    remaining = re.sub(r"[^.!?]+[.!?]+", "", text).strip()

    if remaining:
        return -1

    return len(sentences)


def _validate_care_message(content):
    """Validate the required care-guidance and botanical-note format."""

    if not isinstance(content, str) or not content.strip():
        return None

    cleaned = content.strip()

    cleaned = re.sub(
        r"^\s*```(?:text|markdown|plaintext)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = re.sub(r"\s*```\s*$", "", cleaned).strip()

    care_match = re.match(
        r"^\s*Care guidance\s*:\s*",
        cleaned,
        flags=re.IGNORECASE,
    )

    if not care_match:
        return None

    botanical_matches = list(
        re.finditer(
            r"\bBotanical note\s*:",
            cleaned,
            flags=re.IGNORECASE,
        )
    )

    if len(botanical_matches) != 1:
        return None

    botanical_match = botanical_matches[0]

    care_text = cleaned[
        care_match.end():botanical_match.start()
    ].strip()

    botanical_text = cleaned[
        botanical_match.end():
    ].strip()

    if not care_text or not botanical_text:
        return None

    if re.search(
        r"\bCare guidance\s*:",
        care_text + botanical_text,
        flags=re.IGNORECASE,
    ):
        return None

    if re.search(
        r"\bBotanical note\s*:",
        botanical_text,
        flags=re.IGNORECASE,
    ):
        return None

    if _count_sentences(care_text) != 2:
        return None

    if _count_sentences(botanical_text) != 1:
        return None

    return (
        f"Care guidance: {care_text}\n"
        f"Botanical note: {botanical_text}"
    )


def _format_observations(observations):
    if isinstance(observations, (list, tuple, set)):
        values = [
            str(item)
            for item in observations
            if item is not None
        ]

        if values:
            return "\n".join(values)

        return "No additional observations supplied."

    if observations is None:
        return "No additional observations supplied."

    return str(observations)


def _invoke_groq(prompt, values, max_tokens):
    """Call Groq and print diagnostic information."""

    llm = _create_llm(max_tokens=max_tokens)

    response = (prompt | llm).invoke(values)

    content = _response_text(response)

    print(
        "[DEBUG GROQ MODEL]",
        os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
    )
    print("[DEBUG GROQ RESPONSE TYPE]", type(response).__name__)
    print("[DEBUG GROQ CONTENT]", repr(content))

    usage = getattr(response, "usage_metadata", None)

    if usage:
        print("[DEBUG GROQ USAGE]", usage)

    metadata = getattr(response, "response_metadata", None)

    if isinstance(metadata, dict):
        finish_reason = metadata.get("finish_reason")

        if finish_reason is not None:
            print("[DEBUG GROQ FINISH REASON]", finish_reason)

    return content


def generate_care_message(sensor_data, observations=None):
    """
    Generate sensor-aware plant-care guidance using Groq AI only.

    Returns a validated message or None.
    """

    global LAST_MESSAGE_SOURCE

    LAST_MESSAGE_SOURCE = None

    current = _get_current_reading(sensor_data)

    if not current:
        LAST_MESSAGE_SOURCE = "GROQ_FAILED_NO_MESSAGE"
        print("[AI ERROR] No usable sensor data.")
        return None

    readings_text = "\n".join(
        f"{field}: {_fmt(value)}"
        for field, value in current.items()
    )

    observation_text = _format_observations(observations)

    prompt = ChatPromptTemplate.from_template("""
You are GreenPulse's AI plant-care tip generation agent.
Use simple, accurate English and only the supplied evidence.

OUTPUT FORMAT:
Return exactly two non-empty lines.
Line 1: Care guidance: followed by exactly TWO complete sentences.
Line 2: Botanical note: followed by exactly ONE complete sentence.
Do not add Markdown, bullets, introductions, or extra headings.

CARE GUIDANCE RULES:
- Make advice relevant to current sensor readings and observations.
- Never invent readings, plant species, symptoms, or diagnoses.
- Never infer air quality from air_raw alone.
- Thresholds are configured indicators, not universal plant requirements.
- If soil_moisture is unavailable, do not guess whether soil is dry or wet.
- If soil_moisture is below 30, prioritise checking actual soil dryness
  and watering only if needed.
- If soil_moisture is above 80, do not recommend adding water.
  Recommend checking whether the soil remains excessively wet.
- If soil_moisture is between 30 and 80 inclusive, do not recommend
  watering based on the number alone.
- If light is DARK, VERY_WEAK, or LOW, or sunlight_level is VERY_WEAK,
  consider suitable brighter indirect light.
- Do not recommend changing light unless the supplied readings support it.
- If soil moisture and light both need attention, address soil moisture first.
- Use temperature, humidity, and plant_status only when relevant.
- Do not diagnose plant disease from sensor readings.
- Sentence two should provide a useful monitoring suggestion without
  repeating sentence one.

BOTANICAL NOTE:
- Give one short, generally reliable botanical fact.
- Do not repeat the care guidance.
- Do not invent a plant species or make a plant-specific claim when
  the species is unknown.

CURRENT SENSOR READINGS:
{readings}

OBSERVATIONS:
{observations}

Return only the two required lines.
""")

    try:
        raw_content = _invoke_groq(
            prompt,
            {
                "readings": readings_text,
                "observations": observation_text,
            },
            max_tokens=300,
        )

        if not raw_content:
            LAST_MESSAGE_SOURCE = "GROQ_FAILED_NO_MESSAGE"
            print("[AI ERROR] Groq returned empty final content.")
            print("[AI ACTION] No care message will be published.")
            return None

        validated_message = _validate_care_message(raw_content)

        if validated_message is None:
            LAST_MESSAGE_SOURCE = "GROQ_FAILED_NO_MESSAGE"
            print("[AI ERROR] Groq response failed format validation.")
            print("[AI ACTION] No care message will be published.")
            return None

        LAST_MESSAGE_SOURCE = "GROQ_AI"
        print("[AI MESSAGE SOURCE] GROQ_AI")

        return validated_message

    except Exception as exc:
        LAST_MESSAGE_SOURCE = "GROQ_FAILED_NO_MESSAGE"

        print("[AI ERROR] Care-tip generation failed.")
        print("[AI ERROR TYPE]", type(exc).__name__)
        print("[AI ERROR DETAILS]", str(exc))
        print("[AI ACTION] No fallback care message generated.")

        return None


def answer_user_query(query, sensor_data=None):
    """Answer dashboard questions using Groq AI only."""

    global LAST_QUERY_SOURCE

    LAST_QUERY_SOURCE = None

    if not isinstance(query, str) or not query.strip():
        LAST_QUERY_SOURCE = "GROQ_FAILED_NO_ANSWER"
        print("[AI ERROR] Empty user query.")
        return None

    current = _get_current_reading(sensor_data or {})

    if current:
        readings_text = "\n".join(
            f"{field}: {_fmt(value)}"
            for field, value in current.items()
        )
    else:
        readings_text = "Sensor readings unavailable."

    prompt = ChatPromptTemplate.from_template("""
You are GreenPulse's AI plant-care assistant.

Answer the user's exact question in two to four short sentences
using simple English.

Rules:
- Use current sensor readings when relevant.
- Never invent readings, plant species, symptoms, or diagnoses.
- If readings are unavailable, say so briefly when relevant.
- Recommend checking actual soil conditions before deciding whether
  watering is needed.
- Never infer air quality from air_raw alone.
- Do not repeat the entire sensor dataset.
- Return plain text only, without headings or bullet points.

USER QUESTION:
{query}

CURRENT SENSOR READINGS:
{readings}

Answer directly.
""")

    try:
        answer = _invoke_groq(
            prompt,
            {
                "query": query.strip(),
                "readings": readings_text,
            },
            max_tokens=220,
        )

        if not answer:
            LAST_QUERY_SOURCE = "GROQ_FAILED_NO_ANSWER"
            print("[AI ERROR] Groq returned an empty answer.")
            return None

        LAST_QUERY_SOURCE = "GROQ_AI"
        print("[AI QUERY SOURCE] GROQ_AI")

        return answer

    except Exception as exc:
        LAST_QUERY_SOURCE = "GROQ_FAILED_NO_ANSWER"

        print("[AI ERROR] User-query generation failed.")
        print("[AI ERROR TYPE]", type(exc).__name__)
        print("[AI ERROR DETAILS]", str(exc))

        return None