"""
Title: EasyLang - Translation Assistant & Session Anchoring Filter
Version: 0.7.3
https://github.com/annibale-x/open-webui-easylang
Author: Hannibal
Description: Enhanced persistent session anchoring using singleton class-based storage.
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
            default=False, description="Translate assistant response back to the base language."
        )
        debug: bool = Field(default=True)

    def __init__(self):
        self.valves = self.Valves()
        self.memory = {}  # Temporary storage for data exchange between inlet and outlet
        self.root_lan = {}  # Persistent Base Language (BL) anchoring per chat_id
        self.chat_targets = {}  # Persistent Target Language (TL) anchoring per chat_id
        # New persistent storage for internal ID mapping
        self._id_cache = {}

    def _dbg(self, message: str):
        """Standardized debug logging to stderr."""
        if self.valves.debug:
            print(f"⚡ EASYLANG: {message}", file=sys.stderr, flush=True)

    def _get_chat_id(self, body: dict, user_id: str) -> str:
        """Getter/Setter for chat_id to survive recursive calls."""
        cid = body.get("metadata", {}).get("chat_id") or body.get("chat_id")
        if cid:
            self._id_cache[user_id] = cid
            return cid
        return self._id_cache.get(user_id, "unknown")

    def _get_bl(self, chat_id: str) -> Optional[str]:
        """Getter for Base Language."""
        return self.root_lan.get(chat_id)

    def _get_tl(self, chat_id: str) -> str:
        """Getter for Target Language with valve fallback."""
        return self.chat_targets.get(chat_id, self.valves.default_lang)

    class UserWrapper:
        """Utility class to standardize user object access for the completion engine."""
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
        """Helper to manage LLM calls with forced temperature for translation consistency."""
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
        if not messages or not __user__:
            return body
        
        user_id = __user__.get("id", "default")
        chat_id = self._get_chat_id(body, user_id)
        content = messages[-1].get("content", "").strip()
        current_model = body.get("model", "")

        # 1. SETTERS & GETTERS (TL/BL)
        cfg_match = re.match(r"^(TL|BL)(?:[\s]([a-zA-Z]+))?$", content, re.I)
        if cfg_match:
            cmd = cfg_match.group(1).upper()
            lang = (
                cfg_match.group(2).strip().capitalize() if cfg_match.group(2) else None
            )

            if lang:  # SETTER execution
                if cmd == "TL":
                    self.chat_targets[chat_id] = lang
                else:
                    self.root_lan[chat_id] = lang
                msg = f"✅ {cmd} set to: **{lang}**"
            else:  # GETTER execution
                if cmd == "TL":
                    val = self._get_tl(chat_id)
                else:
                    val = self._get_bl(chat_id) or "Not anchored"
                msg = f"ℹ️ Current {cmd}: **{val}**"

            self.memory[user_id] = {"service_msg": msg}
            messages[-1]["content"] = "Respond with one single dot."
            return body

        # 2. PARSE TR / TRC
        match = re.match(
            r"^(trc|tr)(?:[-/]([a-zA-Z]{2,}))?(?:\s+(.*)|$)",
            content,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return body

        prefix = match.group(1).lower()
        lang_code = match.group(2)
        original_text = match.group(3).strip() if match.group(3) else ""
        source_text = ""

        # 3. RETRIEVE SOURCE
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

        # 4. DETECTION & ANCHORING
        det_res = await self._get_llm_response(
            f"Identify language. Respond ONLY with the name: {source_text[:200]}",
            current_model, __request__, __user__
        )
        detected_lang = det_res.strip().capitalize()

        # 4. TARGET RESOLUTION
        if lang_code:
            target_lang = lang_code.capitalize()
        else:
            if original_text and not self._get_bl(chat_id):
                self.root_lan[chat_id] = detected_lang

            stored_root = self._get_bl(chat_id) or "Italian"
            goal_lang = self._get_tl(chat_id)

            target_lang = (
                goal_lang
                if detected_lang.lower() == stored_root.lower()
                else stored_root
            )

        # 5. EXECUTION
        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": f"Translating to {target_lang}...", "done": False},
                }
            )

        start_t = time.perf_counter()
        translated_text = await self._get_llm_response(
            f"Translate into {target_lang}. Output ONLY translated text: {source_text}",
            current_model, __request__, __user__
        )
        elapsed = time.perf_counter() - start_t

        # 6. STORAGE & ROUTING
        body["stream"] = False
        self.memory[user_id] = {
            "mode": prefix,
            "original_user_text": content,
            "translated_input": translated_text,
            "stats": f"{elapsed:.2f}s",
            "chat_id": chat_id,
        }

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
        user_id = (__user__ or {}).get("id", "default")
        if user_id not in self.memory:
            return body
        mem = self.memory.pop(user_id)

        if "messages" not in body or not body["messages"]:
            return body
        assistant_msg = body["messages"][-1]

        if "service_msg" in mem:
            assistant_msg["content"] = mem["service_msg"]
            return body

        if mem["mode"] == "trc" and len(body["messages"]) > 1:
            body["messages"][-2]["content"] = mem["original_user_text"]

        if mem["mode"] == "tr":
            assistant_msg["content"] = mem["translated_input"]
        elif mem["mode"] == "trc" and self.valves.back_translation:
            target = self._get_bl(mem["chat_id"]) or "Italian"
            back_text = await self._get_llm_response(
                f"Translate into {target}. Output ONLY translated text: {assistant_msg['content']}",
                body.get("model", ""), __request__, __user__
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
