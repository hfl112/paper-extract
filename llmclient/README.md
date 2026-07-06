# llmclient

A tiny, provider-agnostic LLM client. One function — `call_llm` — over Gemini,
OpenAI, DeepSeek, and Claude. SDKs are imported lazily, so you only install what
you use.

## Install

```bash
pip install .           # core only
pip install ".[gemini]" # + google-genai
pip install ".[all]"    # all SDKs
```

## Use

```python
from llmclient import call_llm, available_providers

print(available_providers())                      # providers with an API key present
text = call_llm("Say hello.")                      # auto-pick provider
data = call_llm(prompt, provider="deepseek", json=True)
```

## Configuration (env / .env in CWD)

| Var | Meaning |
|---|---|
| `LLM_PROVIDER` | default provider: `gemini` / `openai` / `deepseek` / `claude` |
| `GEMINI_API_KEY`, `GEMINI_API_KEY_2`, … | Gemini keys (round-robined) |
| `OPENAI_API_KEY` | OpenAI |
| `DEEPSEEK_API_KEY` | DeepSeek (OpenAI-compatible) |
| `ANTHROPIC_API_KEY` | Claude |
| `<PROVIDER>_MODEL_<LEVEL>` | model override, e.g. `OPENAI_MODEL_STANDARD=gpt-4o` |

Provider resolution: explicit arg → `LLM_PROVIDER` → first provider with a key.
Model resolution: explicit arg → env override → built-in routing (`lite`/`standard`/`pro`).
