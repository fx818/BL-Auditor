import requests

def test_llm_connection(base_url, api_key, model):
    """
    Test connectivity for ANY LLM provider that exposes an OpenAI-compatible
    /v1/chat/completions endpoint — including Gemini served through a custom base URL.
    """

    # Ensure no trailing slash
    base_url = base_url.rstrip("/")

    url = f"{base_url}/v1/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Connection test. Respond with 'OK'."}
        ],
        "max_tokens": 5
    }

    print("\n==== Testing LLM Endpoint ====")
    print("URL:", url)
    print("Model:", model)

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        print("Status Code:", response.status_code)
        print("Response JSON:")
        print(response.json())
    except Exception as e:
        print("❌ Error connecting to LLM:", e)


if __name__ == "__main__":

    # ---------------------------------
    # USER INPUT
    # ---------------------------------
    BASE_URL = "https://imllm.intermesh.net"      # Put your base URL
    API_KEY = "sk-GyMwJ_4QWM10ARfkPpCQjQ"                       # Put your API key for this model
    MODEL = "google/gemini-2.5-pro"                     # Or gpt-4o, mistral-large, etc.

    test_llm_connection(BASE_URL, API_KEY, MODEL)