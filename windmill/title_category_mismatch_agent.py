import requests


def main(USER_ID: str, MODID: str, AK: str, api_key: str, model: str, item_name: str, mcat_name: str):
    url = "https://api-gateway.intermesh.net/api/w/production/jobs/run_wait_result/f/f/small_agents/title_category_mismatch_agent_flow"

    headers = {
        "Authorization": "Bearer 1Ocu4XooEcC05kfOf4VyDTy5oW6nyuUn",
        "Content-Type": "application/json",
    }

    payload = {
        "USER_ID": USER_ID,
        "MODID": MODID,
        "AK": AK,
        "api_key": api_key,
        "model": model,
        "item_name": item_name,
        "mcat_name": mcat_name,
    }

    response = requests.post(url, headers=headers, json=payload)
    return response.json()



# Sample output
# {
#     "cost": 0,
#     "model": "google/gemini-2.5-flash-lite",
#     "message": "{\n  \"status\": \"outlier\",\n  \"reason\": \"A visor is typically an accessory or part of a helmet, not a helmet itself, indicating a type mismatch.\"\n}",
#     "response": {
#         "reason": "A visor is typically an accessory or part of a helmet, not a helmet itself, indicating a type mismatch.",
#         "status": "outlier"
#     },
#     "token_usage": {
#         "cost": 0.0000424,
#         "is_byok": false,
#         "cost_details": {
#             "upstream_inference_cost": 0.0000424,
#             "upstream_inference_prompt_cost": 0.0000264,
#             "upstream_inference_completions_cost": 0.000016
#         },
#         "total_tokens": 304,
#         "prompt_tokens": 264,
#         "completion_tokens": 40,
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
#     "response_time": 2.588
# }