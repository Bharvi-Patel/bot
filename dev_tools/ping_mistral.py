import os, httpx
from dotenv import load_dotenv
load_dotenv()
for model in ("mistral-small-2603", "mistral-large-2512", "ministral-8b-2512", "ministral-3b-2512"):
    r = httpx.post("https://api.mistral.ai/v1/chat/completions",
                   headers={"Authorization": "Bearer " + os.environ["MISTRAL_API_KEY"]},
                   json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5})
    print(model, r.status_code, {k: v for k, v in r.headers.items() if "ratelimit" in k.lower()}, r.text[:100])