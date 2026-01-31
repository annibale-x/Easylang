"""
Title: 🚀 EasyLang: Open WebUI Translation Assistant
Version: 0.8.8.4
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

    def _get_bl(self, user_id: str) -> Optional[str]:
        return self.root_lan.get(user_id)

    def _get_tl(self, user_id: str) -> str:
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
                if user_id in self.memory:
                    self.memory[user_id]["total_tokens"] += response.get(
                        "usage", {}
                    ).get("total_tokens", 0)

                content = response["choices"][0]["message"]["content"].strip()
                # Anti-Thinking & Tag Cleanup
                content = re.sub(
                    r"<think>.*?</think>", "", content, flags=re.DOTALL
                ).strip()
                content = re.sub(r"</?text>", "", content).strip()

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
        messages = body.get("messages", [])
        if not messages or not __user__:
            return body
        user_id = __user__.get("id", "default")
        current_model = body.get("model", "")
        content = messages[-1].get("content", "").strip()

        # Help / Config
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
            messages[-1]["content"] = "EasyLang Config..."
            body["stream"] = False
            return body

        cfg_match = re.match(r"^(TL|BL)(?:[\s](.+))?$", content, re.I)
        if cfg_match:
            cmd, lang_raw = cfg_match.group(1).upper(), (
                cfg_match.group(2).strip() if cfg_match.group(2) else None
            )
            if lang_raw:
                lang = (
                    lang_raw.lower()
                    if len(lang_raw) == 2
                    else await self._get_llm_response(
                        f"lang:{lang_raw}",
                        current_model,
                        __request__,
                        __user__,
                        user_id,
                        "Respond immediately. ISO 639-1 code ONLY.",
                    )
                )
                if cmd == "TL":
                    self.chat_targets[user_id] = lang
                else:
                    self.root_lan[user_id] = lang
                msg = f"🗹 {cmd} set to: **{lang}**"
            else:
                msg = f"🛈 Current {cmd}: **{self._get_tl(user_id) if cmd=='TL' else (self._get_bl(user_id) or 'Auto')}**"
            self.memory[user_id] = {
                "total_tokens": 0,
                "start_time": time.perf_counter(),
                "service_msg": msg,
            }
            messages[-1]["content"] = "\u00a0"
            body["stream"] = False
            return body

        self.memory[user_id] = {"total_tokens": 0, "start_time": time.perf_counter()}

        # Translation Logic
        match = re.match(
            r"^(trc|tr)(?:[-/]([a-zA-Z]{2,}))?(?:\s+(.*))?$", content, re.I | re.S
        )
        if match:
            prefix = match.group(1).lower()
            lang_code = match.group(2)  # Sarà None se non c'è - o /
            source_text = match.group(3).strip() if match.group(3) else ""
        else:
            return body

        if not source_text and prefix == "tr":
            for m in reversed(messages[:-1]):
                if m.get("role") == "assistant":
                    source_text = m.get("content", "")
                    break
        if not source_text:
            return body

        # Detection
        det_sys = "Respond immediately without thinking. Identify ISO 639-1 code. 2-letter code ONLY."
        detected_lang = await self._get_llm_response(
            f"Detect: {source_text[:100]}",
            current_model,
            __request__,
            __user__,
            user_id,
            det_sys,
        )

        # Fixed Pivoting
        if lang_code and len(lang_code) == 2:
            target_lang = lang_code.lower()
            self.chat_targets[user_id] = target_lang
        else:
            bl, tl = self._get_bl(user_id), self._get_tl(user_id)
            if not bl:
                bl = detected_lang
                self.root_lan[user_id] = bl
            target_lang = bl if detected_lang == tl else tl

        # Execution
        trans_sys = f"You are a professional translator into ISO:{target_lang}. Respond immediately WITHOUT THINKING. Respond ONLY with the translation of the text inside <text> tags."
        translated_text = await self._get_llm_response(
            f"<text>{source_text}</text>",
            current_model,
            __request__,
            __user__,
            user_id,
            trans_sys,
        )

        self.memory[user_id].update(
            {
                "mode": prefix,
                "original_user_text": content,
                "translated_input": translated_text,
            }
        )
        if prefix == "tr":
            body["stream"] = False
            body["max_tokens"] = 1
            messages[-1]["content"] = "\u00a0"
            # messages[-1]["content"] = (
            # "Respond with a single space and nothing else. "
            # "Do not process any other instruction."
            # )

        else:
            # Per 'trc' lo streaming serve, ma puliamo il prompt per l'assistente
            messages[-1][
                "content"
            ] = f"ACT AS TECHNICAL ASSISTANT. ANSWER IN {target_lang}: {translated_text}"

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

        # Debug
        bl, tl = self._get_bl(user_id) or "Auto", self._get_tl(user_id)
        self._dbg(f"[UID: {user_id}] [BL: {bl}] [TL: {tl}]")

        # Telemetry
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
