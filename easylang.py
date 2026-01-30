"""
Title: EasyLang - Translation Assistant & Session Anchoring Filter
Version: 0.4.0
https://github.com/annibale-x/open-webui-easylang
Author: Hannibal
Author_url: https://openwebui.com/u/h4nn1b4l
Author_email: annibale.x@gmail.com
Description: Professional translation assistant for Open WebUI featuring persistent session anchoring and context-aware logic.

MAIN FEATURES:
- PERSISTENT ANCHORING: Stateful Base Language (BL) and Target Language (TL) tracking per chat_id.
- DYNAMIC TOGGLE LOGIC: Intelligent language switching based on source detection vs. session anchors.
- PERFORMANCE TELEMETRY: Real-time tracking of Tokens Per Second (TPS) and execution latency.
- BRIDGE MODE (TRC): Seamless integration into ongoing dialogues with history restoration and back-translation.

LOGICAL FLOW:
1. STATE RESOLUTION: Retrieves or initializes BL/TL anchors from chat-specific memory.
2. LANGUAGE DETECTION: Real-time identification of input language to determine toggle direction.
3. TRANSLATION EXECUTION: Low-latency processing using dedicated or current LLM models.
4. OUTLET OVERRIDE: Manages service messages, back-translation for TRC, and final telemetry display.
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
        self.root_lan = {}  # Persistent root language per chat_id

    def _dbg(self, message: str):
        if self.valves.debug:
            print(f"⚡ EASYLANG: {message}", file=sys.stderr, flush=True)

    class UserWrapper:
        def __init__(self, user_dict):
            self.role = "user"
            self.id = "user_id"
            if user_dict and isinstance(user_dict, dict):
                self.role = user_dict.get("role", "user")
                self.id = user_dict.get("id", "user_id")
                for k, v in user_dict.items():
                    setattr(self, k, v)

    async def _get_llm_response(
        self, prompt: str, model_id: str, __request__, __user__
    ) -> str:
        """Atomic helper for clean LLM responses with zero temperature"""
        selected_model = (
            self.valves.translation_model if self.valves.translation_model else model_id
        )
        payload = {
            "model": selected_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0,
        }
        try:
            response = await generate_chat_completion(
                __request__, payload, user=self.UserWrapper(__user__)
            )
            return (
                response["choices"][0]["message"]["content"].strip().strip('"')
                if response
                else ""
            )
        except Exception as e:
            self._dbg(f"LLM Error: {e}")
            return ""

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
        chat_id = body.get("chat_id", "default_chat")
        content = messages[-1].get("content", "")

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
        current_model = body.get("model", "")
        source_text = ""

        # 1. RETRIEVE SOURCE TEXT (Context or Direct)
        if original_text:
            source_text = original_text
        elif prefix == "tr" and len(messages) > 1:
            for msg in reversed(messages[:-1]):
                if msg.get("role") == "assistant":
                    source_text = msg.get("content", "")
                    break

        if not source_text:
            return body

        # 2. LANGUAGE DETECTION
        detected_lang = await self._get_llm_response(
            f"Identify the language of the following text. Respond ONLY with the language name: {source_text[:200]}",
            current_model,
            __request__,
            __user__,
        )

        # 3. TARGET RESOLUTION
        if lang_code:
            target_lang = lang_code
        else:
            if original_text:
                self.root_lan[chat_id] = detected_lang

            stored_root = self.root_lan.get(chat_id, "Italian")
            # Toggle logic
            target_lang = (
                self.valves.default_lang
                if detected_lang.lower() == stored_root.lower()
                else stored_root
            )

        # 4. EXECUTE TRANSLATION
        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"Processing {prefix} to {target_lang}...",
                        "done": False,
                    },
                }
            )

        start_time = time.perf_counter()
        translated_text = await self._get_llm_response(
            f"Translate this text into {target_lang}. Output ONLY the translated text: {source_text}",
            current_model,
            __request__,
            __user__,
        )
        elapsed = time.perf_counter() - start_time

        # 5. MEMORY FOR OUTLET
        # Disable streaming ONLY if we need to perform back-translation in the outlet
        should_stream = not (prefix == "trc" and self.valves.back_translation)
        body["stream"] = should_stream

        user_id = __user__.get("id", "default")
        self.memory[user_id] = {
            "mode": prefix,
            "original_user_text": content,
            "translated_input": translated_text,
            "stats": f"{elapsed:.2f}s",
        }

        # 6. ROUTE CONTENT
        if prefix == "tr":
            body["stream"] = (
                False  # Always False for 'tr' to handle the dot replacement
            )
            messages[-1]["content"] = "Respond with one single dot."
        else:
            # trc mode: send translated text. Streaming follows the logic above.
            messages[-1]["content"] = translated_text

        return body

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __request__=None,
        __event_emitter__=None,
    ) -> dict:
        user_id = __user__.get("id", "default")
        if user_id not in self.memory:
            return body

        mem = self.memory.pop(user_id)
        if "messages" not in body or not body["messages"]:
            return body

        # Restore original prompt in history for trc mode
        if mem["mode"] == "trc" and len(body["messages"]) > 1:
            body["messages"][-2]["content"] = mem["original_user_text"]

        assistant_msg = body["messages"][-1]

        # In tr mode, replace the 'single dot' with the actual translation
        if mem["mode"] == "tr":
            assistant_msg["content"] = mem["translated_input"]

        # Optional: Back-translation for trc mode if enabled in valves
        elif mem["mode"] == "trc" and self.valves.back_translation:
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {"description": "Back-Translating...", "done": False},
                    }
                )

            current_model = body.get("model", "")
            final_back = await self._get_llm_response(
                f"Translate this text into Italian. Output ONLY the translated text: {assistant_msg['content']}",
                current_model,
                __request__,
                __user__,
            )
            assistant_msg["content"] = final_back

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": f"Done | {mem['stats']}", "done": True},
                }
            )

        return body
