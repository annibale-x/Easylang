"""
Title: 🚀 EasyLang: Open WebUI Translation Assistant
Version: 0.8.9.5
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
            r"^(trc|tr)(?:\:([^\s]+))?(?:\s+(.*))?$", content, re.I | re.S
        ):
            cmd = match.group(1).upper()
            # Il gruppo 2 ora prende solo ciò che è attaccato ai ":" senza spazi
            lang = match.group(2) if match.group(2) else None
            # Il gruppo 3 prende tutto il resto dopo lo spazio
            text = match.group(3).strip() if match.group(3) else ""

            self.ctx["lang"] = lang
            self.ctx["text"] = text
            self._dbg(f"Parsed: CMD={cmd}, LANG={lang}, TEXT={text}")

        else:
            return body

        # ----------------------------------------------------------------------------------
        bm = body.get("model", "")
        tm = self.valves.translation_model or bm
        self.ctx.update(
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
            if lang:
                new_lang = await self._to_iso(lang)
                lang_key = cmd.lower()
                curr_lang = self.ctx[lang_key]
                if new_lang != curr_lang:
                    self.ctx[lang_key] = new_lang
                    self._save_state()
                    self._dmp({"bl": self.ctx["bl"], "tl": self.ctx["tl"]}, "lang")
                    self.ctx["msg"] = (
                        f"🗹 Current {cmd} switched from **{curr_lang}** to **{new_lang}**"
                    )
            else:
                self.ctx["msg"] = (
                    f"🛈 Current {cmd}: **{self.ctx['tl'] if cmd=='TL' else self.ctx['bl']}**"
                )

        elif cmd in ("TR", "TRC"):

            text = self.ctx["text"]
            lang = self.ctx["lang"]  # or self.ctx["tl"]

            # Work on input language

            if not text and cmd == "TR":  # TODO verificare se può andare per TRC
                for m in reversed(messages[:-1]):
                    if m.get("role") == "assistant":
                        text = m.get("content", "")
                        break
            if not text:
                return body

            # Input text language
            text_lang = await self._query(
                f"Detect: {text[:100]}", "Respond immediately. ISO 639-1 code ONLY."
            )

            # 1. Current state recovery and default target definition
            bl = self.ctx.get("bl")
            tl_state = self.ctx.get("tl")

            # 2. Target Determination Logic (Toggle Rule & Context Recovery)
            # If the user has not sent text, we are translating the last assistant message.
            # In this case, we always invert with respect to the language detected in the message.
            if text_lang == tl_state:
                target_lang = bl
                self._dbg(f"Toggle: Detected TL ({text_lang}) -> Target BL ({bl})")
            elif text_lang == bl:
                target_lang = tl_state
                self._dbg(
                    f"Toggle: Detected BL ({text_lang}) -> Target TL ({tl_state})"
                )
            else:
                # If it's a completely new language, anchor it as SL and translate into TL
                # Unless it is a blank TR, where we force a return to BL.
                if not self.ctx.get("text"):
                    target_lang = bl
                    self._dbg(
                        f"Context Recovery: Language mismatch, forcing fallback to BL ({bl})"
                    )
                else:
                    self.ctx["bl"] = text_lang
                    target_lang = tl_state
                    self._save_state()
                    self._dbg(
                        f"Re-Anchoring: New BL ({text_lang}) -> Target TL ({tl_state})"
                    )

            # 3. Override manual ISO (ex.: it:en)
            # If the user specifies es,en, force the target and update the TL pointer
            if lang:
                target_lang = await self._to_iso(lang)
                self.ctx["tl"] = target_lang
                self.ctx["bl"] = text_lang
                self._save_state()
                self._dbg(f"Manual Override: TL updated to {target_lang}")

            # 4. Execution Translation
            instruction = (
                f"RULE: Translate the following text to language (ISO 639-1): {target_lang}. "
                "RULE: Preserve formatting and tone. Respond ONLY with the translation."
            )

            translated_text = await self._query(text, instruction)

            # 5. Routing Output
            if cmd == "TR":
                self.ctx["msg"] = translated_text
            else:
                # For TRC, we modify the message body and let it flow to the LLM
                enforced_text = f"{translated_text}\n\nRULE: I want you to respond strictly in language (ISO 639-1): {target_lang}"
                body["messages"][-1]["content"] = enforced_text
                self._dbg(f"TRC:{enforced_text}")
                return body

            self._dbg(f"[ TR/TRC ]\n\n>>>  {text}: {text_lang}->{lang}  <<<\n")

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

        if cmd in ("HELP", "TL", "BL", "TR"):
            assistant_msg["content"] = self.ctx.get("msg", "Something went wrong")

        elif cmd == "TRC":
            pass
        else:
            assistant_msg["content"] = "Whoops... Something went wrong"
            return body

        # ----------------------------------------------------------------------------------
        tk = self.ctx.get("tk")
        tt = tt = time.perf_counter() - self.ctx.get("t0", 0.0)
        t2 = round(tt, 2)
        await __event_emitter__(  # type: ignore
            {
                "type": "status",
                "data": {"description": f"Done {t2}s | {tk} tokens", "done": True},
            }
        )

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
