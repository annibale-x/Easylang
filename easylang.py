"""
title: 🚀 EasyLang: Open WebUI Translation Assistant
version: 0.2.7
repo_url: https://github.com/annibale-x/Easylang
author: Hannibal
author_url: https://openwebui.com/u/h4nn1b4l
author_email: annibale.x@gmail.com
license: MIT
description: Translation assistant for Open WebUI. Features smart bidirectional toggling, context-based summarization, and precision performance tracking.
"""

import asyncio
import re
import sys
import time
import json
from typing import Optional, Union
from pydantic import BaseModel, Field
from open_webui.main import generate_chat_completion  # type: ignore
from open_webui.models.users import UserModel  # type: ignore
from open_webui.models.chats import Chats  # type: ignore

version = "0.2.7"


class Filter:
    class Valves(BaseModel):
        target_language: str = Field(
            default="en", description="Initial target language. "
        )
        translation_model: str = Field(
            default="", description="Model for translation. Empty = current."
        )
        back_translation: bool = Field(
            default=False, description="Translate assistant response back."
        )
        debug: bool = Field(
            default=False, description="Enable detailed state dumps in logs."
        )

    def __init__(self):
        self.valves = self.Valves()
        self.ctx = {}
        self.RE_HELP = re.compile(r"^t\?$", re.I)
        self.RE_CONFIG = re.compile(r"^(TL|BL)(?:\:(.+))?\s*$", re.I)
        self.RE_TRANS = re.compile(
            r"^(TRS|TRC|TR)(?:\:([a-z]{2,10}))?(?:\s+(.*))?$", re.I | re.S
        )
        self.RE_ISO = re.compile(r"\b([a-z]{2})\b", re.I)

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __request__=None,
        __event_emitter__=None,
    ) -> dict:

        messages = body.get("messages", [])
        cid = body["metadata"]["chat_id"]
        if not messages or not __user__ or not cid:
            return body

        # FIX: Multimodal Text Extraction (v0.2.6)
        last_msg = messages[-1].get("content", "")
        if isinstance(last_msg, list):
            content = "\n".join(c["text"] for c in last_msg if c.get("type") == "text")
        else:
            content = str(last_msg)
        content = content.strip()

        cmd = ""
        ctx = self.ctx = {}
        dbg_str = ""

        if self.RE_HELP.match(content):
            cmd = "HELP"
        elif match := self.RE_CONFIG.match(content):
            cmd, lang = (
                match.group(1).upper(),
                (match.group(2).strip() if match.group(2) else None),
            )
            ctx["lang"] = lang
            dbg_str = f"Config command detected: {cmd} with parameter: {lang}"
        elif match := self.RE_TRANS.match(content):
            cmd = match.group(1).upper()
            lang = match.group(2) if match.group(2) else None
            text = match.group(3).strip() if match.group(3) else ""
            ctx["lang"] = lang
            ctx["text"] = text
            dbg_str = f"Translation command: {cmd} | Language Param: {lang} | Text Length: {len(text)}"
        else:
            return body

        # self._dmp(body, "INLET RAW BODY")

        bm = body.get("model", "")
        tm = self.valves.translation_model or bm
        ctx.update(
            {
                "t0": time.perf_counter(),
                "tk": 0,
                "cid": cid,
                "bm": bm,
                "tm": tm,
                "req": __request__,
                "user": UserModel(**__user__),
                "emitter": __event_emitter__,
                "uid": __user__.get("id", "default"),
                "cmd": cmd,
            }
        )

        self._dbg(
            f"\n\n 👉 --- INLET START | Chat ID: {ctx['cid']} | Command: {cmd} ---\n"
        )
        self._dbg(f"{dbg_str}")
        await self._get_state()
        self._dbg(f"Current Memory State -> Base: {ctx['bl']} | Target: {ctx['tl']}")

        if cmd == "HELP":
            ctx["msg"] = self._service_msg()
        elif cmd in ("BL", "TL"):
            lang = ctx.get("lang")
            lang_key = cmd.lower()
            if lang:
                new_lang = lang.strip().lower()
                curr_lang = ctx.get(lang_key)
                if new_lang != curr_lang:
                    ctx[lang_key] = new_lang
                    await self._set_state()
                    ctx["msg"] = (
                        f"🗹 Current {cmd} switched from **{curr_lang}** to **{new_lang}**"
                    )
            else:
                ctx["msg"] = f"🛈 Current {cmd}: **{ctx.get(lang_key)}**"

        elif cmd == "TRC":
            # TRC Logic remains in Inlet (Input Modifier)
            # But we use the robust text resolver
            text = await self._resolve_text(messages, cmd)
            if not text:
                return body

            # Execute Core Logic
            translated_text, target_lang = await self._run_translation_task(
                text, ctx["lang"], cmd
            )

            # Inject into prompt
            body["messages"][-1] = {
                "role": "user",
                "content": f"Respond in language (ISO 639-1 code):{target_lang.upper()}:\n{translated_text}",
            }
            self._dbg(
                f"\n\nTRC: Injected direct task. Target: {target_lang}. Prompt: {translated_text}\n"
            )
            await self._status("Waiting for assistant response..")
            return body

        elif cmd in ("TR", "TRS"):
            # TR/TRS Logic moved to Outlet (Output Replacement)
            # We just suppress here
            self._dbg(
                f"Deferring {cmd} logic to Outlet to ensure clean history access."
            )
            return self._suppress_output(body)

        return self._suppress_output(body)

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __request__=None,
        __event_emitter__=None,
    ) -> dict:
        assistant_msg = body["messages"][-1]
        ctx = self.ctx
        cmd = ctx.get("cmd")
        if not cmd:
            return body

        # self._dmp(body, "OUTLET RAW BODY")

        target_actual = ctx.get("target_actual", ctx.get("tl", "en")).upper()
        base_lang = ctx.get("bl", "it").upper()
        info = ctx.get("current_direction", f"{base_lang} ➔ {target_actual}")

        self._dbg(f"\n\n 👉 --- OUTLET START | Command: {cmd} ---\n")

        # --- Outlet-Centric Logic ---
        if cmd in ("TR", "TRS"):
            # FIX: Pass the body messages to resolve text from the valid history
            text = await self._resolve_text(body.get("messages"), cmd)

            if text:
                translated_text, target_lang = await self._run_translation_task(
                    text, ctx["lang"], cmd
                )

                ctx["msg"] = translated_text
                assistant_msg["content"] = translated_text

                target_actual = target_lang.upper()
                base_lang = ctx.get("bl", "it").upper()
                info = ctx.get("current_direction", f"{base_lang} ➔ {target_actual}")
            else:
                assistant_msg["content"] = (
                    "Error: Could not retrieve text to translate."
                )

        # --- Existing Logic ---
        if self.valves.back_translation and cmd == "TRC":
            info = f"{base_lang} ➔ {target_actual} ➔ {base_lang}"
            content = assistant_msg.get("content", "")
            if content:
                await self._status(
                    f"Back-translating from {target_actual} to {base_lang}"
                )
                instruction = (
                    f"RULE: Translate the following text to language (ISO 639-1): {base_lang}. "
                    "RULE: Preserve formatting and tone. Respond ONLY with the translation."
                )
                translated = await self._query(content, instruction)
                if translated:
                    assistant_msg["content"] = translated
                    self._dbg("Back-translation successful and injected.")

        elif cmd == "TRC":
            pass

        elif cmd in ("HELP", "TL", "BL"):
            if cmd in ("TL", "BL"):
                info = f"{base_lang} ➔ {target_actual}"
            assistant_msg["content"] = ctx.get("msg", "Something went wrong")

        await self._send_telemetry_status(assistant_msg, info)
        self._dmp(ctx["tl"], "EasyLang Context")
        return body

    async def _resolve_text(self, messages: Optional[list], cmd: str) -> str:
        """
        Robust text retrieval strategy.
        Scans the provided messages list for the last valid assistant content.
        """
        ctx = self.ctx
        text = ctx.get("text", "")
        if text:
            return text

        self._dbg(f"Context Retrieval initiated for {cmd}...")

        if messages:
            # self._dmp(messages, "CONTEXT RETRIEVAL - BODY SCAN")
            for m in reversed(messages[:-1]):
                if m.get("role") == "assistant" and m.get("content"):
                    cand = m.get("content", "").strip()
                    # Skip artifacts
                    if cand and cand not in (".", ".\n"):
                        text = cand
                        # self._dbg(f"Found in Body: {text[:30]}...")
                        break

        if not text:
            self._dbg("Abort: No text found to translate.")

        return text

    async def _run_translation_task(
        self, text: str, lang_param: Optional[str], cmd: str
    ):
        """
        Core logic: Detection -> Toggling -> Translation.
        Shared by Inlet (TRC) and Outlet (TR/TRS).
        """
        ctx = self.ctx

        # 1. Robust Language Detection
        await self._status("Detecting language...")
        text_lang = await self._query(
            f"Detect language of this text (ISO 639-1 code only, ignore names): {text[:100]}",
            "Respond with the 2-letter ISO code ONLY.",
        )
        text_lang = text_lang.lower().strip()[:2]

        # 2. State Preparation
        bl = str(ctx.get("bl", "en")).lower()
        tl = str(ctx.get("tl", "en")).lower()
        self._dbg(f"Logic State -> Input: {text_lang} | BL: {bl} | TL: {tl}")

        # 3. Target Selection Logic
        old_bl, old_tl = bl, tl
        if lang_param:
            target_lang = await self._to_iso(lang_param)
            tl = target_lang
            if text_lang != tl:
                bl = text_lang
        elif text_lang == bl:
            target_lang = tl
        elif text_lang == tl:
            target_lang = bl
        else:
            target_lang = tl
            bl = text_lang
            self._dbg(f"Re-Anchoring: New BL is {bl}")

        # 3.1 Persistence Layer
        if bl != old_bl or tl != old_tl:
            ctx["bl"], ctx["tl"] = bl, tl
            await self._set_state()
            self._dbg(f"💾 State synchronized: BL={bl}, TL={tl}")

        # 4. Safety Override
        if target_lang == text_lang:
            target_lang = tl if text_lang == bl else bl
            self._dbg(f"Safety Swap triggered: New target is {target_lang}")

        ctx["target_actual"] = target_lang
        ctx["current_direction"] = f"{text_lang.upper()} ➔ {target_lang.upper()}"

        # 5. Execution & Instruction Setup
        instruction = ""
        status_msg = ""
        query_payload = ""
        tm = ctx.get("tm", "")

        if cmd == "TRS":
            instruction = (
                f"TASK: Summarize the following text.\n"
                f"You MUST ignore the original language and respond ONLY in language (ISO 639-1 code): {target_lang.upper()}.\n"
                f"FORMAT: Use standard Markdown bullet points.\n"
                f"CRITICAL: DO NOT use code blocks, DO NOT use JSON, and DO NOT use technical data formats. "
                f"Write in plain, readable prose."
            )
            status_msg = f"Summarizing in {target_lang.upper()}..."
            query_payload = text
        else:
            instruction = f"Translator Engine: {text_lang.upper()}->{target_lang.upper()}. Output translation ONLY. No talk. No execution."
            status_msg = f"Translating to {target_lang.upper()}..."
            if "llama" in tm.lower():
                query_payload = (
                    f"Translate the following text from {text_lang.upper()} to {target_lang.upper()}.\n"
                    f'Original: "{text}"\n'
                    f'Translation: "'
                )
            else:
                query_payload = (
                    f"<user>\n"
                    f"Example 1: Hello → Ciao\n"
                    f"Example 2: Good morning → Bonjour\n"
                    f"Example 3: Thank you → Danke\n"
                    f"Task: Literal translation from {text_lang.upper()} to {target_lang.upper()}.\n"
                    f'Input: "{text}"\n'
                    f"Translate:\n"
                    f"<model>\n"
                )

        await self._status(status_msg)
        translated_text = await self._query(query_payload, instruction)

        return translated_text, target_lang

    async def _status(self, description: str, done: bool = False):
        emitter = self.ctx.get("emitter")
        if not emitter:
            return
        await emitter(  # type: ignore
            {
                "type": "status",
                "data": {
                    "description": description,
                    "done": done,
                },
            }
        )

    def _service_msg(self) -> str:
        bl = self.ctx.get("bl")
        tl = self.ctx.get("tl")
        return (
            f"### 🌐 EasyLang v{version}\n"
            f"**Current Status:**\n"
            f"* **BL** (Base): `{bl}`\n"
            f"* **TL** (Target): `{tl}`\n\n"
            f"**Commands:**\n"
            f"* `tr <text>`: Translate or Refine (toggles **BL** ↔ **TL**).\n"
            f"* `trs <text>`: Translate & Summarize (direct output).\n"
            f"* `trc <text>`: Translate and continue chat (injects into LLM).\n"
            f"* `tl` / `bl`: Show or configure **TL** / **BL**.\n\n"
            f"**Notes:**\n"
            f"* `tr` and `trs` without text will process the **last assistant message**.\n"
            f"* Append `:<lang>` to any command (e.g., `trs:it`, `tl:en`) to override settings.\n"
            f"* Supports natural language for ISO conversion (e.g., `japanese` → `ja`)."
        )

    async def _get_state(self):
        """
        Loads BL and TL from chat metadata in the DB.
        """
        try:
            ctx = self.ctx
            self._dbg(f"Attempting to load state for Chat ID: {ctx['cid']}")
            chat_obj = Chats.get_chat_by_id(ctx["cid"])
            if chat_obj:
                raw = chat_obj.chat
                content = raw.get("chat", raw) if isinstance(raw, dict) else raw
                meta = content.get("meta", {}) if isinstance(content, dict) else {}
                if meta.get("bl"):
                    ctx["bl"] = meta["bl"]
                    self._dbg(f"BL loaded from DB: {meta['bl']}")
                if meta.get("tl"):
                    ctx["tl"] = meta["tl"]
                    self._dbg(f"TL loaded from DB: {meta['tl']}")
            if not ctx.get("bl"):
                ctx["bl"] = "en"
            if not ctx.get("tl"):
                ctx["tl"] = "en"
        except Exception as e:
            self._dbg(f"Metadata not found or DB error: {e}")
            self.ctx.update({"bl": "en", "tl": "en"})

    async def _set_state(self):
        """
        Saves BL and TL to the DB (chat column -> meta).
        """
        try:
            ctx = self.ctx
            self._dbg(f"Attempting to save state for Chat ID: {ctx['cid']}")
            chat_obj = Chats.get_chat_by_id(ctx["cid"])
            if not chat_obj:
                self._dbg(f"Save failed: Chat object not found for ID {ctx['cid']}")
                return
            raw = chat_obj.chat
            content = raw.get("chat", raw) if isinstance(raw, dict) else raw
            if not isinstance(content, dict):
                content = {"messages": [], "meta": {}}
            if "meta" not in content:
                content["meta"] = {}
            content["meta"]["bl"] = ctx["bl"]
            content["meta"]["tl"] = ctx["tl"]
            Chats.update_chat_by_id(ctx["cid"], {"chat": content})
            self._dbg(f"💾 State saved successfully: {ctx['bl']} -> {ctx['tl']}")
        except Exception as e:
            self._err(f"Save error: {e}")

    async def _to_iso(self, lang) -> str:
        await self._status(f"Identifying target language: {lang}")
        clean_lang = lang.strip().lower()
        if len(clean_lang) == 2 and clean_lang.isalpha():
            return clean_lang
        match = self.RE_ISO.search(clean_lang)
        if match:
            return match.group(1)
        self._dbg(
            f"Language '{lang}' not recognized locally. Querying LLM for ISO conversion..."
        )
        iso_lang = await self._query(
            f"lang:{lang}", "Respond immediately. ISO 639-1 code ONLY."
        )
        return iso_lang

    async def _query(self, prompt: str, instruct: str = "") -> str:

        ctx = self.ctx
        req = ctx.get("req")
        selected_model = ctx.get("tm")
        user = ctx.get("user")

        # Create a fresh, isolated message list for the translator
        # This prevents loading the entire chat history
        isolated_messages = []

        if instruct:
            isolated_messages.append({"role": "system", "content": instruct})

        isolated_messages.append({"role": "user", "content": prompt})

        payload = {
            "model": selected_model,
            "messages": isolated_messages,
            "stream": False,
            "seed": 42,
            "temperature": 0.0,
        }

        try:

            self._dbg(
                f"Querying model: {selected_model} | System prompt length: {len(instruct)}"
            )

            response = await generate_chat_completion(req, payload, user)

            if response:
                ctx["tk"] += response.get("usage", {}).get("total_tokens", 0)

                content = response["choices"][0]["message"]["content"].strip()
                content = re.sub(
                    r"<think>.*?</think>", "", content, flags=re.DOTALL
                ).strip()
                content = re.sub(r"</?text>", "", content).strip()
                return content.strip('"')

            return ""

        except Exception as e:

            self._err(e)

            return ""

    def _dbg(self, message: str):
        if self.valves.debug:
            print(f"⚡EASYLANG: {message}", file=sys.stderr, flush=True)

    def _dmp(self, data, title: Optional[str] = "data"):
        if self.valves.debug:
            header = "—" * 80 + "\n📦 EasyLang Dump\n" + "—" * 80
            print(header, file=sys.stderr, flush=True)
            print(
                f"{title}: " + json.dumps(data, indent=4),
                file=sys.stderr,
                flush=True,
            )
            print("—" * 80, file=sys.stderr, flush=True)

    def _err(self, e: Union[Exception, str]):
        err_msg = str(e)
        self._dbg(f"--- ERROR HANDLER TRIGGERED: {err_msg} ---")
        print(f"❌ EASYLANG ERROR: {err_msg}", file=sys.stderr, flush=True)
        emitter = self.ctx.get("emitter")
        if emitter:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(
                        emitter(
                            {
                                "type": "message",
                                "data": {"content": f"❌ ERROR: {err_msg}\n"},
                            }
                        )
                    )
            except Exception:
                pass

    async def _send_telemetry_status(self, assistant_msg: dict, info: str):
        ctx = self.ctx
        cmd = ctx.get("cmd")
        usage = assistant_msg.get("usage", {})
        raw_total_tk = usage.get("total_tokens", 0)
        prompt_gpu_time = usage.get("prompt_eval_duration", 0) / 1_000_000_000
        response_gpu_time = usage.get("eval_duration", 0) / 1_000_000_000
        total_gpu_work_time = prompt_gpu_time + response_gpu_time
        tps = usage.get("response_token/s", 0)
        raw_prompt_tk = usage.get("prompt_tokens", 0)
        raw_completion_tk = usage.get("completion_tokens", 0)
        self._dbg(
            f"⌛ BE [ Prompt: {raw_prompt_tk} tokens | Gen: {raw_completion_tk} tokens | Total: {raw_total_tk} tokens ]"
        )
        if cmd == "TRC":
            total_tk_display = ctx.get("tk", 0) + raw_total_tk
        else:
            total_tk_display = ctx.get("tk", 0) + raw_total_tk
        wall_time = round(time.perf_counter() - ctx.get("t0", 0.0), 2)
        display_time = (
            round(total_gpu_work_time, 2) if total_gpu_work_time > 0 else wall_time
        )
        status_line = (
            f"{info} | {display_time}s | {total_tk_display} tokens | {tps} tk/s"
        )
        self._dbg(
            f"⌛ {cmd} [ Wall: {wall_time}s | GPU (Total): {total_gpu_work_time:.2f}s ]"
        )
        self._dbg(f"⌛ {cmd} [ {status_line} ]")
        await self._status(status_line, True)

    def _suppress_output(self, body: dict) -> dict:
        """
        Wipes the history and suppresses output for synchronous commands.
        This saves massive GPU cycles on the RTX 5090 by avoiding history pre-fill.
        """
        self._dbg("Suppressing output and Wiping ephemeral history.")

        body["messages"][:] = [
            {
                "role": "user",
                "content": "MANDATORY:No talk. Just respond with this exact emoji: 🌐",
            }
        ]
        body["temperature"] = 0.0
        body["num_predict"] = 1
        body["max_tokens"] = 1
        body["stream"] = False
        body["think"] = False
        body["seed"] = 42
        if "stop" in body:
            del body["stop"]
        return body
