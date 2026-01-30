"""
Title: 🚀 EasyLang: Open WebUI Translation Assistant 
Version: 0.8.1
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

* Surgical Translation (tr/trc): Direct translation or interactive chat 
    continuation with automatic context recovery.
* Smart Language Anchoring: Dynamically detects and sets Base (BL) and 
    Target (TL) languages based on user input patterns.
* Cross-Session Persistence: Tracks user preferences (BL/TL) and chat 
    history metadata using a lightweight in-memory state engine.
* Performance Telemetry: Precise calculation of latency (seconds), 
    token consumption, and processing speed (Tk/s).
* Gemma/Mistral Optimized: Uses strict system-level instructions for 
    intermediate LLM calls to prevent verbosity and ensure deterministic results.
* Back-Translation Support: Optional verification loop to translate LLM 
    responses back to the user's native tongue.

[ LOGICAL WORKFLOW (FULL CYCLE) ]

1.  INLET STAGE (User Request Interception):
    a. IDENTIFICATION: Extract User ID, Chat ID, and Model context.
    b. COMMAND PARSING: Regex-based routing for 'tr', 'trc', 'TL/BL' or help 't?'.
    c. CONTEXT RECOVERY: If 'tr' is empty, scrape the last assistant message.
    d. DETECTION CALL: Zero-temperature LLM execution to identify source language.
    e. STATE SYNC: Auto-update TL if a new language is detected (Surgical Fix 0.7.19).
    f. TRANSLATION CALL: Map source text to target language via isolated LLM call.
    g. INJECTION: Replace user input with a technical prompt or service message.

2.  LLM EXECUTION:
    - The main model processes the modified prompt (e.g., "ACT AS TECHNICAL ASSISTANT IN FRENCH...").

3.  OUTLET STAGE (Response Refinement):
    a. METRICS AGGREGATION: Collect token usage from the main model response.
    b. UI POLISHING: Restore original user text in history for 'trc' mode.
    c. BACK-TRANSLATION (Optional): Re-translate the output to the Base Language.
    d. TELEMETRY OUTPUT: Emit a status event with final Time, Tokens, and Speed.
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
        default_target_lang: str = Field(
            default="English", description="Fallback target language."
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
        return self.chat_targets.get(user_id, self.valves.default_target_lang)

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
                return response["choices"][0]["message"]["content"].strip().strip('"')
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

        # DEBUG LOGS (Docker logs)
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
                f"* **BL** (Base Language): `{bl}`\n"
                f"          ↓\n"
                f"* **TL** (Target Language): `{tl}`\n\n"
                f"**Commands:**\n"
                f"* `tr <text>`: Toggle translate (BL ↔ TL).\n"
                f"* `tr`: Translate last assistant message.\n"
                f"* `tr-<lang> <text>`: Force target and update TL.\n"
                f"* `trc <text>`: Translate and continue chat.\n"
                f"* `tl <lang>` / `bl <lang>`: Manual configuration.\n"
                f"* `tl` / `bl`: Show current setting."
            )
            self.memory[user_id]["service_msg"] = help_msg
            messages[-1]["content"] = "Respond with one single dot."
            return body

        cfg_match = re.match(r"^(TL|BL)(?:[\s]([a-zA-Z]+))?$", content, re.I)
        if cfg_match:
            cmd, lang = cfg_match.group(1).upper(), (
                cfg_match.group(2).strip().capitalize() if cfg_match.group(2) else None
            )
            if lang:
                if cmd == "TL":
                    self.chat_targets[user_id] = lang
                else:
                    self.root_lan[user_id] = lang
                msg = f"✅ {cmd} set to: **{lang}**"
            else:
                val = (
                    self._get_tl(user_id)
                    if cmd == "TL"
                    else (self._get_bl(user_id) or "Not anchored")
                )
                msg = f"ℹ️ Current {cmd}: **{val}**"
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
        if not source_text and prefix == "tr":
            for msg in reversed(messages[:-1]):
                if msg.get("role") == "assistant":
                    source_text = msg.get("content", "")
                    break
        if not source_text:
            self.memory[user_id]["service_msg"] = "⚠️ **EasyLang: No context found.**"
            messages[-1]["content"] = "."
            return body

        det_sys = "Identify language. Respond ONLY with the language name (e.g. 'Italian'). No other text."
        det_res = await self._get_llm_response(
            f"Detect language: {source_text[:100]}",
            current_model,
            __request__,
            __user__,
            user_id,
            det_sys,
        )
        detected_lang = det_res.split()[-1].strip(".").capitalize()
        current_tl = self._get_tl(user_id)

        if lang_code:
            target_lang = lang_code.capitalize()
            self.chat_targets[user_id] = target_lang
        else:
            # SURGICAL FIX: Update TL if user writes in a new foreign language
            stored_bl = self._get_bl(user_id)
            if source_text and stored_bl and detected_lang.lower() != stored_bl.lower():
                self.chat_targets[user_id] = detected_lang
            elif not stored_bl and detected_lang.lower() != current_tl.lower():
                self.root_lan[user_id] = detected_lang

            stored_root = self._get_bl(user_id) or detected_lang
            target_lang = (
                self._get_tl(user_id)
                if detected_lang.lower() == stored_root.lower()
                else stored_root
            )

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"Translating prompt to {target_lang}...",
                        "done": False,
                    },
                }
            )

        trans_sys = f"You are a professional translator into {target_lang}. Respond ONLY with translated text."
        translated_text = await self._get_llm_response(
            source_text, current_model, __request__, __user__, user_id, trans_sys
        )

        if prefix == "trc" and __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": f"Delivering request...", "done": False},
                }
            )

        self.memory[user_id].update(
            {
                "mode": prefix,
                "original_user_text": content,
                "translated_input": translated_text,
                "chat_id": chat_id,
            }
        )
        body["stream"] = False
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

        if mem["mode"] == "trc" and len(body["messages"]) > 1:
            body["messages"][-2]["content"] = mem["original_user_text"]

        if mem["mode"] == "tr":
            assistant_msg["content"] = mem["translated_input"]
        elif mem["mode"] == "trc" and self.valves.back_translation:
            target = self._get_bl(user_id) or self.valves.default_target_lang
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {"description": f"Back-translating...", "done": False},
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
