# LeetTrace

A [Python Tutor](https://pythontutor.com)–style **Mac app for LeetCode practice**, for **Python and C++**. Paste your solution and a test case, then step through the run one line at a time and watch your variables, pointers and recursive calls change.

<img src="docs/icon.png" width="96" alt="LeetTrace icon">

## Features

- **Code arrows**: green ➜ marks the line that just ran and orange ➜ marks the line that runs next. Lines where a caller is paused get a "⏸ waiting" tag.
- **Call stack**: one card per active call, newest on top. Changed variables flash, and return values are shown.
- **Data structures drawn visually**: binary trees as trees, linked lists as chains, 2D lists as grids, plus lists, dicts, sets and objects. Arrows point from each variable to what it references.
- **Recursion made visible**: tree nodes are colored *running / waiting / returned* and show their return values. A live **recursion tree** shows every call; click a call to jump to it.
- **Plain-English narration** of each step, e.g. "`checker(root=node 1, maxval=3)` returns 1; checker resumes at line 15", with a list of what changed.
- Buttons to jump to the next call, the previous call, or the end of the current call. Keyboard: ← → to step, Space to play, ⌘↵ to run.
- **⌘+ / ⌘− / ⌘0** to zoom the whole UI. Light and dark mode follow macOS.

## Install

### Option 1: download the app

1. Download `LeetTrace.zip` from the [latest release](../../releases/latest) and unzip it.
2. Drag **LeetTrace.app** into **Applications**.
3. The app isn't notarized by Apple, so macOS blocks the first launch. Clear the quarantine flag once:
   ```bash
   xattr -dr com.apple.quarantine /Applications/LeetTrace.app
   ```
   (Or right-click the app → **Open** → **Open**. On recent macOS: System Settings → Privacy & Security → **Open Anyway**.)

The download is built for **Apple Silicon** Macs. On an Intel Mac, build from source (Option 2).

### Option 2: build from source

Requires macOS 13+ and the Xcode Command Line Tools (`xcode-select --install`).

```bash
git clone https://github.com/Harsha-Venkatesh/leettrace.git
cd leettrace
./build.sh
cp -R build/LeetTrace.app /Applications/
```

### Requirements

**Python 3** (both languages):

LeetTrace runs your code with the Python 3 already on your Mac. It looks in Homebrew (`/opt/homebrew`, `/usr/local`), Anaconda/Miniconda, pyenv, python.org installs, and `/usr/bin/python3`. To use a specific interpreter, set `LEETTRACE_PYTHON`.

**For C++:** Apple's command line developer tools (`xcode-select --install`). LeetTrace compiles your code with the `clang++` that comes with them. No debugger or admin rights are needed.

## Usage

1. Pick **Python** or **C++** at the top of the **Code** box, then paste your LeetCode solution (`class Solution ...`).
2. Enter the test case in either format:
   - one argument per line:
     ```
     [3,1,4,3,null,1,5]
     ```
   - LeetCode's `name = value` form:
     ```
     nums = [2,7,11,15], target = 9
     ```
3. Press **Visualize** (⌘↵) and step with ← / →.

Notes:
- `TreeNode` / `ListNode` arguments are built from the lists automatically, using your type hints. You don't need to uncomment LeetCode's class definitions.
- `List`, `Optional`, `deque`, `defaultdict`, `heapq` and similar are pre-imported, as on LeetCode.
- If a class has several methods, LeetTrace picks the entry point automatically; you can change it from the dropdown.
- Design problems work too:
  ```
  ["MinStack","push","top"]
  [[],[5],[]]
  ```
- Runs stop after 2,500 steps or 10 seconds, so infinite loops won't hang the app.

C++ notes:
- `TreeNode`, `ListNode`, `using namespace std` and the whole standard library are provided, as on LeetCode. `#include` lines (including `<bits/stdc++.h>`) are ignored.
- You'll see vectors, 2D grids, maps, sets, `stack` / `queue` / `priority_queue`, pairs, tuples and the fields of your own classes. Lambdas, including recursive `function<>` and `auto&& self` ones, appear in the call stack like any other function.
- Runtime errors stop the run at the line that caused them, as LeetCode does: null-pointer access, index out of range, signed integer overflow and division by zero.
- Compiling adds a couple of seconds before the first step.

## How it works

| Path | What it does |
| --- | --- |
| `Sources/LeetTraceApp.swift` | SwiftUI window hosting a WKWebView; runs the tracer through a native message bridge |
| `tracer/tracer.py` | Runs your code under `sys.settrace` and emits a JSON trace of every step |
| `tracer/tracer_cpp.py` | C++: reads clang's AST, inserts a trace hook before every statement, loop test and return (keeping line numbers), generates a `main()` from the test case, then compiles and runs it |
| `tracer/lt_runtime.h` | C++: the LeetCode-style prelude plus the runtime the hooks call. It snapshots the call stack and every reachable object into the same JSON trace |
| `web/` | The visualizer UI (HTML/CSS/JS, no dependencies) |
| `devserver.py` | Serves `web/` in a normal browser for UI development: `python3 devserver.py` → http://localhost:8765 |
| `make_icon.py` | Regenerates `AppIcon.icns` (needs Pillow) |
