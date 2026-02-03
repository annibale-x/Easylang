import json
import datetime
from typing import Optional
import re


class Filter:
    def __init__(self):
        # Mappa Hex (0-f) -> Caratteri Invisibili (Zero Width)
        self.hex_to_inv = {
            "0": "\u200b",
            "1": "\u200c",
            "2": "\u200d",
            "3": "\u200e",
            "4": "\u200f",
            "5": "\u202a",
            "6": "\u202b",
            "7": "\u202c",
            "8": "\u202d",
            "9": "\u202e",
            "a": "\u2060",
            "b": "\u2061",
            "c": "\u2062",
            "d": "\u2063",
            "e": "\u2064",
            "f": "\u2066",
        }
        self.inv_to_hex = {v: k for k, v in self.hex_to_inv.items()}
        # Marker invisibile per identificare l'inizio della riga di stato
        self.LINE_MARKER = "\u200a"
        self.END_MARKER = "\u2067"

    def _state(self, messages: list, data: Optional[dict] = None) -> Optional[dict]:
        """Getter/Setter polimorfico per gestire lo stato invisibile."""
        # 1. Trova il primo messaggio utente
        target_idx = next(
            (i for i, m in enumerate(messages) if m.get("role") == "user"), -1
        )
        if target_idx == -1:
            return None

        # Definiamo il terminatore invisibile (es. U+2064)
        current_content = messages[target_idx].get("content", "")

        # --- SETTER (Scrittura) ---
        if data is not None:
            # Rimuoviamo eventuale stato precedente tramite regex (dal marker al terminatore)
            clean_content = re.sub(
                f"{re.escape(self.LINE_MARKER)}.*?{re.escape(self.END_MARKER)}",
                "",
                current_content,
                flags=re.DOTALL,
            )

            # Codifica: Dict -> JSON -> Hex -> Invisible
            json_bytes = json.dumps(data).encode("utf-8")
            hex_str = json_bytes.hex()
            encoded_payload = "".join(self.hex_to_inv[c] for c in hex_str)

            # Aggiornamento: Append diretto senza \n
            messages[target_idx][
                "content"
            ] = f"{clean_content}{self.LINE_MARKER}{encoded_payload}{self.END_MARKER}"
            return data

        # --- GETTER (Lettura) ---
        # Estrazione tramite regex del contenuto tra i marker
        match = re.search(
            f"{re.escape(self.LINE_MARKER)}(.*){re.escape(self.END_MARKER)}",
            current_content,
            flags=re.DOTALL,
        )

        if match:
            encoded_str = match.group(1)
            try:
                # FIX: Filtra SOLO i caratteri che appartengono alla tua mappa
                # Se c'è un carattere spurio a pos 19, questo lo ignora e non spacca fromhex
                hex_str = "".join(
                    self.inv_to_hex[c] for c in encoded_str if c in self.inv_to_hex
                )

                # Protezione: fromhex vuole una stringa di lunghezza pari
                if not hex_str or len(hex_str) % 2 != 0:
                    return {}

                return json.loads(bytes.fromhex(hex_str).decode("utf-8"))
            except Exception as e:
                print(f"[ERROR] Decodifica fallita: {e}")
                return {}

        return {}

    async def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        messages = body.get("messages", [])

        print("\n" + "=" * 20 + " DEBUG INLET " + "=" * 20)

        # Recuperiamo lo stato salvato precedentemente
        state = self._state(messages)
        # print("MESSAGES IN INLET")
        # print(json.dumps(messages, indent=4))

        if state:
            print(f"✅ STATO RECUPERATO: {state}")
        else:
            print("ℹ️ INFO: Nessuno stato trovato (Primo messaggio?)")

        print("=" * 53 + "\n")
        return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        messages = body.get("messages", [])

        print("\n" + "=" * 20 + " DEBUG OUTLET " + "=" * 20)

        # Generiamo il nuovo timestamp
        ts_now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_data = {"ts": ts_now}
        new_data = {"bl": "en", "tl": "en"}

        # Salviamo il JSON nello stato invisibile
        self._state(messages, data=new_data)

        print(f"💾 STATO SALVATO: {new_data}")
        print("=" * 54 + "\n")

        return body
