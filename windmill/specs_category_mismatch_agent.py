import requests


def main(USER_ID: str, MODID: str, AK: str, api_key: str, model: str, specs: str, mcat_name: str):
    url = "https://api-gateway.intermesh.net/api/w/production/jobs/run_wait_result/f/f/small_agents/specs_category_mismatch_agent_flow"

    headers = {
        "Authorization": "Bearer L8XvjBl5sSZkHO4mWBGOuubGhzkLEEXe",
        "Content-Type": "application/json",
    }

    payload = {
        "USER_ID": USER_ID,
        "MODID": MODID,
        "AK": AK,
        "api_key": api_key,
        "model": model,
        "specs": specs,
        "mcat_name": mcat_name,
    }

    response = requests.post(url, headers=headers, json=payload)
    return response.json()



# Output response:
# {
#     "cost": 0,
#     "model": "google/gemini-2.5-flash-lite",
#     "message": "{\"item_id\":\"\",\"status\":\"OUTLIER\",\"reason\":\"The \\\"Back Type\\\" specification \\\"High Back\\\" is inconsistent with the \\\"Head Visor\\\" category, as a head visor does not have a back style or type.\"}",
#     "response": {
#         "reason": "The \"Back Type\" specification \"High Back\" is inconsistent with the \"Head Visor\" category, as a head visor does not have a back style or type.",
#         "status": "OUTLIER",
#         "item_id": ""
#     },
#     "token_usage": {
#         "cost": 0.0000515,
#         "is_byok": false,
#         "cost_details": {
#             "upstream_inference_cost": 0.0000515,
#             "upstream_inference_prompt_cost": 0.0000419,
#             "upstream_inference_completions_cost": 0.0000096
#         },
#         "total_tokens": 886,
#         "prompt_tokens": 838,
#         "completion_tokens": 48,
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
#     "response_time": 0.672
# }