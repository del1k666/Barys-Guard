import requests
from PyQt6.QtCore import QThread, pyqtSignal
from config import Config


class AIWorker(QThread):
    chunk  = pyqtSignal(str)
    done   = pyqtSignal()
    error  = pyqtSignal(str)

    def __init__(self, messages, system_prompt="", api_key="", provider="groq"):
        super().__init__()
        self.messages      = messages
        self.system_prompt = system_prompt
        self.api_key       = api_key
        self.provider      = provider  # "groq" or "claude"

    def run(self):
        try:
            if self.provider == "groq":
                self._run_groq()
            else:
                self._run_claude()
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.done.emit()

    def _run_groq(self):
        msgs = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        msgs.extend(self.messages)
        payload = {
            "model": "llama-3.3-70b-versatile",
            "max_tokens": 2048,
            "messages": msgs,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload, headers=headers, timeout=60
        )
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"]
            self.chunk.emit(text)
        else:
            self.error.emit(f"Groq API error {r.status_code}: {r.text[:300]}")

    def _run_claude(self):
        payload = {
            "model": "claude-opus-4-5",
            "max_tokens": 2048,
            "messages": self.messages,
        }
        if self.system_prompt:
            payload["system"] = self.system_prompt
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            json=payload, headers=headers, timeout=60
        )
        if r.status_code == 200:
            text = r.json()["content"][0]["text"]
            self.chunk.emit(text)
        else:
            self.error.emit(f"Claude API error {r.status_code}: {r.text[:300]}")
