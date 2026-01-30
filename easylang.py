"""
Title: 🚀 EasyLang: Open WebUI Translation Assistant
Version: 0.8.4
https://github.com/annibale-x/Easylang
Author: Hannibal
Author_url: https://openwebui.com/u/h4nn1b4l
Author_email: annibale.x@gmail.com
Core Purpose:
EasyLang is a high-performance translation middleware designed for Open WebUI.
It acts as an intelligent interceptor that manages multi-language workflows
between the User and the LLM, enabling seamless translation, context-aware
anchoring, and real-time performance telemetry.

================================================================================

[ MAIN FEATURES ]

* Easy Translation (tr/trc): Direct translation or interactive chat
    continuation with automatic context recovery.
* ISO 639-1 Dictionary: Dynamic resolution of language names (e.g., "italiano", 
    "german") into standard 2-letter codes via intermediate LLM calls.
* Smart Language Anchoring: Dynamically detects and sets Base (BL) and
    Target (TL) languages with internal ISO-centric fallback (default: 'en').
* Dynamic Stream Control: Smart bypass that enables native streaming for 'trc'
    mode while maintaining interceptor control for 'tr' and back-translation.
* Real-Time Status Emission: Active UI feedback through event emitters tracking
    every pipeline stage (Detection, Resolution, Translation).
* Performance Telemetry: Precise calculation of latency (seconds),
    token consumption, and processing speed (Tk/s).
* Back-Translation Support: Optional verification loop to translate LLM
    responses back to the user's native tongue (BL).

[ LOGICAL WORKFLOW (FULL CYCLE) ]

1.  INLET STAGE (User Request Interception):
    a. IDENTIFICATION: Extract User ID, Chat ID, and Model context.
    b. COMMAND PARSING: Regex routing for 'tr', 'trc', 'TL/BL' or help 't?'.
    c. ISO RESOLUTION: LLM-driven conversion of user language input to ISO 639-1.
    d. CONTEXT RECOVERY: If 'tr' is empty, scrape the last assistant message.
    e. DETECTION CALL: Zero-temp LLM execution to identify source ISO code.
    f. LOGICAL PIVOTING: Dynamic swap between BL and TL based on detected input.
    g. TRANSLATION CALL: isolated LLM call to map source text to target ISO.
    h. STREAM OPTIMIZATION: Conditionally enable/disable streaming based on mode.

2.  LLM EXECUTION:
    - The main model processes the injected system prompt in the target language.

3.  OUTLET STAGE (Response Refinement):
    a. METRICS AGGREGATION: Collect token usage and compute processing speed.
    b. UI POLISHING: Restore original user text in history for 'trc' mode.
    c. BACK-TRANSLATION (Optional): Recursive LLM loop to return response in BL.
    d. TELEMETRY OUTPUT: Emit final status event with Time, Tokens, and Speed.
    e. CLEANUP: Clear volatile memory and release the thread.

================================================================================
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
        back_translation: bool = Field(
            default=False, description="Translate assistant response back."
        )
        debug: bool = Field(
            default=True, description="Enable detailed state dumps in logs."
        )

    def __init__(self):
        self.valves = self.Valves()
        self.memory, self.root_lan, self.chat_targets, self._id_cache = {}, {}, {}, {}

    def _dbg(self, message: str):
        if self.valves.debug:
            print(f"⚡ EASYLANG: {message}", file=sys.stderr, flush=True)

    def _get_chat_id(self, body: dict, user_id: str) -> str:
        cid = body.get("metadata", {}).get("chat_id") or body.get("chat_id")
        if cid:
            self._id_cache[user_id] = cid
        return self._id_cache.get(user_id, "unknown")

    def _get_bl(self, user_id: str) -> Optional[str]:
        return self.root_lan.get(user_id)

    def _get_tl(self, user_id: str) -> str:
        # Internal ISO fallback to ensure logic consistency (en vs English)
        return self.chat_targets.get(user_id, "en")

    class UserWrapper:
        def __init__(self, user_dict):
            self.role, self.id = "user", "user_id"
            if user_dict and isinstance(user_dict, dict):
                for k, v in user_dict.items():
                    setattr(self, k, v)

    async def _get_llm_response(
        self,
        prompt: str,
        model_id: str,
        __request__,
        __user__,
        user_id: str,
        system_instruction: str = "",
    ) -> str:
        selected_model = (
            self.valves.translation_model if self.valves.translation_model else model_id
        )
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": selected_model,
            "messages": messages,
            "stream": False,
            "temperature": 0,
        }
        try:
            response = await generate_chat_completion(
                __request__, payload, user=self.UserWrapper(__user__)
            )
            if response:
                usage = response.get("usage", {})
                if user_id in self.memory:
                    self.memory[user_id]["total_tokens"] += usage.get("total_tokens", 0)
                # Pro-tip: normalization is key for reliable ISO matching
                return (
                    response["choices"][0]["message"]["content"]
                    .strip()
                    .strip('"')
                    .lower()
                )
            return ""
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
        current_model = body.get("model", "")
        content = messages[-1].get("content", "").strip()

        self._dbg(
            f"UID: {user_id} | CID: {chat_id} | BL: {self._get_bl(user_id)} | TL: {self._get_tl(user_id)}"
        )

        if content.lower() == "t?" or re.match(r"^(TL|BL)", content, re.I):
            self.memory[user_id] = {
                "total_tokens": 0,
                "start_time": time.perf_counter(),
            }

        if content.lower() == "t?":
            bl, tl = self._get_bl(user_id) or "Not yet defined (Auto)", self._get_tl(
                user_id
            )
            help_msg = (
                f"### 🌐 EasyLang Helper\n"
                f"**Current Status:**\n"
                f"* **BL**: `{bl}`\n"
                f"          ↓\n"
                f"* **TL**: `{tl}`\n\n"
                f"**Commands:**\n"
                f"* `tr <text>`: Toggle translate (BL ↔ TL).\n"
                f"* `tl <lang>` / `bl <lang>`: Manual configuration."
            )
            self.memory[user_id]["service_msg"] = help_msg
            messages[-1]["content"] = "Respond with one single dot."
            return body

        cfg_match = re.match(r"^(TL|BL)(?:[\s](.+))?$", content, re.I)
        if cfg_match:
            cmd, lang_raw = cfg_match.group(1).upper(), (
                cfg_match.group(2).strip() if cfg_match.group(2) else None
            )
            cmd_string = "Target Language" if cmd == "TL" else "Base Language"
            if lang_raw:
                # Resolve language name to ISO 639-1 via LLM dictionary
                if __event_emitter__:
                    await __event_emitter__({"type": "status", "data": {"description": f"Resolving {cmd_string} to ISO...", "done": False}})
                
                iso_sys = "Act as data dictionary. Give ONLY the ISO 639-1 code for language:<lang>"
                lang = await self._get_llm_response(
                    f"language:{lang_raw}",
                    current_model,
                    __request__,
                    __user__,
                    user_id,
                    iso_sys,
                )
                if cmd == "TL":
                    self.chat_targets[user_id] = lang
                else:
                    self.root_lan[user_id] = lang
                msg = f"🗹 {cmd_string} set to: **{lang}**"
            else:
                val = (
                    self._get_tl(user_id)
                    if cmd == "TL"
                    else (self._get_bl(user_id) or "Not anchored")
                )
                msg = f"🛈 Current {cmd_string}: **{val}**"
            self.memory[user_id]["service_msg"] = msg
            messages[-1]["content"] = "Respond with one single dot."
            return body

        match = re.match(
            r"^(trc|tr)(?:[-/]([a-zA-Z]{2,}))?(?:\s+(.*)|$)", content, re.I | re.DOTALL
        )
        if not match:
            return body

        self.memory[user_id] = {"total_tokens": 0, "start_time": time.perf_counter()}
        prefix, lang_code, source_text = (
            match.group(1).lower(),
            match.group(2),
            (match.group(3).strip() if match.group(3) else ""),
        )
        
        # Scrape context if tr is empty
        if not source_text and prefix == "tr":
            for msg in reversed(messages[:-1]):
                if msg.get("role") == "assistant":
                    source_text = msg.get("content", "")
                    break
        if not source_text:
            self.memory[user_id]["service_msg"] = "⚠️ **EasyLang: No context found.**"
            messages[-1]["content"] = "."
            return body

        # Perform Zero-Shot Language Detection
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Detecting source language...", "done": False}})

        det_sys = "Identify the language of the text. Respond ONLY with the ISO 639-1 code (e.g. 'it', 'en', 'de')."
        detected_lang = await self._get_llm_response(
            f"Detect language: {source_text[:100]}",
            current_model,
            __request__,
            __user__,
            user_id,
            det_sys,
        )
        current_tl = self._get_tl(user_id)

        if lang_code:
            # Force target via command suffix
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": f"Resolving forced language: {lang_code}...", "done": False}})
            
            iso_sys = "Act as data dictionary. Give ONLY the ISO 639-1 code for language:<lang>"
            target_lang = await self._get_llm_response(
                f"language:{lang_code}",
                current_model,
                __request__,
                __user__,
                user_id,
                iso_sys,
            )
            self.chat_targets[user_id] = target_lang
        else:
            # Logical pivoting between BL and TL
            stored_bl = self._get_bl(user_id)
            if not stored_bl:
                self.root_lan[user_id] = detected_lang
                stored_bl = detected_lang
            if detected_lang == current_tl:
                target_lang = stored_bl
            elif detected_lang == stored_bl:
                target_lang = current_tl
            else:
                self.root_lan[user_id] = detected_lang
                target_lang = current_tl

        # Core Translation Block
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": f"Translating to {target_lang}...", "done": False}})

        trans_sys = f"You are a professional translator into ISO:{target_lang}. Respond ONLY with the translated text."
        translated_text = await self._get_llm_response(
            source_text, current_model, __request__, __user__, user_id, trans_sys
        )

        self.memory[user_id].update(
            {
                "mode": prefix,
                "original_user_text": content,
                "translated_input": translated_text,
                "chat_id": chat_id,
            }
        )
        
        # Dynamic Stream Handling: enabled only for standard trc without back-translation
        body["stream"] = (
            False if (prefix == "tr" or self.valves.back_translation) else True
        )
        
        messages[-1]["content"] = (
            f"ACT AS TECHNICAL ASSISTANT. ANSWER IN {target_lang} TO THIS REQUEST: {translated_text}"
            if prefix == "trc"
            else "Respond with one single dot."
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
        assistant_msg = body["messages"][-1]

        # Finalize service messages (help/status)
        if "service_msg" in mem:
            assistant_msg["content"] = mem["service_msg"]
            elapsed = time.perf_counter() - mem["start_time"]
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {"description": f"Done | {elapsed:.2f}s", "done": True},
                    }
                )
            return body

        mem["total_tokens"] += body.get("usage", {}).get("total_tokens", 0)

        # Restore UI history for continuity
        if mem["mode"] == "trc" and len(body["messages"]) > 1:
            body["messages"][-2]["content"] = mem["original_user_text"]

        if mem["mode"] == "tr":
            assistant_msg["content"] = mem["translated_input"]
        elif mem["mode"] == "trc" and self.valves.back_translation:
            # Back-translation loop for quality assurance
            target = self._get_bl(user_id) or "en"
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {"description": f"Back-translating response to {target}...", "done": False},
                    }
                )
            self.memory[user_id] = mem
            back_sys = f"Translate into {target}. Respond ONLY with translated text."
            back_text = await self._get_llm_response(
                assistant_msg["content"],
                body.get("model", ""),
                __request__,
                __user__,
                user_id,
                back_sys,
            )
            assistant_msg["content"] = back_text
            mem = self.memory.pop(user_id)

        # Emission of final performance telemetry
        elapsed = time.perf_counter() - mem["start_time"]
        total_tk = mem["total_tokens"]
        speed = round(total_tk / elapsed, 1) if elapsed > 0 else 0
        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"Done | {elapsed:.2f}s | {total_tk} tokens | {speed} Tk/s",
                        "done": True,
                    },
                }
            )
        return body
