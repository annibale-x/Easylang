"""
Title: 🚀 EasyLang: Open WebUI Translation Assistant
Version: 0.8.9
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
        # Persistent session-based storage for language pointers and state telemetry
        self.valves = self.Valves()
        self.memory, self.root_lan, self.chat_targets, self._id_cache = {}, {}, {}, {}

    def _dbg(self, message: str):
        # Standard error logging for Docker container monitoring
        if self.valves.debug:
            print(f"⚡ EASYLANG: {message}", file=sys.stderr, flush=True)

    def _get_chat_id(self, body: dict, user_id: str) -> str:
        # Retrieves unique chat identifier with internal fallback cache
        cid = body.get("metadata", {}).get("chat_id") or body.get("chat_id")
        if cid:
            self._id_cache[user_id] = cid
        return self._id_cache.get(user_id, "unknown")

    def _get_bl(self, user_id: str) -> Optional[str]:
        # Getter for the Base Language (User's home language)
        return self.root_lan.get(user_id)

    def _get_tl(self, user_id: str) -> str:
        # Getter for the Target Language (Destination language), defaults to 'en'
        return self.chat_targets.get(user_id, "en")

    class UserWrapper:
        # Pydantic-compatible wrapper to simulate authorized user object in sub-calls
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
        # Orchestrates hidden LLM calls for background tasks (Translation/Detection)
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
                # Accumulate token usage across the entire sub-call pipeline
                if user_id in self.memory:
                    self.memory[user_id]["total_tokens"] += response.get(
                        "usage", {}
                    ).get("total_tokens", 0)

                content = response["choices"][0]["message"]["content"].strip()
                
                # Surgical removal of reasoning artifacts and internal XML tags
                content = re.sub(
                    r"<think>.*?</think>", "", content, flags=re.DOTALL
                ).strip()
                content = re.sub(r"</?text>", "", content).strip()

                # Optimized parsing for ISO 639-1 code extraction
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
        # Main interceptor: Processes user input before it reaches the core LLM
        messages = body.get("messages", [])
        if not messages or not __user__:
            return body
        user_id = __user__.get("id", "default")
        current_model = body.get("model", "")
        content = messages[-1].get("content", "").strip()

        # Handle system dashboard query
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
            messages[-1]["content"] = "."
            return body

        # Primary command parsing via Regex (supports tr/trc and ISO bypass)
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

        # Contextual history scraping if no text is provided
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

        # Target Language Resolution: Priority given to ISO bypass
        if lang_code and len(lang_code) == 2:
            target_lang = lang_code.lower()
            self.chat_targets[user_id] = target_lang
        else:
            # Dynamic detection fallback
            det_sys = "Respond immediately without thinking. Identify ISO 639-1 code. 2-letter code ONLY."
            detected_lang = await self._get_llm_response(
                f"Detect: {source_text[:100]}",
                current_model,
                __request__,
                __user__,
                user_id,
                det_sys,
            )

            bl, tl = self._get_bl(user_id), self._get_tl(user_id)
            if not bl:
                bl = detected_lang
                self.root_lan[user_id] = bl
            target_lang = bl if detected_lang == tl else tl

        # Execute translation task with Anti-CoT directives
        trans_sys = f"You are a professional translator into ISO:{target_lang}. Respond immediately WITHOUT THINKING. Respond ONLY with the translation of the text inside <text> tags."
        translated_text = await self._get_llm_response(
            f"<text>{source_text}</text>",
            current_model,
            __request__,
            __user__,
            user_id,
            trans_sys,
        )

        # Update telemetry state and adjust stream settings based on operational mode
        self.memory[user_id].update(
            {
                "mode": prefix,
                "original_user_text": content,
                "translated_input": translated_text,
            }
        )
        body["stream"] = (
            False if (prefix == "tr" or self.valves.back_translation) else True
        )
        messages[-1]["content"] = (
            f"ACT AS TECHNICAL ASSISTANT. ANSWER IN {target_lang}: {translated_text}"
            if prefix == "trc"
            else "."
        )
        return body

    async def outlet(
        self,
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __request__=None,
        __event_emitter__=None,
    ) -> dict:
        # Final stage: Post-processes LLM output and handles back-translation/telemetry
        user_id = (__user__ or {}).get("id", "default")
        if user_id not in self.memory:
            return body
        mem = self.memory.pop(user_id)
        assistant_msg = body["messages"][-1]

        # Intercept service-level responses (Help/Errors)
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

        # Final token aggregation
        mem["total_tokens"] += body.get("usage", {}).get("total_tokens", 0)
        
        # History cleanup: Restores original prompt for trc mode
        if mem["mode"] == "trc" and len(body["messages"]) > 1:
            body["messages"][-2]["content"] = mem["original_user_text"]

        # Handling UI output based on mode (Direct Translation vs Back-Translation)
        if mem["mode"] == "tr":
            assistant_msg["content"] = mem["translated_input"]
        elif mem["mode"] == "trc" and self.valves.back_translation:
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

        # Telemetry calculation and UI status update
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
