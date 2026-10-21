"""The AI layer: any of Claude, OpenAI, Gemini or a local OpenAI-compatible server (Ollama, LM Studio), chosen per
task, with keys encrypted, a monthly spend cap, and a log of every call (counts and cost only, never content).

Nothing is sent anywhere until the owner adds a provider, turns it on and picks it for a task.
"""
