"""
Title: EasyLang - Contextual Translation Filter & Session Anchoring
Version: 0.1.3
https://github.com/annibale-x/open-webui-easylang
Author: Hannibal
Author_url: https://openwebui.com/u/h4nn1b4l
Author_email: annibale.x@gmail.com
Description: Open WebUI translation filter with persistent session state management.

MAIN FEATURES:
- SIMPLE TRANSLATION HELPER
"""

import re
import sys
import time
from typing import Optional
from pydantic import BaseModel, Field
from open_webui.main import generate_chat_completion


class Filter:
    class Valves(BaseModel):
        translation_model: str = Field(
            default="", description="Model for translation. Empty = current."
        )
        default_lang: str = Field(
            default="English", description="Target language if not specified."
        )
        debug: bool = Field(default=True)

    def __init__(self):
        self.valves = self.Valves()
        self.memory = {}

    def _dbg(self, message: str):
        if self.valves.debug:
            print(f"⚡ EASYTRANS: {message}", file=sys.stderr, flush=True)

    class UserWrapper:
        def __init__(self, user_dict):
            self.role = "user"
            self.id = "user_id"
            if user_dict and isinstance(user_dict, dict):
                self.role = user_dict.get("role", "user")
                self.id = user_dict.get("id", "user_id")
                for k, v in user_dict.items():
                    setattr(self, k, v)

    async def _translate(
        self, text: str, target_lang: str, model_id: str, __request__, __user__
    ) -> tuple[str, dict]:
        selected_model = (
            self.valves.translation_model if self.valves.translation_model else model_id
        )
        user_object = self.UserWrapper(__user__)

        # Professional prompt for clean translation
        payload = {
            "model": selected_model,
            "messages": [
                {
                    "role": "user",
                    "content": f"ACT AS A TRANSLATOR. Target Language: {target_lang}. Output ONLY the translated text. Text: {text}",
                }
            ],
            "stream": False,
        }

        stats = {"elapsed": 0.0, "tokens": 0, "tps": 0.0}
        start_time = time.perf_counter()

        try:
            response = await generate_chat_completion(
                __request__, payload, user=user_object
            )
            stats["elapsed"] = time.perf_counter() - start_time

            if response and "choices" in response:
                content = (
                    response["choices"][0]["message"]["content"].strip().strip('"')
                )
                usage = response.get("usage", {})
                stats["tokens"] = usage.get(
                    "completion_tokens", len(content.split()) * 1.3
                )
                if stats["elapsed"] > 0:
                    stats["tps"] = stats["tokens"] / stats["elapsed"]
                return content, stats
            return text, stats
        except Exception as e:
            self._dbg(f"Translation error: {e}")
            return text, stats

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __request__=None,
        __event_emitter__=None,
    ) -> dict:
        messages = body.get("messages", [])
        if not messages:
            return body

        content = messages[-1].get("content", "")

        # ADVANCED PARSER:
        # Matches:
        # 1. tr: <text> (uses default valve lang)
        # 2. tr-<lang>: <text> (e.g. tr-it: hello)
        # 3. tr/<lang>: <text> (e.g. tr/de: ciao)
        # 4. chat: <text> (direct translation no overwrite)
        match = re.match(
            r"^(tr|chat)(?:[-/]([a-zA-Z]{2,}))?\:\s*(.*)",
            content,
            re.IGNORECASE | re.DOTALL,
        )

        if not match:
            return body

        prefix = match.group(1).lower()  # tr or chat
        lang_code = match.group(2)  # optional lang code (e.g. 'it', 'de')
        original_text = match.group(3).strip()  # the actual text

        if not original_text:
            return body

        # Resolve target language
        target_lang = lang_code if lang_code else self.valves.default_lang

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"Translating to {target_lang}...",
                        "done": False,
                    },
                }
            )

        translated, stats = await self._translate(
            original_text, target_lang, body.get("model", ""), __request__, __user__
        )

        body["easytrans_mode"] = prefix
        body["easytrans_data"] = translated
        if __user__ and "id" in __user__:
            self.memory[__user__["id"]] = {"mode": prefix, "data": translated}

        # Silent strategy for 'tr' mode
        if prefix == "tr":
            messages[-1]["content"] = "Respond with one single dot. Do not think."
        else:
            messages[-1]["content"] = translated

        if __event_emitter__:
            status_msg = f"Done ({target_lang}) | Time: {stats['elapsed']:.2f}s | Speed: {stats['tps']:.1f} tok/s"
            await __event_emitter__(
                {"type": "status", "data": {"description": status_msg, "done": True}}
            )

        return body

    async def outlet(
        self, body: dict, __user__: Optional[dict] = None, __request__=None
    ) -> dict:
        mode = body.get("easytrans_mode")
        translated_input = body.get("easytrans_data")

        if not mode and __user__ and __user__["id"] in self.memory:
            mem = self.memory.pop(__user__["id"])
            mode, translated_input = mem["mode"], mem["data"]

        if mode == "tr" and "messages" in body:
            body["messages"][-1]["content"] = f"{translated_input}"

        return body
