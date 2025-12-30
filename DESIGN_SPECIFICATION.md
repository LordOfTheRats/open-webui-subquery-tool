# Subquery Tool for Open WebUI - Design Specification

Version: 0.6.2  
Author: René Vögeli  
License: MIT  
Last Updated: 2025-12-30

---

## 1. Overview

### 1.1 Purpose

Subquery is an Open WebUI tool that performs native-mode subqueries using the same model that invoked it. It self-drives tool execution (multi-tool orchestration) via Open WebUI internals while preserving current chat persona, files/knowledge, and enabled tools (excluding this Subquery tool itself).

### 1.2 Key Features

- Native function calling: forces `function_calling="native"` to use OpenAI-style tool_calls
- Multi-tool orchestration: resolves and executes enabled tools, excluding Subquery
- Context control: optionally includes recent chat messages; preserves persona by not injecting a system message
- Files and metadata preservation: passes through files and metadata to subquery
- Robust tool call parsing:
  - Structured tool_calls (OpenAI native)
  - Fallback parser for inline XML-like function call blocks in assistant content
- Argument filtering: only passes kwargs accepted by target callable
- Async execution: awaits tool functions when applicable
- Safety and limits: bounded by `max_rounds` to avoid infinite loops

### 1.3 Target Use Cases

- Invoke a focused subtask with the same model, reusing current tool ecosystem
- Perform multi-step tool workflows driven by the model within a single subquery
- Run isolated reasoning without polluting the main chat context, yet optionally referencing recent messages

---

## 2. Architecture

### 2.1 Design Pattern

```python
class Tools:
    def __init__(self):
        self.max_rounds = 8

    async def subquery(...):
        ...
```

All behavior is centered around `Tools.subquery`. No Valves/config class is used in this tool.

### 2.2 Core Components

- Message Tail Handling (`_tail_messages`):
  - Extracts the last N messages, keeping only role/content for system/user/assistant
- Kwarg Filtering (`_filter_kwargs_for_callable`):
  - Filters call kwargs to match the target callable signature unless it accepts **kwargs
- Tool Call Normalization (`_normalize_tool_calls`):
  - Ensures each tool_call has a stable integer `index`
- Text Tool Call Extraction (`_extract_text_tool_calls`):
  - Parses assistant content for blocks:
    ```
    <function=NAME>
      <parameter=KEY>VALUE</parameter>
      ...
    </function>
    ```
  - Produces OpenAI-style tool_calls with JSON-encoded arguments
  - Trims assistant content before the first function block

### 2.3 Subquery Flow

1. Validate injected __request__, __user__, __model__
2. Build messages:
   - Optional tail of recent messages (to preserve persona without adding system prompts)
   - Append the user prompt
3. Derive params from __metadata__.params; force `function_calling="native"`
4. Compute tool_ids by taking __metadata__.tool_ids minus "subquery" (case-insensitive)
5. Resolve tools via `get_tools(__request__, tool_ids, user, extra_params)`
6. Loop up to `max_rounds`:
   - Call `chat_completion` with current messages and resolved tools
   - Read assistant message
   - Extract tool calls (structured or XML-like parsed)
   - If no tool calls: return assistant content
   - Append assistant tool_calls message
   - For each tool_call:
     - Find tool by name
     - Decode JSON arguments
     - Merge execution context (__user__, user, __metadata__, __messages__, __files__, __model__, __request__)
     - Filter kwargs to callable signature
     - Execute (await if needed)
     - Append tool result as a tool message with `tool_call_id`
7. If rounds exhausted: raise runtime error

---

## 3. API Surface

### 3.1 Subquery

```python
async def subquery(
    prompt: str,
    include_recent_messages: int = 0,
    __user__: dict | None = None,
    __metadata__: dict | None = None,
    __messages__: list | None = None,
    __files__: list | None = None,
    __model__: dict | None = None,
    __request__: Request | None = None,
) -> str
```

- Purpose: Execute a subquery with native tool-calling using the current model.
- Parameters:
  - prompt: The subquery prompt text
  - include_recent_messages: Number of tail messages to include; default 0
  - __user__, __metadata__, __messages__, __files__, __model__, __request__: Open WebUI-injected context objects
- Returns: Assistant text response (string). If tools were called, result is the final assistant content after tool execution loop.

---

## 4. Reliability & Error Handling

- Input validation:
  - Raises if __request__ or __user__ not injected
  - Raises if __model__ missing or lacks id
- Execution bounds:
  - `max_rounds` (default 8) prevents infinite loops
- Tool resolution:
  - If tool not found, appends a tool message indicating missing tool and continues
- Exceptions:
  - Unhandled exceptions are logged with traceback and re-raised

---

## 5. Integration Patterns

### 5.1 Open WebUI Usage

- Discovery: Place this tool in the tools directory; Open WebUI will register it
- Persona preservation: No system message injection; assistant persona remains intact
- Tool ecosystem: Subquery uses currently enabled tools except "Subquery" itself
- Files/knowledge: Files passed to subquery are forwarded to `chat_completion` unchanged

### 5.2 Example Invocations

- “Run a focused analysis using current tools on the uploaded files”
- “Summarize last page’s content, using available search tools, but keep persona”

---

## 6. Known Limitations

1. No configuration Valves; behavior is fixed aside from `max_rounds`
2. Only one public method (subquery); no direct external APIs
3. XML-like parser is simple and expects strict tags
4. Tool argument values parsed from text are strings unless the tool handles conversion
5. If models return non-native tool_calls unexpectedly, fallback parsing may be required
6. No streaming support (`stream=False` enforced)

---

## 7. Troubleshooting

- “[Subquery] __request__ was not injected”:
  - Ensure Open WebUI injects context when invoking the tool
- “could not determine current model id from __model__”:
  - Verify the calling model is correctly provided by Open WebUI
- “Tool 'NAME' not found”:
  - Confirm the tool is enabled and not excluded; Subquery itself is excluded by design
- “exceeded max_rounds=8”:
  - The model produced tool_calls repeatedly; adjust logic or prompt to converge

---

## 8. Version History

- 0.6.2 (Current):
  - Native orchestration, tool exclusion, XML-like tool call parsing, kwarg filtering
  - Persona-preserving message handling and round limit

---

Document End
