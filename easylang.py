"""
title: EasyLang Filter
author: Hannibal
version: 0.6.1
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
        self.chat_targets = {}

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
        content = messages[-1].get("content", "").strip()
        user_id = __user__.get("id", "default")
        current_model = body.get("model", "")

        # 1. COMMAND tl:lang
        tl_match = re.match(r"^tl\:([a-zA-Z]+)", content, re.IGNORECASE)
        if tl_match:
            new_tl = tl_match.group(1).strip().capitalize()
            self.chat_targets[chat_id] = new_tl
            self.memory[user_id] = {
                "service_msg": f"✅ Target language set to: **{new_tl}**"
            }
            messages[-1]["content"] = "Respond with one single dot."
            return body

        # 2. PARSE tr/trc
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
        source_text = ""

        # 3. RETRIEVE SOURCE (Dalla 0.3.7)
        if original_text:
            source_text = original_text
        elif prefix == "tr" and len(messages) > 1:
            for msg in reversed(messages[:-1]):
                if msg.get("role") == "assistant":
                    source_text = msg.get("content", "")
                    break

        if not source_text:
            self.memory[user_id] = {"service_msg": "⚠️ **EasyLang: No context found.**"}
            messages[-1]["content"] = "Respond with one single dot."
            return body

        # 4. DETECTION & ANCHORING (Dalla 0.3.7)
        detected_lang = await self._get_llm_response(
            f"Identify the language of the following text. Respond ONLY with the language name: {source_text[:200]}",
            current_model,
            __request__,
            __user__,
        )

        # Logica di risoluzione target della 0.3.7
        if lang_code:
            target_lang = lang_code.capitalize()
        else:
            if original_text:
                self.root_lan[chat_id] = detected_lang  # ANCORAGGIO QUI

            stored_root = self.root_lan.get(chat_id, "Italian")
            # Toggle logic: se rileva BL, vai a Valve.lang, altrimenti torna a BL.
            target_lang = (
                self.valves.default_lang
                if detected_lang.lower() == stored_root.lower()
                else stored_root
            )

        # 5. EXECUTION
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

        start_t = time.perf_counter()
        translated_text = await self._get_llm_response(
            f"Translate this text into {target_lang}. Output ONLY the translated text: {source_text}",
            current_model,
            __request__,
            __user__,
        )
        elapsed = time.perf_counter() - start_t

        # 6. STORAGE & ROUTING
        body["stream"] = False  # Disabilitiamo stream per gestire outlet pulito
        self.memory[user_id] = {
            "mode": prefix,
            "original_user_text": content,
            "translated_input": translated_text,
            "stats": f"{elapsed:.2f}s",
            "chat_id": chat_id,
        }

        # Strategia: tr -> dot (overwrite), trc -> forward (bridge)
        messages[-1]["content"] = (
            "Respond with one single dot." if prefix == "tr" else translated_text
        )
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
        assistant_msg = body["messages"][-1]

        if "service_msg" in mem:
            assistant_msg["content"] = mem["service_msg"]
            return body

        # Ripristino prompt originale per trc
        if mem["mode"] == "trc" and len(body["messages"]) > 1:
            body["messages"][-2]["content"] = mem["original_user_text"]

        # Finalizzazione Output
        if mem["mode"] == "tr":
            assistant_msg["content"] = mem["translated_input"]
        elif mem["mode"] == "trc" and self.valves.back_translation:
            target = self.root_lan.get(mem["chat_id"], "Italian")
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": f"Back-Translating to {target}...",
                            "done": False,
                        },
                    }
                )

            back_text = await self._get_llm_response(
                f"Translate this text into {target}. Output ONLY the translated text: {assistant_msg['content']}",
                body.get("model", ""),
                __request__,
                __user__,
            )
            assistant_msg["content"] = back_text

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": f"Done | {mem['stats']}", "done": True},
                }
            )
        return body
