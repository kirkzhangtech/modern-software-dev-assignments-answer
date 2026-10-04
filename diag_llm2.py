"""Temporary diagnostic: test both local models with realistic inputs."""

import ollama

from week2.app.config import settings
from week2.app.services.extract import EXTRACTION_SYSTEM_PROMPT, ItemList

SAMPLES = [
    "Team sync notes.\nAlice: I'll finish the migration script by Friday.\nWe should probably schedule a retro.\nBob: the staging cluster is down again.",
    "notes\ntodo: call the bank\nask about the fee",
    "Please remember to renew the domain before it lapses.",
    "no tasks here, just a thought about the weather",
]

client = ollama.Client(host=settings.ollama_host)

for model in ["mistral-nemo:12b", "llama3.1:8b"]:
    print("#" * 70)
    print("MODEL:", model)
    for sample in SAMPLES:
        print("-" * 70)
        print("INPUT:", repr(sample))
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": sample},
            ],
            options={"temperature": settings.ollama_temperature},
            format=ItemList.model_json_schema(),
        )
        print("RAW:", repr(response.message.content))