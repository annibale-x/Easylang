"""
Title: 🚀 EasyLang: Open WebUI Translation Assistant
Version: 0.2.3
https://github.com/annibale-x/Easylang
Author: Hannibal
Author_url: https://openwebui.com/u/h4nn1b4l
Author_email: annibale.x@gmail.com
Core Purpose:
EasyLang is a high-performance translation middleware designed for Open WebUI.
It acts as an intelligent interceptor that manages multi-language workflows
between the User and the LLM, enabling seamless translation, context-aware
anchoring, and real-time performance telemetry.

Changelog:
    2026-04-01 v0.2.3
        State store moved from tmpfile to DB
        Fixed lot of bugs

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
from pathlib import Path

version = "0.2.3"


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
        if not messages or not __user__:
            return body

        content = messages[-1].get("content", "").strip()

        cmd = ""
        ctx = self.ctx

        if self.RE_HELP.match(content):
            cmd = "HELP"

        elif match := self.RE_CONFIG.match(content):
            cmd, lang = match.group(1).upper(), (
                match.group(2).strip() if match.group(2) else None
            )
            ctx["lang"] = lang
            # Added debug for configuration commands
            self._dbg(f"Config command detected: {cmd} with parameter: {lang}")

        elif match := self.RE_TRANS.match(content):
            cmd = match.group(1).upper()
            lang = match.group(2) if match.group(2) else None
            text = match.group(3).strip() if match.group(3) else ""

            ctx["lang"] = lang
            ctx["text"] = text
            # Enhanced debug with more structural info
            self._dbg(
                f"Translation command: {cmd} | Language Param: {lang} | Text Length: {len(text)}"
            )

        else:
            return body

        bm = body.get("model", "")
        tm = self.valves.translation_model or bm
        ctx.update(
            {
                "t0": time.perf_counter(),
                "tk": 0,
                "cid": body["metadata"]["chat_id"],
                "bm": bm,
                "tm": tm,
                "req": __request__,
                "user": UserModel(**__user__),
                "emitter": __event_emitter__,
                "uid": __user__.get("id", "default"),
                "cmd": cmd,
            }
        )

        # Identification debug for inlet entry
        self._dbg(f"--- INLET START | Chat ID: {ctx['cid']} | Command: {cmd} ---")

        await self._get_state()

        # Added state dump after update
        self._dbg(f"Current Memory State -> Base: {ctx['bl']} | Target: {ctx['tl']}")

        if cmd == "HELP":
            ctx["msg"] = self._service_msg()

        elif cmd in ("BL", "TL"):
            lang = ctx["lang"]
            if lang:
                new_lang = await self._to_iso(lang)
                lang_key = cmd.lower()
                curr_lang = ctx[lang_key]
                if new_lang != curr_lang:
                    ctx[lang_key] = new_lang
                    await self._set_state()
                    self._dmp(
                        {"bl": ctx["bl"], "tl": ctx["tl"]}, f"Updated {cmd} State"
                    )
                    ctx["msg"] = (
                        f"🗹 Current {cmd} switched from **{curr_lang}** to **{new_lang}**"
                    )
            else:
                ctx["msg"] = (
                    f"🛈 Current {cmd}: **{ctx['tl'] if cmd=='TL' else ctx['bl']}**"
                )

        elif cmd in ("TR", "TRC", "TRS"):
            text = ctx["text"]
            lang_param = ctx["lang"]

            # 1. Fetch text if empty (Context Retrieval)
            if not text and cmd in ("TR", "TRS", "TRC"):
                self._dbg(f"Fetching last assistant message for {cmd}...")
                for m in reversed(messages[:-1]):
                    if m.get("role") == "assistant" and m.get("content"):
                        text = m.get("content", "")
                        break
            if not text:
                self._dbg("Abort: No text found to translate.")
                return body

            # 2. Robust Language Detection
            await self._status("Detecting language...")
            # Improved prompt to avoid proper noun confusion (e.g. "Apollo" -> EN)
            text_lang = await self._query(
                f"Detect language of this text (ISO 639-1 code only, ignore names): {text[:100]}",
                "Respond with the 2-letter ISO code ONLY.",
            )

            # Strict normalization: lowercase and strip formatting artifacts
            text_lang = text_lang.lower().strip()[:2]

            # 3. State Preparation (Strict Lowercase)
            bl = str(ctx.get("bl", "en")).lower()
            tl = str(ctx.get("tl", "en")).lower()

            self._dbg(f"Logic State -> Input: {text_lang} | BL: {bl} | TL: {tl}")

            # 4. Target Selection Logic (Unified)
            old_bl, old_tl = bl, tl

            if lang_param:
                # Case A: Explicit override (e.g., :fr)
                target_lang = await self._to_iso(lang_param)
                tl = target_lang
            elif text_lang == bl:
                # Case B: Toggling Base -> Target
                target_lang = tl
            elif text_lang == tl:
                # Case C: Toggling Target -> Base
                target_lang = bl
            else:
                # Case D: Re-Anchoring (New input language detected)
                # Keep the same target, but update what we consider the 'Base'
                target_lang = tl
                bl = text_lang
                self._dbg(f"Re-Anchoring: New BL is {bl}")

            # 4.1 Persistence Layer
            # Only hit the DB if something actually changed
            if bl != old_bl or tl != old_tl:
                ctx["bl"], ctx["tl"] = bl, tl
                await self._set_state()
                self._dbg(f"💾 State synchronized: BL={bl}, TL={tl}")

            # 5. Safety Override
            # Strict check: if target is the same as input, force the opposite
            if target_lang == text_lang:
                target_lang = tl if text_lang == bl else bl
                self._dbg(f"Safety Swap triggered: New target is {target_lang}")

            ctx["target_actual"] = target_lang
            ctx["current_direction"] = f"{text_lang.upper()} ➔ {target_lang.upper()}"

            # 6. Execution & Instruction Setup
            instruction = ""
            status_msg = ""

            if cmd == "TRC":
                instruction = (
                    f"RULE: First, translate the user's request into language (ISO 639-1 code): {target_lang.upper()}.\n"
                    f"THEN, fulfill that request immediately and extensively in {target_lang.upper()}.\n"
                    f"DO NOT meta-comment. DO NOT ask questions. Just provide the requested content."
                )
                status_msg = f"Moving conversation to {target_lang.upper()}..."
            elif cmd == "TRS":
                instruction = (
                    f"TASK: Summarize the following text.\n"
                    f"You MUST ignore the original language and respond ONLY in language (ISO 639-1 code): {target_lang.upper()}.\n"
                    f"FORMAT: Use standard Markdown bullet points.\n"
                    f"CRITICAL: DO NOT use code blocks, DO NOT use JSON, and DO NOT use technical data formats. "
                    f"Write in plain, readable prose."
                )
                status_msg = f"Summarizing in {target_lang.upper()}..."
            else:  # TR
                instruction = (
                    f"### TASK: TRANSLATE TO {target_lang.upper()} ###\n"
                    f"ROLE: You are a literal translation engine. NOT an assistant.\n"
                    f"CRITICAL RULES:\n"
                    f"1. Translate the input text strictly into language (ISO 639-1 code): {target_lang.upper()}.\n"
                    f"2. DO NOT ANSWER questions. DO NOT explain concepts. DO NOT engage in conversation.\n"
                    f"3. Output ONLY the translated text. No preamble, no 'Here is the translation'.\n"
                    f"4. If the input is a question, your output must be the translated question, NOT the answer.\n"
                    f"5. Maintain formatting."
                )
                status_msg = f"Translating to {target_lang.upper()}"

            await self._status(status_msg)
            translated_text = await self._query(text, instruction)

            # 7. Routing
            if cmd in ("TR", "TRS"):
                ctx["msg"] = translated_text

            else:  # TRC logic
                # Simplified injection: Use the translated prompt as the primary instruction
                body["messages"][-1]["content"] = (
                    f"Please perform the following task in {target_lang.upper()}:\n"
                    f"{translated_text}"
                )
                self._dbg(
                    f"TRC: Injected direct task in {target_lang}. Bypass suppression."
                )

                if self.valves.back_translation:
                    await self._status(f"Sending {target_lang.upper()} prompt to model")

                await self._status(f"Waiting for model response to complete...")
                return body  # <--- CRITICAL: Returns immediately to LLM

        # Suppression logic for direct output commands (TR, TRS, HELP, etc.)
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

        # Get the actual target used (fallback to global tl if missing)
        target_actual = ctx.get("target_actual", ctx.get("tl", "en")).upper()
        base_lang = ctx.get("bl", "it").upper()

        # Base info string
        info = ctx.get("current_direction", f"{base_lang} ➔ {target_actual}")

        self._dbg(f"--- OUTLET START | Command: {cmd} ---")

        if self.valves.back_translation and cmd == "TRC":
            # Update info to show the full round-trip
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
                self._dbg(
                    f"Starting back-translation from {target_actual} to {base_lang}..."
                )
                translated = await self._query(content, instruction)
                if translated:
                    assistant_msg["content"] = translated
                    self._dbg("Back-translation successful and injected.")

        elif cmd in ("HELP", "TL", "BL", "TR", "TRS"):
            self._dbg(
                f"Injecting captured message from context into assistant response for command: {cmd}"
            )
            assistant_msg["content"] = ctx.get("msg", "Something went wrong")

        tk = ctx.get("tk")
        tt = time.perf_counter() - ctx.get("t0", 0.0)
        t2 = round(tt, 2)

        # Telemetry debug
        self._dbg(
            f"Execution Telemetry -> Time: {t2}s | Tokens used in middleware: {tk}"
        )

        await self._status(f"{info} | {t2}s | {tk} tokens", True)  # type: ignore

        return body

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
            f"### 🌐 EasyLang Helper v{version}\n"
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
            from open_webui.models.chats import Chats

            ctx = self.ctx
            self._dbg(f"Attempting to load state for Chat ID: {ctx['cid']}")

            chat_obj = Chats.get_chat_by_id(ctx["cid"])
            if chat_obj:
                # Safe JSON navigation to avoid nested structures
                raw = chat_obj.chat
                content = raw.get("chat", raw) if isinstance(raw, dict) else raw
                meta = content.get("meta", {}) if isinstance(content, dict) else {}

                # If they exist in DB, use them. Otherwise keep defaults.
                if meta.get("bl"):
                    ctx["bl"] = meta["bl"]
                    self._dbg(f"BL loaded from DB: {meta['bl']}")
                if meta.get("tl"):
                    ctx["tl"] = meta["tl"]
                    self._dbg(f"TL loaded from DB: {meta['tl']}")

            # Safety defaults if ctx is still empty
            if not ctx.get("bl"):
                ctx["bl"] = "it"
            if not ctx.get("tl"):
                ctx["tl"] = "en"

        except Exception as e:
            self._dbg(f"Metadata not found or DB error: {e}")
            self.ctx.update({"bl": "it", "tl": "en"})

    async def _set_state(self):
        """
        Saves BL and TL to the DB (chat column -> meta).
        """
        try:
            from open_webui.models.chats import Chats

            ctx = self.ctx
            self._dbg(f"Attempting to save state for Chat ID: {ctx['cid']}")

            chat_obj = Chats.get_chat_by_id(ctx["cid"])
            if not chat_obj:
                self._dbg(f"Save failed: Chat object not found for ID {ctx['cid']}")
                return

            raw = chat_obj.chat
            content = raw.get("chat", raw) if isinstance(raw, dict) else raw

            # Ensure meta structure exists
            if not isinstance(content, dict):
                content = {"messages": [], "meta": {}}
            if "meta" not in content:
                content["meta"] = {}

            content["meta"]["bl"] = ctx["bl"]
            content["meta"]["tl"] = ctx["tl"]

            # Physical record update
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

        messages = []
        if instruct:
            messages.append({"role": "system", "content": instruct})

        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": selected_model,
            "messages": messages,
            "stream": False,
            "temperature": 0,
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

    def _suppress_output(self, body: dict) -> dict:
        self._dbg("Suppressing main model output (Single Dot mode).")
        body["messages"] = [{"role": "user", "content": "Respond with a single dot."}]
        body["max_tokens"] = 1
        body["stream"] = False
        if "stop" in body:
            del body["stop"]
        return body
