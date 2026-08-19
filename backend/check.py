import os

import requests

# This gateway is NOT OpenAI-API-compatible: it exposes POST /v1/call_llm,
# not /v1/chat/completions, so the OpenAI SDK can't be pointed at it directly.
#
# The gateway holds no provider keys itself — set your own OpenAI key in the
# environment before running this script.
resp = requests.post(
    "http://localhost:8000/v1/call_llm",
    headers={
        "Authorization": "Bearer sk-guard-eff4c2b0f05961319b23f01513a5056fc10ca20dc2ebb651",
        "X-Provider-Key": os.environ["OPENAI_API_KEY"],
    },
    json={
        "provider": "openai",  # optional; falls back to your registered vendor
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are a concise assistant. Answer in one sentence."},
            {"role": "user",   "content": "Explain what an LLM gateway is."},
        ],
        
    },
)

resp.raise_for_status()
data = resp.json()
print(data["choices"][0]["message"]["content"])
