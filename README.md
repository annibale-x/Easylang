
# 🌐 EasyLang: Easy Translation Assistant Filter

State-aware translation assistant for Open WebUI. Features smart bidirectional toggling, context-based summarization, and precision performance tracking.


[![GitHub Repo](https://img.shields.io/badge/GitHub-Repository-181717?logo=github&logoColor=white)](https://github.com/annibale-x/EasyLang)
![Open WebUI Plugin](https://img.shields.io/badge/Open%20WebUI-Plugin-blue?style=flat&logo=openai)
![License](https://img.shields.io/github/license/annibale-x/EasyLang?color=green)

---

### 🚀 At a Glance: What can you do?

-   **Zero-Click Translation**: Just type `tr <text>` and let the filter figure out if it needs to translate to your target language or back to your base language.
    
-   **Smart "Context Scraper"**: Type `tr` (empty) to instantly translate the last message sent by the Assistant without copying and pasting.
    
-   **Contextual Summarization**: Use `trs` to get a bulleted summary of long texts or previous assistant answers in your preferred language.
    
-   **Bypass the LLM**: Translate text directly in the chat bar without triggering a full LLM response, saving time and tokens.
    
-   **Deep Language Injection**: Use `trc` to translate your thought and feed it directly to the model, allowing you to chat in a language you don't speak fluently.
    
-   **Automatic Re-Anchoring**: Switch your speaking language mid-conversation; EasyLang detects the change and updates your "Base Language" pointer automatically.
    
-   **Real-Time Telemetry**: Monitor exactly how many tokens you are spending and the latency of every single translation.

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
| **`trs <text>`** | **Summarize** | **[NEW]** Translates and summarizes text in `TL` using bullet points. |
| **`trs`** | **Context Summary** | Summarizes the last `assistant` message in the target language. |
| **`trc <text>`** | **Chat Continuation** | Translates input and injects it into the LLM stream to continue the chat. |
| **`<cmd>:<lang>`** | **Force Language** | Valid for `tr:`, `trs:`, `trc:`. Overrides `TL` and updates the pointer. |
| **`bl:<lang>` / `tl:<lang>`** | **Manual Set** | Sets `BL` or `TL` using full names or 2-letter ISO codes. |
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

> 💡 **TIP: Dynamic Re-Anchoring & Symmetry**
> EasyLang automatically syncs pointers based on your input to maintain a perfect toggle.
> * **The Toggle Rule**: If you speak **BL**, it translates to **TL**. If you speak **TL**, it translates back to **BL**.
> * **Auto-Update**: If you speak a **NEW** language (neither BL nor TL), that language immediately becomes the new **BL**, and the system translates it to the current **TL**, keeping the session synchronized.

---

### 🔧 Configuration Parameters (Valves) v0.8.8

| Valve | Default | Description |
| :--- | :---: | :--- |
| **Translation Model** | (Current) | Defines the model for internal sub-calls (Detection/Translation). If empty, the filter uses the active session model. |
| **Back Translation** | `False` | Enables a recursive translation loop. Intercepts the Assistant response and translates it back to the current Base Language (BL). |
| **Debug** | `False` | **Runtime Logging**. Dumps internal state machines (UID, CID, BL/TL pointers) and execution logs to the Docker/Standard Error console (`⚡ EASYLANG`). |

---

### 📌 Output & Performance Metrics

Upon completion, every translation displays a real-time telemetry status:
`IT ➔ EN | 0.64s | 73 tokens`

1. **Direction**: Shows the flow (e.g., `BASE ➔ TARGET` or `BACK-TRANSLATION`).
2. **Time**: Total round-trip latency including internal LLM calls.
3. **Tokens**: **Cumulative** token consumption across all middleware stages (Detection + Translation).

> ⚠️ **WARNING: Performance Optimization**
> Reasoning models (e.g., DeepSeek-R1, o1, o3) are significantly slower and more token-intensive due to their internal architecture. To minimize latency and costs, it is highly recommended to set the **Translation Model** valve to a high-throughput, lightweight model.
>
> **Recommended for Translation Valve**:
> * `gpt-4o-mini` (High speed/accuracy balance)
> * `claude-3-haiku` (Excellent linguistic nuance)
> * `gemma2:9b` or `llama3.2:3b` (Best for local Ollama deployments)

---

### 💡 Workflow Example (v0.2.0)

| User Input | Assistant Output| TL | BL | Description |
| :--- | :--- | :---: | :---: | :--- |
| `tr ciao` | hello | `en` | `it` | **Initial Anchor**: `it` detected as BL. Default TL (`en`) applied. |
| `trc:fr come va?` | *[LLM Response]* | `fr` | `it` | **Force & Inject**: Sets TL to `fr`, translates, and sends to LLM. |
| `trs ciao...` | *[Summary in FR]* | `fr` | `it` | **Summarization**: Creates a bulleted summary in the current TL. |
| `tr how are you` | come stai | `fr` | `it` | **Symmetric Toggle**: Input matches TL, translates back to BL. |
| `tr:es ciao` | hola | `es` | `it` | **Forced Target**: `:` override updates TL to `es` and translates. |
| `tr Hola!` | ciao | `es` | `es` | **Self-Correction**: Input is `es`, pointers sync and text is refined. |
| `tr Guten Tag` | hola | `es` | `de` | **Dynamic Re-Anchoring**: New language `de` detected and set as new BL. |

### ✨ Bonus Feature: Text Refinement

Since EasyLang re-processes the input through a high-fidelity LLM translation layer at `temperature: 0`, it acts as an automatic buffer that sanitizes typos and punctuation errors. If the input language matches the forced target, the pointers self-correct.

| User Input (with Typos) | Assistant Output| TL | BL | Description |
| :--- | :--- | :---: | :---: | :--- |
| `tl:en` | 🗹 TL set to: **en** | `en` | `any` | Manual target setup. |
| `tr:en Helo wordl,,, may mane is Hannibal!` | Hello world, my name is Hannibal! | `en` | `en` | **Self-Correction**: Input detected as `en`. Pointer updates to sync logic while LLM polishes text. |

> ℹ️ **NOTE: Real-Time Text Polisher**
> This effect effectively serves as a text polisher. The LLM corrects spelling and grammar while the filter ensures your language pointers stay synchronized with your actual speech.

---

### ‼️ Early Release & Beta Notice**
While the core logic is solid, it has not yet been extensively stress-tested for all possible edge cases. The filter is currently undergoing intensive development and testing. Please be patient with any anomalies or unexpected behavior. 

If you encounter bugs or logic errors, please open an [issue](https://github.com/annibale-x/Easylang/issues) on GitHub.