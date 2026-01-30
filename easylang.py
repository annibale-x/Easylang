"""
Title: EasyLang - Translation Assistant & Session Anchoring Filter
Version: 0.7.2
https://github.com/annibale-x/open-webui-easylang
Author: Hannibal
Author_url: https://openwebui.com/u/h4nn1b4l
Author_email: annibale.x@gmail.com
Description: Professional translation assistant for Open WebUI featuring persistent session anchoring and context-aware logic.

MAIN FEATURES:
- PERSISTENT ANCHORING: Stateful Base Language (BL) and Target Language (TL) tracking per chat_id.
- FLEXIBLE COMMAND PARSING: Regex-driven triggers (TR, TRC, TL, BL) with case-insensitive, colon-free syntax.
- DYNAMIC TOGGLE LOGIC: Intelligent language switching based on source detection vs. session anchors.
- PERFORMANCE TELEMETRY: Real-time tracking of Tokens Per Second (TPS) and execution latency.
- BRIDGE MODE (TRC): Seamless integration into ongoing dialogues with history restoration and back-translation.

LOGICAL FLOW:
1. INLET CAPTURE: Identifies commands (TR/TRC) and session management (TL/BL) via regex.
2. STATE RESOLUTION: Retrieves or initializes BL/TL anchors from chat-specific memory.
3. LANGUAGE DETECTION: Real-time identification of input language to determine toggle direction.
4. TRANSLATION EXECUTION: Low-latency processing using dedicated or current LLM models.
5. OUTLET OVERRIDE: Manages service messages, back-translation for TRC, and final telemetry display.
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

    def _dbg(self, message: str):
        """Standardized debug logging to stderr."""
        if self.valves.debug:
            print(f"⚡ EASYLANG: {message}", file=sys.stderr, flush=True)

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
        if not messages:
            return body
        
        chat_id = body.get("chat_id", "default_chat")
        content = messages[-1].get("content", "").strip()
        user_id = __user__.get("id", "default")
        current_model = body.get("model", "")

        # 1. SETTERS & GETTERS (TL/BL)
        # Matches commands like: TL, TL English, BL, BL Italian
        cfg_match = re.match(r"^(TL|BL)(?:\s+([a-zA-Z]+))?$", content, re.IGNORECASE)
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
                    val = self.chat_targets.get(chat_id, self.valves.default_lang)
                else:
                    val = self.root_lan.get(chat_id, "Not anchored")
                msg = f"ℹ️ Current {cmd}: **{val}**"

            # Use memory to trigger a service response in the outlet
            self.memory[user_id] = {"service_msg": msg}
            messages[-1]["content"] = "Respond with one single dot."
            return body

        # 2. PARSE TR / TRC (Colon-free regex)
        # Supports: "tr text", "tr-en text", "trc text", "tr" (for context lookup)
        match = re.match(
            r"^(trc|tr)(?:[-/]([a-zA-Z]{2,}))?(?:\s+(.*)|$)",
            content,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return body

        prefix = match.group(1).lower()
        lang_code = match.group(2)
        # Protect against NoneType: fallback to empty string if no text follows the command
        original_text = match.group(3).strip() if match.group(3) else ""

        # Initialize source_text to prevent UnboundLocalError
        source_text = ""

        # 3. RETRIEVE SOURCE
        # If explicit text is provided, use it; otherwise, look for the last assistant response
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
        # Determine the source language to apply the dynamic toggle logic
        detected_lang = await self._get_llm_response(
            f"Identify the language of the following text. Respond ONLY with the language name: {source_text[:200]}",
            current_model,
            __request__,
            __user__,
        )

        # 4. TARGET RESOLUTION (Prioritizes explicit lang_code > session setters > valves)
        if lang_code:
            target_lang = lang_code.capitalize()
        else:
            # Anchor the chat's base language if this is the first direct translation request
            if original_text and chat_id not in self.root_lan:
                self.root_lan[chat_id] = detected_lang

            stored_root = self.root_lan.get(chat_id, "Italian")

            # Resolve the intended target language
            goal_lang = self.chat_targets.get(chat_id, self.valves.default_lang)

            # Toggle logic:
            # If input is BL -> translate to TL (goal_lang)
            # If input is not BL -> translate back to BL (stored_root)
            target_lang = (
                goal_lang
                if detected_lang.lower() == stored_root.lower()
                else stored_root
            )

        # 5. EXECUTION
        # Notify the UI that processing has started
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
        # Disable streaming to allow outlet override and cleanup
        body["stream"] = False
        self.memory[user_id] = {
            "mode": prefix,
            "original_user_text": content,
            "translated_input": translated_text,
            "stats": f"{elapsed:.2f}s",
            "chat_id": chat_id,
        }

        # Strategy: overwrite user content with a dot for 'tr' mode to hide trigger logic
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

        # Handle service messages (getters/setters output)
        if "service_msg" in mem:
            assistant_msg["content"] = mem["service_msg"]
            return body

        # Restore the original user prompt in the history for TRC mode transparency
        if mem["mode"] == "trc" and len(body["messages"]) > 1:
            body["messages"][-2]["content"] = mem["original_user_text"]

        # Final output logic
        if mem["mode"] == "tr":
            assistant_msg["content"] = mem["translated_input"]
        elif mem["mode"] == "trc" and self.valves.back_translation:
            # Back-translation loop for bridge mode
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

        # Update final status with latency/telemetry
        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": f"Done | {mem['stats']}", "done": True},
                }
            )
        return body
