"""
LLM-agnostic client. Switch provider in business_profile.json.
Supports: anthropic (Claude), openai (GPT)
"""

import os
import json


def call_llm(system_prompt: str, user_message: str, profile: dict) -> str:
    provider = profile.get("llm", {}).get("provider", "anthropic")
    model = profile.get("llm", {}).get("model", "claude-sonnet-4-6")

    if provider == "anthropic":
        return _call_anthropic(system_prompt, user_message, model)
    elif provider == "openai":
        return _call_openai(system_prompt, user_message, model)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


def _call_anthropic(system_prompt: str, user_message: str, model: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text


def _call_openai(system_prompt: str, user_message: str, model: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
    )
    return response.choices[0].message.content
