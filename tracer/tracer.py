#!/usr/bin/env python3
"""LeetTrace tracer.

Reads {"code", "testcase", "method"} as JSON on stdin, runs the LeetCode-style
solution against the test case under sys.settrace, and writes a JSON trace of
every step (stack frames, heap objects, call tree) to stdout.
"""
import ast
import builtins
import functools
import inspect
import io
import itertools
import json
import re
import sys
import time
import types
from collections import deque

USER_FILE = "<solution>"
MAX_STEPS = 2500
MAX_HEAP = 400
MAX_ITEMS = 60
TIME_LIMIT = 10.0

PRELUDE = '''
from typing import *
import collections, heapq, math, bisect, itertools, functools, string, re, random, operator
from collections import deque, defaultdict, Counter, OrderedDict
from heapq import heappush, heappop, heapify, heappushpop, nlargest, nsmallest
from functools import lru_cache, reduce
from itertools import accumulate, permutations, combinations, product
from math import inf, gcd, sqrt, ceil, floor, log2
from bisect import bisect_left, bisect_right, insort
try:
    from functools import cache
except ImportError:
    pass

class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right
    def __repr__(self):
        return "TreeNode(%r)" % (self.val,)

class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next
    def __repr__(self):
        return "ListNode(%r)" % (self.val,)
'''


class StopTrace(BaseException):
    """Raised from the trace function to stop runaway programs."""


class UserError(Exception):
    def __init__(self, msg, line=None):
        super().__init__(msg)
        self.msg = msg
        self.line = line


def is_tree(v):
    return (not isinstance(v, type) and hasattr(v, "val")
            and hasattr(v, "left") and hasattr(v, "right"))


def is_ll(v):
    return not isinstance(v, type) and hasattr(v, "val") and hasattr(v, "next") and not is_tree(v)


def is_prim(v):
    return v is None or isinstance(v, (bool, int, float, complex, str))


def is_callable_thing(v):
    return (isinstance(v, (types.FunctionType, types.BuiltinFunctionType, types.MethodType,
                           types.BuiltinMethodType, functools.partial))
            or type(v).__name__ == "_lru_cache_wrapper")


def short(v):
    """Compact text for call-tree labels. Returns (text, is_node)."""
    if is_tree(v) or is_ll(v):
        return "node %s" % short(v.val)[0], True
    if isinstance(v, str):
        r = repr(v)
        return (r if len(r) <= 18 else r[:16] + "…'"), False
    try:
        r = repr(v)
    except Exception:
        r = type(v).__name__
    return (r if len(r) <= 18 else r[:17] + "…"), False


# ---------------------------------------------------------------- test case


def split_top(s, sep=","):
    parts, depth, cur, quote = [], 0, [], None
    i = 0
    while i < len(s):
        c = s[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
            cur.append(c)
        elif c in "[({":
            depth += 1
            cur.append(c)
        elif c in "])}":
            depth -= 1
            cur.append(c)
        elif c == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def parse_value(s):
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    t = re.sub(r"\bnull\b", "None", s)
    t = re.sub(r"\btrue\b", "True", t)
    t = re.sub(r"\bfalse\b", "False", t)
    try:
        return ast.literal_eval(t)
    except Exception:
        raise UserError("Couldn't read this test-case value: %s" % s[:80])


def parse_testcase(text):
    """Returns (positional_values, named_values)."""
    text = re.sub(r"^\s*Input:\s*", "", text.strip(), flags=re.I)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if any(re.match(r"^[A-Za-z_]\w*\s*=", l) for l in lines):
        named = {}
        for part in split_top(",".join(lines)):
            if "=" not in part:
                raise UserError("Expected 'name = value' in test case, got: %s" % part[:60])
            k, v = part.split("=", 1)
            named[k.strip()] = parse_value(v)
        return None, named
    return [parse_value(l) for l in lines], None


def build_tree(vals, T):
    if not vals or vals[0] is None:
        return None
    it = iter(vals)
    root = T(next(it))
    q = deque([root])
    while q:
        node = q.popleft()
        for side in ("left", "right"):
            try:
                v = next(it)
            except StopIteration:
                return root
            if v is not None:
                child = T(v)
                setattr(node, side, child)
                q.append(child)
    return root


def build_ll(vals, L):
    head = None
    for v in reversed(vals):
        head = L(v, head)
    return head


def find_node(root, val):
    stack = [root]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if n.val == val:
            return n
        stack.extend([n.left, n.right])
    return None


TREE_NAMES = {"root", "root1", "root2", "tree"}
LL_NAMES = {"head", "l1", "l2", "list1", "list2", "head1", "head2", "heada", "headb"}


def ann_text(p):
    a = p.annotation
    if a is inspect.Parameter.empty:
        return ""
    if isinstance(a, str):
        return a
    return getattr(a, "__name__", None) if isinstance(a, type) else str(a)


def convert(value, ann, name, ns, built):
    T, L = ns.get("TreeNode"), ns.get("ListNode")
    lname = name.lower()
    tree_like = "TreeNode" in ann or (not ann and lname in TREE_NAMES)
    ll_like = "ListNode" in ann or (not ann and lname in LL_NAMES)
    if tree_like and isinstance(value, list):
        if value and all(isinstance(x, list) for x in value):
            return [build_tree(x, T) for x in value]
        return build_tree(value, T)
    if tree_like and is_prim(value) and value is not None:
        # e.g. lowestCommonAncestor(root, p, q): p/q are values of nodes in root
        for b in built:
            if is_tree(b):
                node = find_node(b, value)
                if node is not None:
                    return node
    if ll_like and isinstance(value, list):
        if value and all(isinstance(x, list) for x in value):
            return [build_ll(x, L) for x in value]
        return build_ll(value, L)
    return value


def display(v, depth=0):
    """LeetCode-style output text."""
    if depth > 20:
        return "…"
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v)
    if is_tree(v):
        out, q = [], deque([v])
        seen = set()
        while q and len(out) < 2000:
            n = q.popleft()
            if n is None or id(n) in seen:
                out.append(None)
                continue
            seen.add(id(n))
            out.append(n.val)
            q.append(n.left)
            q.append(n.right)
        while out and out[-1] is None:
            out.pop()
        return "[" + ",".join("null" if x is None else display(x, depth + 1) for x in out) + "]"
    if is_ll(v):
        out, seen = [], set()
        while v is not None and id(v) not in seen and len(out) < 2000:
            seen.add(id(v))
            out.append(display(v.val, depth + 1))
            v = v.next
        return "[" + ",".join(out) + "]"
    if isinstance(v, (list, tuple, deque)):
        return "[" + ",".join(display(x, depth + 1) for x in v) + "]"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return "%.5f" % v
    return repr(v)


# ---------------------------------------------------------------- entry point


def user_line(tb):
    line = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == USER_FILE:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


def pick_entry(code, names):
    """The entry is the function no other top-level function refers to."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return names[-1] if names else None
    bodies = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            bodies.setdefault(node.name, node)
    referenced = set()
    for n, fn in bodies.items():
        for sub in ast.walk(fn):
            ref = sub.attr if isinstance(sub, ast.Attribute) else sub.id if isinstance(sub, ast.Name) else None
            if ref in names and ref != n:
                referenced.add(ref)
    cands = [n for n in names if n not in referenced] or names
    annotated = [n for n in cands if n in bodies and any(a.annotation for a in bodies[n].args.args)]
    return (annotated or cands)[-1]


# ---------------------------------------------------------------- tracer


class Tracer:
    def __init__(self, out):
        self.out = out
        self.steps = []
        self.stack = []
        self.calls = []
        self.ids = {}
        self.keep = []
        self.next_fid = 0
        self.t0 = time.time()
        self.truncated = None

    def oid(self, v):
        k = id(v)
        if k not in self.ids:
            self.ids[k] = len(self.ids) + 1
            self.keep.append(v)  # keep alive so ids are never reused
        return self.ids[k]

    # -- trace hooks
    def global_trace(self, frame, event, arg):
        if frame.f_code.co_filename != USER_FILE:
            return None
        self.on_call(frame)
        return self.local_trace

    def local_trace(self, frame, event, arg):
        if event == "line":
            self.stack[-1]["exc"] = False
            self.record(frame, "line")
        elif event == "return":
            self.record(frame, "return", arg)
            entry = self.stack.pop()
            call = entry["call"]
            call["e"] = len(self.steps) - 1
            call["ret"] = "exception" if entry.get("exc") else short(arg)[0]
        elif event == "exception":
            self.stack[-1]["exc"] = True
            self.record(frame, "exception", arg)
        return self.local_trace

    def on_call(self, frame):
        code = frame.f_code
        fid = self.next_fid
        self.next_fid += 1
        nargs = code.co_argcount + code.co_kwonlyargcount
        nargs += bool(code.co_flags & inspect.CO_VARARGS) + bool(code.co_flags & inspect.CO_VARKEYWORDS)
        args, refs = [], []
        locs = frame.f_locals
        for a in code.co_varnames[:nargs]:
            if a == "self" or a not in locs:
                continue
            v = locs[a]
            text, node = short(v)
            args.append([a, text, node])
            if node:
                refs.append(self.oid(v))
        call = {"id": fid, "p": self.stack[-1]["fid"] if self.stack else None,
                "n": code.co_name, "a": args, "r": refs,
                "s": len(self.steps), "e": None, "ret": None}
        self.calls.append(call)
        self.stack.append({"frame": frame, "fid": fid, "call": call, "exc": False})
        self.record(frame, "call")

    # -- snapshots
    def record(self, frame, ev, arg=None):
        if len(self.steps) >= MAX_STEPS:
            self.truncated = "steps"
            raise StopTrace()
        if time.time() - self.t0 > TIME_LIMIT:
            self.truncated = "time"
            raise StopTrace()
        heap, queue = {}, deque()

        def ref(v):
            if v is None or isinstance(v, (bool, int, float, complex)):
                return ["p", repr(v)]
            if isinstance(v, str):
                return ["p", repr(v) if len(v) <= 60 else repr(v[:57]) + "…"]
            if isinstance(v, tuple) and len(v) <= 4 and all(
                    x is None or isinstance(x, (bool, int, float, str)) for x in v):
                return ["p", repr(v)]
            if is_callable_thing(v):
                return ["f", "function " + getattr(v, "__name__", "?")]
            if isinstance(v, type):
                return ["f", "class " + v.__name__]
            if isinstance(v, types.ModuleType):
                return ["f", "module " + v.__name__]
            o = self.oid(v)
            if o not in heap:
                heap[o] = None
                queue.append((o, v))
            return ["r", o]

        stack = []
        for e in self.stack:
            f = e["frame"]
            free = set(f.f_code.co_freevars)
            vs = [[k, ref(v)] for k, v in f.f_locals.items()
                  if k not in free and not k.startswith("__") and not k.startswith(".")]
            stack.append({"id": e["fid"], "n": f.f_code.co_name, "ln": f.f_lineno, "v": vs})
        if ev == "return":
            stack[-1]["ret"] = ref(arg)

        while queue:
            o, v = queue.popleft()
            if len(heap) > MAX_HEAP:
                heap[o] = {"k": "more"}
                continue
            heap[o] = self.describe(v, ref)

        step = {"e": ev, "ln": frame.f_lineno, "s": stack, "h": heap, "o": self.out.tell()}
        if ev == "exception":
            step["x"] = "%s: %s" % (arg[0].__name__, arg[1])
        self.steps.append(step)

    def describe(self, v, ref):
        cls = type(v).__name__
        try:
            if is_tree(v):
                d = {"k": "tree", "c": cls, "val": ref(v.val), "l": ref(v.left), "r": ref(v.right)}
                return d
            if is_ll(v):
                return {"k": "ll", "c": cls, "val": ref(v.val), "n": ref(v.next)}
            if isinstance(v, (list, tuple, deque)):
                items = list(itertools.islice(v, MAX_ITEMS))
                return {"k": "list", "c": cls, "i": [ref(x) for x in items], "len": len(v)}
            if isinstance(v, (set, frozenset)):
                items = list(itertools.islice(v, MAX_ITEMS))
                try:
                    items.sort()
                except Exception:
                    pass
                return {"k": "set", "c": cls, "i": [ref(x) for x in items], "len": len(v)}
            if isinstance(v, dict):
                items = list(itertools.islice(v.items(), MAX_ITEMS))
                return {"k": "dict", "c": cls, "i": [[ref(a), ref(b)] for a, b in items], "len": len(v)}
            if hasattr(v, "__dict__"):
                return {"k": "obj", "c": cls,
                        "a": [[k, ref(x)] for k, x in vars(v).items() if not k.startswith("__")]}
            return {"k": "raw", "c": cls, "v": repr(v)[:80]}
        except Exception as ex:  # never let a weird object kill the trace
            return {"k": "raw", "c": cls, "v": "<%s>" % type(ex).__name__}


# ---------------------------------------------------------------- run


def err(msg, line=None):
    return {"ok": False, "error": {"msg": msg, "line": line}}


def run(req):
    code = req.get("code") or ""
    tc = req.get("testcase") or ""
    want = req.get("method") or None
    if not code.strip():
        return err("Paste your solution code first.")

    ns = {"__name__": "__main__", "__builtins__": builtins}
    exec(PRELUDE, ns)
    try:
        compiled = compile(code, USER_FILE, "exec")
    except SyntaxError as e:
        return err("SyntaxError: %s" % e.msg, e.lineno)

    out = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = out
    try:
        exec(compiled, ns)
    except Exception as e:
        return err("%s: %s" % (type(e).__name__, e), user_line(e.__traceback__))
    finally:
        sys.stdout = real_stdout

    # --- what to call
    design = None
    lines = [l for l in tc.strip().splitlines() if l.strip()]
    if len(lines) == 2:
        try:
            a, b = parse_value(lines[0]), parse_value(lines[1])
            if (isinstance(a, list) and a and all(isinstance(x, str) for x in a)
                    and isinstance(b, list) and len(a) == len(b)
                    and isinstance(ns.get(a[0]), type)):
                design = (ns[a[0]], a, b)
        except UserError:
            pass

    arg_display = []
    methods = []
    entry = None
    if design:
        cls, ops, params = design
        entry = cls.__name__
        arg_display = [["operations", display(ops)], ["arguments", display(params)]]

        def call():
            obj, outs = None, []
            for i, (m, p) in enumerate(zip(ops, params)):
                if i == 0:
                    obj = cls(*p)
                    outs.append(None)
                else:
                    outs.append(getattr(obj, m)(*p))
            return outs
    else:
        sol = ns.get("Solution")
        if isinstance(sol, type):
            methods = [n for n, f in vars(sol).items()
                       if isinstance(f, (types.FunctionType, staticmethod, classmethod))
                       and not n.startswith("__")]
            owner = sol()
        else:
            methods = [n for n, f in ns.items()
                       if isinstance(f, types.FunctionType) and f.__code__.co_filename == USER_FILE]
            owner = None
        if not methods:
            return err("Couldn't find a function to run. Paste a LeetCode `class Solution` or a function.")
        entry = want if want in methods else pick_entry(code, methods)
        fn = getattr(owner, entry) if owner is not None else ns[entry]

        try:
            positional, named = parse_testcase(tc)
            params = list(inspect.signature(fn).parameters.values())
            params = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
            if named is not None:
                missing = [p.name for p in params if p.name not in named and p.default is p.empty]
                if missing:
                    raise UserError("Test case is missing: %s" % ", ".join(missing))
                raw = [(p, named[p.name]) for p in params if p.name in named]
            else:
                required = [p for p in params if p.default is p.empty]
                if not (len(required) <= len(positional) <= len(params)):
                    names = ", ".join(p.name for p in params)
                    raise UserError("%s(%s) needs %d input line(s) but the test case has %d. "
                                    "Put each argument on its own line." %
                                    (entry, names, len(required), len(positional)))
                raw = list(zip(params, positional))
        except UserError as e:
            return err(e.msg, e.line)

        built = []
        for p, v in raw:
            built.append(convert(v, ann_text(p), p.name, ns, built))
        arg_display = [[p.name, display(v)] for (p, v) in raw]
        kwargs = {p.name: b for (p, _), b in zip(raw, built)}

        def call():
            return fn(**kwargs)

    tracer = Tracer(out)
    result, error = None, None
    sys.stdout = out
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 3000))
    sys.settrace(tracer.global_trace)
    try:
        result = call()
    except StopTrace:
        pass
    except Exception as e:
        error = {"msg": "%s: %s" % (type(e).__name__, e), "line": user_line(e.__traceback__)}
    finally:
        sys.settrace(None)
        sys.stdout = real_stdout
        sys.setrecursionlimit(old_limit)

    if not tracer.steps:
        return err(error["msg"] if error else "Nothing ran — no steps were recorded.",
                   error and error.get("line"))

    finished = error is None and tracer.truncated is None
    return {
        "ok": True,
        "entry": entry,
        "methods": methods,
        "args": arg_display,
        "result": display(result) if finished else None,
        "stdout": out.getvalue(),
        "error": error,
        "truncated": tracer.truncated,
        "maxSteps": MAX_STEPS,
        "steps": tracer.steps,
        "calls": tracer.calls,
    }


def main():
    try:
        req = json.loads(sys.stdin.read() or "{}")
        res = run(req)
    except Exception as e:  # last-resort guard: always answer with JSON
        res = err("Tracer crashed: %s: %s" % (type(e).__name__, e))
    sys.__stdout__.write(json.dumps(res, separators=(",", ":")))


if __name__ == "__main__":
    main()
