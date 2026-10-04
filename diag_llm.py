"""Temporary diagnostic: inspect the raw Ollama response."""

import json

import ollama

from week2.app.config import settings
from week2.app.services.extract import EXTRACTION_SYSTEM_PROMPT, ItemList

SAMPLES = [
    "- [ ] Set up database\n- Write tests",
    "Remind me to email Bob about the invoice",
    "Meeting notes:\nSarah will handle the Q3 report.\nTODO: deploy the new API",
]

client = ollama.Client(host=settings.ollama_host)

for sample in SAMPLES:
    print("=" * 70)
    print("INPUT:", repr(sample))
    print("-" * 70)
    response = client.chat(
        model=settings.ollama_model,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": sample},
        ],
        options={"temperature": settings.ollama_temperature},
        format=ItemList.model_json_schema(),
    )
    print("RAW CONTENT:", repr(response.message.content))
    try:
        print("PARSED:", [i.name for i in ItemList.model_validate_json(response.message.content).items])
    except Exception as exc:
        print("PARSE FAILED:", exc)
    print()

print("=" * 70)
print("SCHEMA SENT TO MODEL:")
print(json.dumps(ItemList.model_json_schema(), indent=2))