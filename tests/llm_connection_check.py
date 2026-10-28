
from openai import OpenAI


def test_llm_connection():
    try:
        # Load API key and model from your existing config.py
        from backend.app.core.config import settings

        api_key = settings.OPENAI_API_KEY.get_secret_value()
        model = settings.LLM_MODEL

        print(f"Testing OpenAI LLM connection...")
        print(f"Configured model: {model}")

        # Create the OpenAI client
        client = OpenAI(
            api_key=api_key,
            timeout=30.0,
        )

        # Make a real LLM API call
        response = client.responses.create(
            model=model,
            input="Reply with exactly this text: LLM connection successful."
        )

        print("\nSUCCESS: LLM API call completed!")
        print(f"Model used: {response.model}")
        print(f"LLM response: {response.output_text}")

        if response.usage:
            print(f"Input tokens: {response.usage.input_tokens}")
            print(f"Output tokens: {response.usage.output_tokens}")

    except Exception as exc:
        print("\nFAILED: LLM API call did not complete.")
        print(f"Error type: {type(exc).__name__}")
        print(f"Error details: {exc}")


if __name__ == "__main__":
    test_llm_connection()
