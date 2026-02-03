"""
Title: 🚀 EasyLang: Open WebUI Translation Assistant
Version: 0.8.9.3
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
from open_webui.main import generate_chat_completion
from open_webui.models.users import UserModel
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
            default=True, description="Enable detailed state dumps in logs."
        )

    def __init__(self):
        self.valves = self.Valves()
        self.ctx = {}

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

        if content.lower() == "t?":
            cmd = "HELP"

        elif match := re.match(r"^(TL|BL)(?:\:(.+))?\s*$", content, re.I):
            cmd, lang = match.group(1).upper(), (
                match.group(2).strip() if match.group(2) else None
            )
            self.ctx["lang"] = lang

        elif match := re.match(
            r"^(trc|tr)(?:[-/]([a-zA-Z]{2,}))?(?:\s+(.*))?$", content, re.I | re.S
        ):
            cmd = "trtrc"

        else:
            return body

        # ----------------------------------------------------------------------------------
        bm = body.get("model", "")
        tm = self.valves.translation_model or bm
        self.ctx.update(
            {
                "t0": time.perf_counter(),
                "tk": 0,
                "cid": body.get("metadata").get("chat_id"),
                "bm": bm,
                "tm": tm,
                "req": __request__,
                "user": UserModel(**__user__),
                "emitter": __event_emitter__,
                "uid": __user__.get("id", "default"),
                "cmd": cmd,
            }
        )
        # ----------------------------------------------------------------------------------
        self._dbg(f"INLET {self.ctx['cid']} {cmd}")
        # ----------------------------------------------------------------------------------

        await self._update_state()

        # ----------------------------------------------------------------------------------
        self._dbg(f"STATE: {self.ctx['bl']}-{self.ctx['tl']}")
        # ----------------------------------------------------------------------------------

        if cmd == "HELP":
            self.ctx["msg"] = self._service_msg()

        elif cmd in ("BL", "TL"):
            lang = self.ctx["lang"]

            self._dbg(f"+++++++++++++++++++++++++++++ {cmd}")

            if lang:
                self._dbg(f"+++++++++++++++++++++++++++++ {lang}")
                new_lang = await self._to_iso(lang)
                self._dbg(f"+++++++++++++++++++++++++++++ {new_lang}")
                lang_key = cmd.lower()
                curr_lang = self.ctx[lang_key]
                self._dbg(f"+++++++++++++++++++++++++++++ {curr_lang}")
                self._dbg(f"+++++++++++++++++++++++++++++ {lang_key}")

                if new_lang != curr_lang:
                    self.ctx[lang_key] = new_lang
                    self._save_state()
                    self._dmp({"bl": self.ctx["bl"], "tl": self.ctx["tl"]}, "lang")
                    self.ctx[
                        "msg"
                    ] = f"🗹 Current {cmd} switched from **{curr_lang}** to **{new_lang}**"
            else:
                self.ctx[
                    "msg"
                ] = f"🛈 Current {cmd}: **{self.ctx['tl'] if cmd=='TL' else self.ctx['bl']}**"

        return self._suppress_output(body)

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __request__=None,
        __event_emitter__=None,
    ) -> dict:
        assistant_msg = body["messages"][-1]
        cmd = self.ctx.get("cmd")
        msg = self.ctx.get("msg")

        if not msg:
            return body

        # ----------------------------------------------------------------------------------
        self._dbg(f"OUTLET {self.ctx['cid']} {cmd}")
        # ----------------------------------------------------------------------------------

        # ----------------------------------------------------------------------------------

        if cmd in ("HELP", "TL", "BL"):
            assistant_msg["content"] = self.ctx.get("msg", "Something went wrong")

        # ----------------------------------------------------------------------------------
        tk = self.ctx.get("tk")
        tt = time.perf_counter() - self.ctx.get("t0")
        t2 = round(tt, 2)
        await __event_emitter__(
            {
                "type": "status",
                "data": {"description": f"Done {t2}s | {tk} tokens", "done": True},
            }
        )

        # self._dmp({"bl": self.ctx["bl"], "tl": self.ctx["tl"]}, "lang")
        # self._dbg(f"TOKENS: {tk}")
        # self._dbg(f"TIME: {t2}\n\n")

        return body

    # =========================================================================

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
        except Exception as e:
            self._err(e)

        return True

    async def _update_state(self):
        ctx = self.ctx
        filename = Path(f"/tmp/{ctx['cid']}.el")

        # GETTER: controlla esistenza PRIMA di leggere
        if not filename.exists():
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
        ctx.update({"bl": content[:2], "tl": content[2:]})

    async def _to_iso(self, lang) -> str:
        # Test if already ISO 639-1 code
        match = re.search(r"\b([a-z]{2})\b", lang.lower())
        if match:
            return match.group(1)
        iso_lang = await self._query(
            f"lang:{lang}", "Respond immediately. ISO 639-1 code ONLY."
        )

        return iso_lang

    async def _query(self, prompt: str, instruct: str = "") -> str:
        ctx = self.ctx
        req = ctx.get("req")

        selected_model = ctx.get("bm")
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
            response = await generate_chat_completion(req, payload, user)
            if response:
                self.ctx["tk"] += response.get("usage", {}).get("total_tokens", 0)

                content = response["choices"][0]["message"]["content"].strip()
                content = re.sub(
                    r"<think>.*?</think>", "", content, flags=re.DOTALL
                ).strip()
                content = re.sub(r"</?text>", "", content).strip()
                return content.strip('"')
            return ""
        except Exception as e:
            self._err(e)  # Await the error reporter
            return ""

    # =========================================================================

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
        """Synchronous error handler that schedules emitter tasks if needed."""
        err_msg = str(e)
        print(f"❌ EASYLANG ERROR: {err_msg}", file=sys.stderr, flush=True)

        emitter = self.ctx.get("emitter")

        if emitter:
            # Schedule the async emission without blocking the sync caller
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
        # Non svuotiamo i messaggi, ma iniettiamo un comando di stop immediato
        body["messages"] = [{"role": "user", "content": "Respond with a single dot"}]
        body["max_tokens"] = 1
        if "stop" in body:
            del body["stop"]  # Puliamo eventuali stop precedenti
        return body
