"""
Title: 🚀 EasyLang: Open WebUI Translation Assistant
Version: 0.8.11
https://github.com/annibale-x/Easylang
Author: Hannibal
Author_url: https://openwebui.com/u/h4nn1b4l
Author_email: annibale.x@gmail.com
Core Purpose:
EasyLang is a high-performance translation middleware designed for Open WebUI.
It acts as an intelligent interceptor that manages multi-language workflows
between the User and the LLM, enabling seamless translation, context-aware
anchoring, and real-time performance telemetry.
"""

import re
import sys
import time
from typing import Optional
from pydantic import BaseModel, Field
from open_webui.main import generate_chat_completion


class Filter:
    class Valves(BaseModel):
        # Configuration for the LLM used specifically for middleware tasks
        translation_model: str = Field(
            default="", description="Model for translation. Empty = current."
        )
        # Toggle for back-translation feature in 'trc' mode
        back_translation: bool = Field(
            default=False, description="Translate assistant response back."
        )
        # Debugging flag for stderr logging
        debug: bool = Field(
            default=True, description="Enable detailed state dumps in logs."
        )

    def __init__(self):
        self.valves = self.Valves()
        # memory: Stores telemetry and state during the inlet/outlet lifecycle
        # root_lan: Stores detected Base Language (BL) per user
        # chat_targets: Stores current Target Language (TL) per user
        # _id_cache: Temporary storage for chat sessions identification
        self.memory, self.root_lan, self.chat_targets, self._id_cache = {}, {}, {}, {}

    def _dbg(self, message: str):
        """Standardized debug logger for internal state tracking."""
        if self.valves.debug:
            print(f"⚡ EASYLANG: {message}", file=sys.stderr, flush=True)

    def _get_chat_id(self, body: dict, user_id: str) -> str:
        """Extracts unique chat identifier for session persistence."""
        cid = body.get("metadata", {}).get("chat_id") or body.get("chat_id")
        if cid:
            self._id_cache[user_id] = cid
        return self._id_cache.get(user_id, "unknown")

    def _get_bl(self, user_id: str) -> Optional[str]:
        """Retrieves the established Base Language (Native) for the user."""
        return self.root_lan.get(user_id)

    def _get_tl(self, user_id: str) -> str:
        """Retrieves current Target Language (Default to 'en' if not set)."""
        return self.chat_targets.get(user_id, "en")

    class UserWrapper:
        """Internal adapter to satisfy Open WebUI's user object expectations."""
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
        """
        Executes internal LLM calls for detection, ISO resolution, or translation.
        Includes automated token tracking and response sanitization.
        """
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
            "stream": False, # Sync execution for middleware logic
            "temperature": 0, # Strict deterministic output
        }
        try:
            response = await generate_chat_completion(
                __request__, payload, user=self.UserWrapper(__user__)
            )
            if response:
                # Accumulate tokens consumed by middleware operations
                if user_id in self.memory:
                    self.memory[user_id]["total_tokens"] += response.get(
                        "usage", {}
                    ).get("total_tokens", 0)

                content = response["choices"][0]["message"]["content"].strip()
                
                # Cleanup: removes Chain of Thought (thinking) and helper XML tags
                content = re.sub(
                    r"<think>.*?</think>", "", content, flags=re.DOTALL
                ).strip()
                content = re.sub(r"</?text>", "", content).strip()

                # ISO Logic: ensure only a 2-letter code is returned if in detection mode
                if (
                    "iso 639-1" in system_instruction.lower()
                    and "translate" not in system_instruction.lower()
                ):
                    match = re.search(r"\b([a-z]{2})\b", content.lower())
                    if match:
                        return match.group(1)

                return content.strip('"')
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
        """
        Pre-processing pipeline:
        1. Command parsing (tr/trc/t?)
        2. ISO language resolution and anchoring
        3. Translation task generation
        4. Stream control modification
        """
        messages = body.get("messages", [])
        if not messages or not __user__:
            return body
        user_id = __user__.get("id", "default")
        current_model = body.get("model", "")
        content = messages[-1].get("content", "").strip()

        # [HELPER INTERFACE]
        if content.lower() == "t?":
            bl, tl = self._get_bl(user_id) or "Auto", self._get_tl(user_id)
            help_msg = (
                f"### 🌐 EasyLang Helper\n"
                f"**Current Status:**\n"
                f"* **BL** (Base Language): `{bl}`\n"
                f"  ↓\n"
                f"* **TL** (Target Language): `{tl}`\n\n"
                f"**Commands:**\n"
                f"* `tr <text>`: Toggle translate (BL ↔ TL).\n"
                f"* `tr`: Translate last assistant message.\n"
                f"* `tr-<lang> <text>`: Force target and update TL.\n"
                f"* `trc <text>`: Translate and continue chat.\n"
                f"* `tl <lang>` / `bl <lang>`: Manual configuration."
            )
            self.memory[user_id] = {
                "total_tokens": 0,
                "start_time": time.perf_counter(),
                "service_msg": help_msg,
            }
            messages[-1]["content"] = "." # Placeholder to avoid UI error
            return body

        # [COMMAND PARSING]
        # Regex (v0.8.11): Handles prefix, optional ISO separator, and content
        # Strictly avoids collisions with natural text (e.g., "tr It works!")
        match = re.match(r"^(trc|tr)(?:([- /])([a-zA-Z]{2,}))?(?:\s+(.*))?$", content, re.I | re.S)
        if not match:
            return body

        # Initialize telemetry and session memory
        self.memory[user_id] = {"total_tokens": 0, "start_time": time.perf_counter()}
        prefix, lang_code, source_text = (
            match.group(1).lower(),
            match.group(2),
            (match.group(3).strip() if match.group(3) else ""),
        )

        # [CONTEXT RECOVERY]
        # Automatically pulls last Assistant content if command is issued without text
        if not source_text and prefix == "tr":
            for m in reversed(messages[:-1]):
                if m.get("role") == "assistant":
                    source_text = m.get("content", "")
                    break

        if not source_text:
            self.memory[user_id][
                "service_msg"
            ] = "⚠️ **EasyLang: No context found to translate.**"
            messages[-1]["content"] = "."
            return body

        # [TARGET LANGUAGE RESOLUTION]
        if lang_code and len(lang_code) == 2:
            # ISO Bypass: User explicitly defined the language (tr-en)
            target_lang = lang_code.lower()
            self.chat_targets[user_id] = target_lang
        else:
            # Auto-detection: Invoke LLM to identify source language
            det_sys = "Respond immediately without thinking. Identify ISO 639-1 code. 2-letter code ONLY."
            detected_lang = await self._get_llm_response(
                f"Detect: {source_text[:100]}",
                current_model,
                __request__,
                __user__,
                user_id,
                det_sys,
            )

            # Anchoring: Determine if we translate to TL or back to BL
            bl, tl = self._get_bl(user_id), self._get_tl(user_id)
            if not bl:
                bl = detected_lang
                self.root_lan[user_id] = bl
            target_lang = bl if detected_lang == tl else tl

        # [TRANSLATION EXECUTION]
        # Instruction Hardening: Force model to behave as a sterile API
        trans_sys = (
            f"You are a sterile translation engine for ISO:{target_lang}. "
            "DO NOT engage in conversation. DO NOT answer greetings. "
            "Respond ONLY with the direct translation of the text found inside <text> tags. "
            "Zero talk, just output the exact translation."
        )
        translated_text = await self._get_llm_response(
            f"<text>{source_text}</text>",
            current_model,
            __request__,
            __user__,
            user_id,
            trans_sys,
        )

        # Save session data for the outlet phase
        self.memory[user_id].update(
            {
                "mode": prefix,
                "original_user_text": content,
                "translated_input": translated_text,
            }
        )
        
        # Stream Control: Disable for surgical 'tr' or back-translation loops
        body["stream"] = (
            False if (prefix == "tr" or self.valves.back_translation) else True
        )
        
        # Modify final message: 'trc' continues conversation, 'tr' halts for injection
        messages[-1]["content"] = (
            f"ACT AS TECHNICAL ASSISTANT. ANSWER IN {target_lang}: {translated_text}"
            if prefix == "trc"
            else "."
        )
        return body

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __request__=None,
        __event_emitter__=None,
    ) -> dict:
        """
        Post-processing pipeline:
        1. Injects service messages (help/errors)
        2. Injects direct translations (tr)
        3. Restores original user UI context
        4. Handles back-translation and final telemetry emission
        """
        user_id = (__user__ or {}).get("id", "default")
        if user_id not in self.memory:
            return body
            
        mem = self.memory.pop(user_id)
        assistant_msg = body["messages"][-1]

        # [SERVICE MESSAGE INJECTION]
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

        # Aggregate total tokens from LLM completion
        mem["total_tokens"] += body.get("usage", {}).get("total_tokens", 0)
        
        # UI Context Restoration: Replaces the 'trc ...' command with original user text in UI history
        if mem["mode"] == "trc" and len(body["messages"]) > 1:
            body["messages"][-2]["content"] = mem["original_user_text"]

        # [TRANSLATION INJECTION]
        if mem["mode"] == "tr":
            # Direct surgical injection of translated text
            assistant_msg["content"] = mem["translated_input"]
        elif mem["mode"] == "trc" and self.valves.back_translation:
            # Back-Translation loop: Translates AI response back to user's native tongue
            target = self._get_bl(user_id) or "en"
            back_sys = f"Translate text inside <text> tags into ISO:{target}. Respond immediately WITHOUT THINKING. Respond ONLY with the translation."
            self.memory[user_id] = mem
            assistant_msg["content"] = await self._get_llm_response(
                f"<text>{assistant_msg['content']}</text>",
                body.get("model", ""),
                __request__,
                __user__,
                user_id,
                back_sys,
            )
            mem = self.memory.pop(user_id)

        # [TELEMETRY EMISSION]
        # Calculates latency and processing speed for the status bar
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
