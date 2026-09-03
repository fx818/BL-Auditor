import requests


def main(USER_ID: str, MODID: str, AK: str, api_key: str, model: str, item_name: str, specs: list):
    url = "https://api-gateway.intermesh.net/api/w/production/jobs/run_wait_result/f/f/small_agents/title_specs_mismatch_agent_flow"

    headers = {
        "Authorization": "Bearer IUufzyroO3zPV5CstsPNYbkLjzIRYBY7",
        "Content-Type": "application/json",
    }

    payload = {
        "USER_ID": USER_ID,
        "MODID": MODID,
        "AK": AK,
        "api_key": api_key,
        "model": model,
        "item_name": item_name,
        "specs": specs,
    }

    response = requests.post(url, headers=headers, json=payload)
    return response.json()


# Output response:
# {
#     "cost": 0,
#     "model": "google/gemini-2.5-flash-lite",
#     "message": "{\n  \"status\": \"outlier\",\n  \"reason\": \"The title 'TMT Bars' implies a metal product, but the specifications list 'Material: Plastic'.\"\n}",
#     "response": {
#         "reason": "The title 'TMT Bars' implies a metal product, but the specifications list 'Material: Plastic'.",
#         "status": "outlier"
#     },
#     "token_usage": {
#         "cost": 0.00002735,
#         "is_byok": false,
#         "cost_details": {
#             "upstream_inference_cost": 0.00002735,
#             "upstream_inference_prompt_cost": 0.00001995,
#             "upstream_inference_completions_cost": 0.0000074
#         },
#         "total_tokens": 436,
#         "prompt_tokens": 399,
#         "completion_tokens": 37,
#         "prompt_tokens_details": {
#             "audio_tokens": 0,
#             "video_tokens": 0,
#             "cached_tokens": 0,
#             "cache_write_tokens": 0,
#             "cache_creation_tokens": 0
#         },
#         "completion_tokens_details": {
#             "audio_tokens": 0,
#             "image_tokens": 0,
#             "reasoning_tokens": 0
#         }
#     },
#     "response_time": 1.583
# }