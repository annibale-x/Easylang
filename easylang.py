"""
Title: 🚀 EasyLang: Open WebUI Translation Assistant
Version: 0.1.9
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

import asyncio
import re
import sys
import time
import json
from typing import Optional
from pydantic import BaseModel, Field
from open_webui.main import generate_chat_completion  # type: ignore
from open_webui.models.users import UserModel  # type: ignore
from pathlib import Path


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
            r"^(TRC|TR)(?:\:([a-z]{2,10}))?(?:\s+(.*))?$", re.I | re.S
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

        await self._update_state()

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
                    self._save_state()
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

        elif cmd in ("TR", "TRC"):

            text = ctx["text"]
            lang = ctx["lang"]

            if not text and cmd == "TR":
                self._dbg("Empty TR detected, fetching last assistant message...")
                for m in reversed(messages[:-1]):
                    if m.get("role") == "assistant":
                        text = m.get("content", "")
                        break
            if not text:
                self._dbg("Abort: No text found to translate.")
                return body

            await self._status("Detecting the base language")
            text_lang = await self._query(
                f"Detect: {text[:100]}", "Respond immediately. ISO 639-1 code ONLY."
            )
            self._dbg(f"Detected input language: {text_lang}")

            bl = ctx.get("bl")
            tl_state = ctx.get("tl")

            if text_lang == tl_state:
                target_lang = bl
                self._dbg(
                    f"Toggle logic: Input matches TL ({text_lang}). New Target -> BL ({bl})"
                )
            elif text_lang == bl:
                target_lang = tl_state
                self._dbg(
                    f"Toggle logic: Input matches BL ({text_lang}). New Target -> TL ({tl_state})"
                )
            else:
                if not ctx.get("text"):
                    target_lang = bl
                    self._dbg(
                        f"Fallback logic: Unexpected language in TR, forcing BL ({bl})"
                    )
                else:
                    ctx["bl"] = text_lang
                    target_lang = tl_state
                    self._save_state()
                    self._dbg(
                        f"Re-Anchoring: New Base Language set to {text_lang}. Target: {tl_state}"
                    )

            if lang:
                target_lang = await self._to_iso(lang)
                ctx["tl"] = target_lang
                ctx["bl"] = text_lang
                self._save_state()
                self._dbg(
                    f"Manual Override active: Force TL={target_lang}, BL={text_lang}"
                )

            instruction = (
                f"RULE: Translate the following text to language (ISO 639-1): {target_lang}. "
                "RULE: Preserve formatting and tone. Respond ONLY with the translation."
            )

            if text_lang.lower() == target_lang.lower():
                status_msg = f"Refining text in {target_lang.upper()}"
            else:
                status_msg = (
                    f"Translating from {text_lang.upper()} to {target_lang.upper()}"
                )

            await self._status(status_msg)
            translated_text = await self._query(text, instruction)

            # Log translation completion
            self._dbg(f"Translation completed. Output length: {len(translated_text)}")

            if cmd == "TR":
                ctx["msg"] = translated_text
            else:
                enforced_text = f"{translated_text}\n\nRULE: I want you to respond strictly in language (ISO 639-1): {target_lang}"
                body["messages"][-1]["content"] = enforced_text
                self._dbg(f"TRC mode: Injected translation into message body.")

                if self.valves.back_translation:
                    self._dbg(
                        "Back-translation enabled. Proceeding to outlet for response capture."
                    )
                    await self._status(f"Sending {target_lang.upper()} prompt to model")

                return body

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
        msg = ctx.get("msg")
        info = f"{ctx['bl'].upper()} ➔ {ctx['tl'].upper()}"

        if not cmd:
            return body

        # Identification debug for outlet entry
        self._dbg(f"--- OUTLET START | Chat ID: {ctx['cid']} | Command: {cmd} ---")

        if self.valves.back_translation and cmd == "TRC":
            info = f"{ctx['bl'].upper()} ➔ {ctx['tl'].upper()} ➔ {ctx['bl'].upper()}"
            content = assistant_msg.get("content", "")
            if content:
                base_lang = ctx["bl"]
                await self._status(
                    f"Back-translating from {ctx['tl'].upper()} to {base_lang.upper()}"
                )
                instruction = (
                    f"RULE: Translate the following text to language (ISO 639-1): {base_lang}. "
                    "RULE: Preserve formatting and tone. Respond ONLY with the translation."
                )
                self._dbg(f"Starting back-translation to {base_lang}...")
                translated = await self._query(content, instruction)
                if translated:
                    assistant_msg["content"] = translated
                    self._dbg("Back-translation successful and injected.")

        elif cmd in ("HELP", "TL", "BL", "TR"):
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
            f"### 🌐 EasyLang Helper\n"
            f"**Current Status:**\n"
            f"* **BL** (Base): `{bl}`\n"
            f"* **TL** (Target): `{tl}`\n\n"
            f"**Commands:**\n"
            f"* `tr <text>`: Translate (toggles **BL** ↔ **TL**).\n"
            f"* `tr`: Translate last assistant message.\n"
            f"* `trc <text>`: Translate and continue chat.\n"
            f"* `tl` / `bl`: Show or configure **TL** / **BL**.\n\n"
            f"**Notes:**\n"
            f"* Append `:<lang>` to any command (e.g., `tr:fr`, `tl:it`) to update settings on the fly.\n"
            f"* Languages can be entered in **any format or language** (e.g., `:italian`, `:jp`, `:español`);\n"
            f"they will be automatically converted to **ISO 639-1** format."
        )

    def _save_state(self):
        ctx = self.ctx
        filename = Path(f"/tmp/{ctx['cid']}.el")
        try:
            with open(filename, "w") as f:
                f.write(f"{ctx['bl']}{ctx['tl']}")
            self._dbg(f"State saved successfully to {filename}")
        except Exception as e:
            self._dbg(f"Critical I/O Error during save: {e}")
            self._err(f"EasyLang State Error: Unable to save preferences ({e})")

    async def _update_state(self):
        ctx = self.ctx
        filename = Path(f"/tmp/{ctx['cid']}.el")

        if not filename.exists():
            await self._status("Initializing EasyLang state...")
            self._dbg(f"State file {filename} not found. Initializing...")
            if self.valves.target_language:
                bl = await self._to_iso(self.valves.target_language)
            else:
                bl = "en"

            try:
                with open(filename, "w") as f:
                    f.write(f"{bl}{bl}")
            except Exception as e:
                self._err(e)

        content = filename.read_text().strip()
        if len(content) >= 4:
            ctx.update({"bl": content[:2], "tl": content[2:]})
        else:
            self._dbg("State file corrupted or too short. Falling back to defaults.")
            ctx.update({"bl": "en", "tl": "en"})

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
            header = "—" * 80 + "\n📦 EASYMAGE DUMP\n" + "—" * 80
            print(header, file=sys.stderr, flush=True)
            print(
                f"{title}: " + json.dumps(data, indent=4),
                file=sys.stderr,
                flush=True,
            )
            print("—" * 80, file=sys.stderr, flush=True)

    def _err(self, e: Exception):
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
        body["messages"] = [{"role": "user", "content": "Respond with a single dot"}]
        body["max_tokens"] = 1
        if "stop" in body:
            del body["stop"]
        return body
