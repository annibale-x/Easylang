"""
Title: EasyLang - Contextual Translation Filter & Session Anchoring
Version: 0.2.0
https://github.com/annibale-x/open-webui-easylang
Author: Hannibal
Author_url: https://openwebui.com/u/h4nn1b4l
Author_email: annibale.x@gmail.com
Description: Open WebUI translation filter.

MAIN FEATURES:
- SIMPLE TRANSLATION HELPER
- PERFORMANCE TELEMETRY: Real-time tracking of Tokens Per Second (TPS) and execution latency.
- BRIDGE MODE (TRC): Seamless integration into ongoing dialogues with history restoration and back-translation.
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
        back_translation: bool = Field(
            default=False, description="Translate assistant response back to Italian."
        )
        debug: bool = Field(default=True)

    def __init__(self):
        self.valves = self.Valves()
        self.memory = {}

    def _dbg(self, message: str):
        if self.valves.debug:
            print(f"⚡ EASYTRANS: {message}", file=sys.stderr, flush=True)

    class UserWrapper:
        """Utility class to wrap user data for internal API calls"""

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
        """Internal translation engine using the selected or current model"""
        selected_model = (
            self.valves.translation_model if self.valves.translation_model else model_id
        )
        user_object = self.UserWrapper(__user__)

        payload = {
            "model": selected_model,
            "messages": [
                {
                    "role": "user",
                    "content": f"ACT AS A TRANSLATOR. Target Language: {target_lang}. Output ONLY translated text. Text: {text}",
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
        """Handles incoming user messages, detects keywords and translates if necessary"""
        messages = body.get("messages", [])
        if not messages:
            return body

        content = messages[-1].get("content", "")
        # Updated Parser: tr: (overwrite) or trc: (bridge chat)
        match = re.match(
            r"^(tr|trc)(?:[-/]([a-zA-Z]{2,}))?\:\s*(.*)",
            content,
            re.IGNORECASE | re.DOTALL,
        )

        if not match:
            return body

        prefix, lang_code, original_text = (
            match.group(1).lower(),
            match.group(2),
            match.group(3).strip(),
        )
        if not original_text:
            return body
        target_lang = lang_code if lang_code else self.valves.default_lang

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"Translating input to {target_lang}...",
                        "done": False,
                    },
                }
            )

        # Perform input translation
        translated_input, in_stats = await self._translate(
            original_text, target_lang, body.get("model", ""), __request__, __user__
        )
        body["stream"] = False
        # Store session data for the Outlet phase
        user_id = __user__.get("id", "default")
        self.memory[user_id] = {
            "mode": prefix,
            "original_user_text": content,
            "translated_input": translated_input,
            "in_stats": in_stats,
        }

        # Determine strategy: silent dot for 'tr', translated text for 'trc'
        if prefix == "tr":
            messages[-1]["content"] = "Respond with one single dot."
        else:
            messages[-1]["content"] = translated_input

        if __event_emitter__:
            # Removed 'done: True' to keep the status chain alive for the next step
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"Input processed. Waiting for Model...",
                        "done": False,
                    },
                }
            )

        return body

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __request__=None,
        __event_emitter__=None,
    ) -> dict:
        """Handles model response, restores history and performs back-translation"""
        user_id = __user__.get("id", "default")
        if user_id not in self.memory:
            return body

        mem = self.memory.pop(user_id)
        mode, original_user_text, translated_input, in_stats = (
            mem["mode"],
            mem["original_user_text"],
            mem["translated_input"],
            mem["in_stats"],
        )

        if "messages" not in body or not body["messages"]:
            return body

        # Restore original user prompt in chat history for 'trc' mode
        if mode == "trc" and len(body["messages"]) > 1:
            body["messages"][-2]["content"] = original_user_text

        assistant_msg = body["messages"][-1]

        # Finalizing output based on mode
        if mode == "tr":
            assistant_msg["content"] = translated_input
            status_msg = f"Done | Time: {in_stats['elapsed']:.2f}s | Speed: {in_stats['tps']:.1f} tok/s"

        elif mode == "trc" and self.valves.back_translation:
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": "Back-Translating response...",
                            "done": False,
                        },
                    }
                )

            raw_response = assistant_msg["content"]
            # Translate foreign response back to Italian
            translated_back, out_stats = await self._translate(
                raw_response, "Italian", body.get("model", ""), __request__, __user__
            )
            assistant_msg["content"] = translated_back

            status_msg = f"Done | In: {in_stats['elapsed']:.1f}s | Out: {out_stats['elapsed']:.1f}s | Speed: {out_stats['tps']:.1f} tok/s"
        else:
            status_msg = f"Done | Input Speed: {in_stats['tps']:.1f} tok/s"

        if __event_emitter__:
            await __event_emitter__(
                {"type": "status", "data": {"description": status_msg, "done": True}}
            )

        return body
