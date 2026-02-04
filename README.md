## 🌐 EasyLang v0.2.3: Easy Translation Assistant Filter

State-aware translation assistant for Open WebUI. Features smart bidirectional toggling, context-based summarization, and precision performance tracking.

[![GitHub Repo](https://img.shields.io/badge/GitHub-Repository-181717?logo=github&logoColor=white)](https://github.com/annibale-x/EasyLang)
![Open WebUI Plugin](https://img.shields.io/badge/Open%20WebUI-Plugin-blue?style=flat&logo=openai)
![License](https://img.shields.io/github/license/annibale-x/EasyLang?color=green)

---

### ✨ What's New in v0.2.3
- **Database State Persistence**: Moved state management from unstable temporary files to the **Open WebUI Database**. Your `BL` (Base Language) and `TL` (Target Language) settings are now strictly tied to the specific `chat_id` and persist across sessions.
- **Prompt Engineering Overhaul**: Refined system instructions for `TR`, `TRS`, and `TRC` to reduce "hallucinations" or meta-comments from the translation model. 
- **Bug Fixes**: General stability improvements and logic refinement for state synchronization.

---

### 🚀 At a Glance: What can you do?

- **Zero-Click Translation**: Just type `tr <text>` and let the filter figure out if it needs to translate to your target language or back to your base language.
- **Smart "Context Scraper"**: Type `tr` (empty) to instantly translate the last message sent by the Assistant without copying and pasting.
- **Contextual Summarization**: Use `trs` to get a bulleted summary of long texts or previous assistant answers in your preferred language.
- **Bypass the LLM**: Translate text directly in the chat bar without triggering a full LLM response, saving time and tokens.
- **Deep Language Injection**: Use `trc` to translate your thought and feed it directly to the model, allowing you to chat in a language you don't speak fluently.
- **Automatic Re-Anchoring**: Switch your speaking language mid-conversation; EasyLang detects the change and updates your "Base Language" pointer automatically.
- **Real-Time Telemetry**: Monitor exactly how many tokens you are spending and the latency of every single translation.

---

### 📖 The Philosophy

EasyLang was created for power users who operate in multi-language environments, removing the "copy-paste-translate" friction by baking the intelligence directly into the prompt bar. 

Whether you are debugging code in English or chatting in French, the filter adapts its anchoring logic to match your current cognitive flow.

---

### 💡 Usage & Command Schema

Every command containing `<text>` triggers a **Language Detection** routine. The system dynamically compares the detected language with the `BL` (Base Language) and `TL` (Target Language) pointers.

| Command | Action | Logic / Behavioral Impact |
| :--- | :--- | :--- |
| **`tr <text>`** | **Dynamic Toggle** | Detects input: If `TL` → Translates to `BL`. Otherwise → Translates to `TL`. |
| **`tr`** | **Context Recovery** | Scrapes the last `assistant` message and executes a symmetric translation. |
| **`trs <text>`** | **Structured Summary** | Generates a dense, hierarchical summary in `TL` preserving technical depth. |
| **`trs`** | **Context Summary** | Extracts and structures the core information from the last assistant message. |
| **`trc <text>`** | **Chat Continuation** | Translates input and injects it into the LLM stream to continue the chat. |
| **`<cmd>:<lang>`** | **Force Language** | Valid for `tr:`, `trs:`, `trc:`. Overrides `TL` and updates the pointer in DB. |
| **`bl:<lang>` / `tl:<lang>`** | **Manual Set** | Sets `BL` or `TL` using full names or 2-letter ISO codes (Persisted in DB). |
| **`bl` / `tl`** | **Pointer Query** | Returns the current value of the requested language pointer. |
| **`t?`** | **System Dashboard** | Displays `BL`/`TL` status, telemetry, and command reference. |

---

## ⚡ Language Updates Logic

Every translation command triggers a state check. Pointers are dynamic and designed to follow your conversational flow without manual intervention.

### 🏠 Setting BL (Base Language)
| Command | Example | Description |
| :--- | :--- | :--- |
| **`bl:<lang>`** | `bl:italian` | **Manual**: Resolves and forces the Base Language pointer. |
| **`bl:<iso>`** | `bl:it` | **Instant**: Immediately sets the BL using a 2-letter ISO code. |
| **Automatic** | *User Input* | **Dynamic**: Automatically updates to the detected language if it differs from the current TL. |

### 🎯 Setting TL (Target Language)
| Command | Example | Description |
| :--- | :--- | :--- |
| **`tl:<lang>`** | `tl:spanish` | **Manual**: Explicitly sets the default destination language. |
| **`tr:<lang>`** | `tr:en` | **Forced Context**: Translates the last message and updates the TL pointer. |
| **`trc:<lang> <text>`**| `trc:fr ...` | **Force & Inject**: Sets TL to `fr`, translates, and continues chat. |
| **`trs:<lang> <text>`**| `trs:de ...` | **Forced Summary**: Summarizes text directly in the specified ISO language. |

> 💡 **TIP: Database Sync**
> Pointers are saved in the chat metadata. If you refresh the page or return to a chat after days, EasyLang will remember exactly which languages you were using.

---

### 🔧 Configuration Parameters (Valves)

| Valve | Default | Description |
| :--- | :---: | :--- |
| **Translation Model** | (Current) | Defines the model for internal sub-calls. If empty, uses the active session model. |
| **Back Translation** | `False` | Enables recursive translation. Intercepts Assistant response and translates it back to `BL`. |
| **Debug** | `False` | Dumps state machines and execution logs to the console (`⚡ EASYLANG`). |

---

### 💡 Workflow Example (v0.2.3)

| User Input | Assistant Output| BL | TL | Description |
| :--- | :--- | :---: | :---: | :--- |
| `tr ciao` | hello | `it` | `en` | **Initial Anchor**: `it` detected as BL. Default TL (`en`) applied. |
| `trc:fr come va?` | *[LLM Response]* | `it` | `fr` | **Force & Inject**: Updates TL to `fr` in DB and sends translated prompt to LLM. |
| `trs` | *[Summary in FR]* | `it` | `fr` | **Context Summary**: Summarizes the last assistant response in `fr`. |
| `tr comment ça va?` | come va? | `it` | `fr` | **Symmetric Toggle**: Input is TL (`fr`), translates back to BL (`it`). |
| `tr Guten Tag` | hola | `de` | `fr` | **Dynamic Re-Anchoring**: New language `de` detected. BL becomes `de`, translates to current TL (`fr`). |
| `tl:en` | 🗹 Current TL switched...| `de` | `en` | **Manual Sync**: Forcing target to English for the rest of the chat. |

### ✨ Bonus Feature: Text Refinement

Since EasyLang re-processes the input through a high-fidelity LLM translation layer at `temperature: 0`, it acts as an automatic buffer that sanitizes typos and punctuation errors. If the input language matches the forced target, the pointers self-correct.

| User Input (with Typos) | Assistant Output| BL | TL | Description |
| :--- | :--- | :---: | :---: | :--- |
| `tl:en` | 🗹 TL set to: **en** | `any` | `en` | Manual target setup. |
| `tr:en Helo wordl,,, may mane is Hannibal!` | Hello world, my name is Hannibal! | `en` | `en` | **Self-Correction**: Input detected as `en`. Pointer updates to sync logic while LLM polishes text. |

> ℹ️ **NOTE: Real-Time Text Polisher**
> This effect effectively serves as a text polisher. The LLM corrects spelling and grammar while the filter ensures your language pointers stay synchronized with your actual speech.
---

### 📌 Performance Metrics
Upon completion, every translation displays a real-time telemetry status:
`IT ➔ EN | 0.64s | 73 tokens`

> ⚠️ **REASONING MODELS NOTICE**
> Using models like DeepSeek-R1 or o1 as the **Translation Model** valve will significantly increase latency and token costs. Use lightweight models (Gemma 2, Llama 3.2, GPT-4o-mini) for the best experience.

---

### ‼️ Early Release & Beta Notice

While the core logic is solid, it has not yet been extensively stress-tested for all possible edge cases. The filter is currently undergoing intensive development and testing. Please be patient with any anomalies or unexpected behavior. 

If you encounter bugs or logic errors, please open an [issue](https://github.com/annibale-x/Easylang/issues) on GitHub.