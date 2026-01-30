# 🌐 EasyLang: Easy Translation Assistant Filter

Open WebUI filter designed to orchestrate seamless multilingual communication. It manages smart translation workflows, automatic language anchoring, and real-time performance analytics.

[![GitHub Repo](https://img.shields.io/badge/GitHub-Repository-181717?logo=github&logoColor=white)](https://github.com/annibale-x/EasyLang)
![Open WebUI Plugin](https://img.shields.io/badge/Open%20WebUI-Plugin-blue?style=flat&logo=openai)
![License](https://img.shields.io/github/license/annibale-x/EasyLang?color=green)

⚠️ **IMPORTANT: Early Release & Beta Notice**
This project is only 3 days old. While the core logic is solid, it has not yet been extensively stress-tested for all possible edge cases. The filter is currently undergoing intensive development and testing. Please be patient with any anomalies or unexpected behavior. If you encounter bugs or logic errors, please open an [issue](https://github.com/annibale-x/Easylang/issues) on GitHub.

---

### 📖 The Philosophy

EasyLang was created for power users who operate in multi-language environments, removing the "copy-paste-translate" friction by baking the intelligence directly into the prompt bar. 

Whether you are debugging code in English or chatting in French, the filter adapts its anchoring logic to match your current cognitive flow.

---

### 💡 Usage & Command Schema

Every command containing `<text>` triggers a **Language Detection** routine (unless an ISO override is provided). The system dynamically compares the detected language with the `BL` (Base Language) and `TL` (Target Language) pointers.

| Command | Action | Logic / Behavioral Impact |
| :--- | :--- | :--- |
| **`tr <text>`** | **Dynamic Toggle** | Detects input: If `TL` → Translates to `BL`. Otherwise → Translates to `TL`. |
| **`tr`** | **Context Recovery** | Scrapes the last `assistant` message and executes a symmetric translation. |
| **`tr-<iso> <text>`** | **Forced Target** | Translates `<text>` directly to `<iso>` and updates the `TL` pointer. |
| **`tr-<iso>`** | **Forced Context** | Scrapes the last `assistant` message, translates to `<iso>`, and updates `TL`. |
| **`trc <text>`** | **Chat Continuation** | Translates input to `TL` (or `BL` if input is `TL`) and dispatches to the LLM. |
| **`bl <lang>` / `tl <lang>`** | **Manual Override** | Sets `BL` or `TL` using full names (LLM resolved) or 2-letter ISO codes. |
| **`bl` / `tl`** | **Pointer Query** | Returns the current value of the requested language pointer. |
| **`t?`** | **System Dashboard** | Displays `BL`/`TL` status, session metadata, and command reference. |

---

## ⚡ Language Updates Logic

Every translation command triggers a state check. Pointers are dynamic and designed to follow your conversational flow without manual intervention.

### 🏠 Setting BL (Base Language)
| Command | Example | Description |
| :--- | :--- | :--- |
| **`bl <lang>`** | `bl italian` | **Manual**: Resolves and forces the Base Language pointer. |
| **`bl <iso>`** | `bl it` | **Instant**: Immediately sets the BL using a 2-letter ISO code. |
| **Automatic** | *User Input* | **Dynamic**: Automatically updates to the detected language if it differs from the current TL. |

### 🎯 Setting TL (Target Language)
| Command | Example | Description |
| :--- | :--- | :--- |
| **`tl <lang>`** | `tl spanish` | **Manual**: Explicitly sets the default destination language. |
| **`tr-<iso>`** | `tr-en` | **Forced Context**: Translates the last message and updates the TL pointer. |
| **`tr-<iso> <text>`** | `tr-fr salut` | **Forced Target**: Translates text and overrides the TL pointer. |

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
| **Debug** | `True` | **Runtime Logging**. Dumps internal state machines (UID, CID, BL/TL pointers) and execution logs to the Docker/Standard Error console (`⚡ EASYLANG`). |

---

### ✨ Key Features

* **Dynamic Pivot Anchoring**: Implements a real-time state machine for language pointers. The system automatically re-anchors the Base Language (BL) and Target Language (TL) based on input detection, maintaining bidirectional symmetry without manual state management.
* **CoT Suppression (Anti-Thinking)**: Injects deterministic system-level directives to inhibit Chain-of-Thought (CoT) generation in reasoning models (e.g., DeepSeek-R1, o1). This forces immediate output and minimizes latency during detection and translation sub-tasks.
* **Surgical Response Sanitization**: Multi-stage Regex pipeline designed to strip XML artifacts (`<text>`) and reasoning blocks (`<think>`). Ensures clean payload delivery by removing non-content metadata generated by chatty or verbose models.
* **Unified Performance Telemetry**: Real-time instrumentation of the entire pipeline. Aggregates metrics from all internal sub-calls (Detection, Pivoting, Translation) to provide precise latency (seconds), cumulative token consumption, and effective throughput (**Tk/s**).
* **Recursive Back-Translation**: Optional secondary loop for response verification. Intercepts the assistant's output and executes a recursive translation back to the detected Base Language (BL) before final UI rendering.
* **Low-Latency ISO Bypass**: Hybrid resolution engine that prioritizes 2-letter ISO codes for instant configuration, falling back to LLM-driven dictionary resolution only for full-string language names.

---
### 📌 Output & Performance Metrics

Upon completion, every translation displays a real-time telemetry status:
`Done | 0.64s | 73 tokens | 110.3 Tk/s`

1. **Time**: Total round-trip latency including internal LLM calls and sanitization.
2. **Tokens**: **Cumulative** consumption across all internal pipeline stages.
3. **Speed**: Precise throughput (Tokens / Time).

> ⚠️ **WARNING: Performance Optimization**
> Reasoning models (e.g., DeepSeek-R1, o1, o3) are significantly slower and more token-intensive due to their internal architecture. To minimize latency and costs, it is highly recommended to set the **Translation Model** valve to a high-throughput, lightweight model.
>
> **Recommended for Translation Valve**:
> * `gpt-4o-mini` (High speed/accuracy balance)
> * `claude-3-haiku` (Excellent linguistic nuance)
> * `gemma2:9b` or `llama3.2:3b` (Best for local Ollama deployments)

---

### 💡 Workflow Example

| User Input | Assistant Output| TL | BL | Description |
| :--- | :--- | :---: | :---: | :--- |
| `tr ciao` | hello | `en` | `it` | **Inception**: `it` detected and anchored as BL. Default TL (`en`) applied. |
| `tr how are you` | come stai | `en` | `it` | **Symmetric Toggle**: Input matches TL (`en`), translates back to BL (`it`). |
| `tr-es ciao` | hola | `es` | `it` | **Forced Target**: ISO override updates TL to `es` and translates. |
| `tr` | ¿cómo estás? | `es` | `it` | **Context Recovery**: Scrapes last Assistant msg ("hola") and translates to opposite. |
| `tr-fr` | salut | `fr` | `it` | **Forced Context**: Scrapes assistant, translates to `fr`, and updates TL. |
| `trc comment ça va?` | *[LLM Response in FR]* | `fr` | `it` | **Chat Continuation**: Preserves user text in history, sends translation to LLM. |
| `tr Guten Tag` | salut | `fr` | `de` | **Dynamic Re-Anchoring**: New language `de` detected. It becomes the new BL. |
| `tl ja` | 🗹 TL set to: **ja** | `ja` | `de` | **Manual ISO Override**: TL pointer explicitly moved to Japanese. |
| `t?` | *[Status Dashboard]* | `ja` | `de` | **System Audit**: Displays current state and telemetry summary. |

### ✨ Bonus Feature: Text Refinement

Since EasyLang re-processes the input through a high-fidelity LLM translation layer at `temperature: 0`, it acts as an automatic buffer that sanitizes typos and punctuation errors. If the input language matches the forced target, the pointers self-correct.

| User Input (with Typos) | Assistant Output| TL | BL | Description |
| :--- | :--- | :---: | :---: | :--- |
| `tl en` | 🗹 TL set to: **en** | `en` | `any` | Manual target setup. |
| `tr-en Helo wordl,,, may mane is Hannibal!` | **Hello world, my name is Hannibal!** | `en` | `en` | **Self-Correction**: Input detected as `en`. Pointer updates to sync logic while LLM polishes text. |

> ℹ️ **NOTE: Real-Time Text Polisher**
> This effect effectively serves as a text polisher. The LLM corrects spelling and grammar while the filter ensures your language pointers stay synchronized with your actual speech.
