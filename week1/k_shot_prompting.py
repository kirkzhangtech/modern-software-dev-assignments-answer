import os
from dotenv import load_dotenv
from ollama import chat


load_dotenv()

NUM_RUNS_TIMES = 20

# TODO: Fill this in!
YOUR_SYSTEM_PROMPT = """
# Identity
You are a letter-reversal tool. Your only job is to reverse the order of characters in the input string.

# Rules
1. Output the reversed string directly. Nothing else.
2. Do NOT explain, greet, or add punctuation that wasn't in the input.
3. Reverse ALL characters, including spaces and symbols.
4. If the input is empty, output an empty string.
5. Do not "correct" or reinterpret the input. Reverse it exactly as given.
6. Starting from the last character of the input word, extract one character at a time until you reach the first character. Do not skip any repeated letters.
7. Do NOT match the input to any example by similarity.
8. Reverse the EXACT characters of the input, one by one.
# Output Format
Raw text only. No quotes, no code blocks, no labels.

# Examples


Input: understand
Output: dnatsrednu

Input: assessment
Output: tnemssessa

Input: successful
Output: lufsseccus



# Task
Reverse the input letter that user input, character by character.

"""

USER_PROMPT = """
Reverse the order of letters in the following word. Only output the reversed word, no other text:

httpstatus
"""


EXPECTED_OUTPUT = "sutatsptth"

def test_your_prompt(system_prompt: str) -> bool:
    """Run the prompt up to NUM_RUNS_TIMES and return True if any output matches EXPECTED_OUTPUT.

    Prints "SUCCESS" when a match is found.
    """
    for idx in range(NUM_RUNS_TIMES):
        print(f"Running test {idx + 1} of {NUM_RUNS_TIMES}")
        response = chat(
            model="mistral-nemo:12b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": USER_PROMPT},
            ],
            options={"temperature": 0.5},
        )
        output_text = response.message.content.strip()
        if output_text.strip() == EXPECTED_OUTPUT.strip():
            print("SUCCESS")
            return True
        else:
            print(f"Expected output: {EXPECTED_OUTPUT}")
            print(f"Actual output: {output_text}")
    return False

if __name__ == "__main__":
    test_your_prompt(YOUR_SYSTEM_PROMPT)