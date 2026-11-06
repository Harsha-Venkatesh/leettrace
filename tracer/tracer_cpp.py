"""LeetTrace C++ tracer.

Same request/response as tracer.py, for C++ solutions. There is no settrace in
C++ and attaching a debugger needs admin rights on macOS, so instead the code is
instrumented at the source level:

  1. clang dumps the AST of the solution (wrapped in a namespace so the dump can
     be filtered to just the user's code);
  2. hooks are inserted at every function entry, statement, loop test and return
     (`__lt_fr.line(12, {__lt::V("i", i), ...})`) without adding any newlines, so
     line numbers stay the same as in the editor;
  3. a main() is generated that builds the test-case arguments and calls the
     entry point;
  4. the program is compiled and run, and lt_runtime.h writes the trace as JSON.
"""
import bisect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tracer import MAX_STEPS, UserError, display, err, parse_testcase, parse_value, split_top  # noqa: E402

RUNTIME = os.path.join(HERE, "lt_runtime.h")
NS = "__lt_user"
PREFIX = "namespace %s { " % NS
STD = "-std=gnu++20"
COMPILE_FLAGS = [STD, "-O0", "-w", "-fno-omit-frame-pointer",
                 "-D_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_FAST",
                 "-fsanitize=signed-integer-overflow,integer-divide-by-zero,null,return,shift",
                 "-fsanitize-trap=all"]
RUN_TIMEOUT = 20

FRAME_KEY = (1, -10 ** 6)   # the Frame declaration comes first at a body's '{'
END_KEY = (2, 0)            # the end-of-body hook comes last before its '}'


def find_cxx():
    env = os.environ.get("LEETTRACE_CXX")
    if env and os.access(env, os.X_OK):
        return env
    # /usr/bin/clang++ is a stub that pops an installer when the tools are missing
    try:
        ok = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        ok = False
    if ok and os.access("/usr/bin/clang++", os.X_OK):
        return "/usr/bin/clang++"
    return shutil.which("clang++") if not ok else None


# ---------------------------------------------------------------- AST helpers


def kids(n):
    return [c for c in (n.get("inner") or []) if c.get("kind")]


def loc_off(loc):
    if not loc:
        return None
    if "expansionLoc" in loc:
        loc = loc["expansionLoc"]
    return loc.get("offset")


def loc_end(loc):
    if not loc:
        return None
    if "expansionLoc" in loc:
        loc = loc["expansionLoc"]
    if "offset" not in loc:
        return None
    return loc["offset"] + loc.get("tokLen", 0)


def rng(n):
    r = n.get("range") or {}
    return loc_off(r.get("begin")), loc_end(r.get("end"))


def qual(n):
    return (n.get("type") or {}).get("qualType", "")


def is_fn_type(t):
    return ("(lambda at" in t or re.match(r"^(const\s+)?(std::)?function<", t) is not None
            or "(*)" in t)


def ret_type(fn_type):
    """'vector<int> (vector<int> &, int)' -> 'vector<int>'; None when deduced."""
    depth = 0
    for i, c in enumerate(fn_type):
        if c == "<":
            depth += 1
        elif c == ">":
            depth -= 1
        elif c == "(" and depth == 0:
            head = fn_type[:i].strip()
            if head == "auto":
                m = re.search(r"->\s*(.+)$", fn_type)
                return m.group(1).strip() if m else None
            return head or None
    return None


def referenced_names(n, out):
    k = n.get("kind")
    if k == "MemberExpr" and n.get("name"):
        out.add(n["name"])
    elif k == "DeclRefExpr":
        out.add((n.get("referencedDecl") or {}).get("name"))
    for c in kids(n):
        referenced_names(c, out)
    return out


# ---------------------------------------------------------------- instrumenter


class Instrumenter:
    def __init__(self, src):
        self.src = src
        self.edits = []
        self.starts = [0] + [m.end() for m in re.finditer("\n", src)]
        self.bodies = set()
        self.rets = []
        self.classes = {}      # name -> {"tag", "fields", "methods": [...], "ctors": [...]}
        self.functions = []    # free functions: {"name", "params", "ret", "body"}

    def line(self, off):
        return bisect.bisect_right(self.starts, off)

    def add(self, off, key, text):
        self.edits.append((off, key, len(self.edits), text))

    def apply(self):
        out, last = [], 0
        for off, _, _, text in sorted(self.edits):
            out.append(self.src[last:off])
            out.append(text)
            last = off
        out.append(self.src[last:])
        return "".join(out)

    def stmt_end(self, n):
        """Offset just past a statement, including its ';'."""
        e = rng(n)[1]
        if e is None:
            return None
        if self.src[e - 1] == ";":
            return e
        i, s = e, self.src
        while i < len(s):
            if s[i].isspace():
                i += 1
            elif s.startswith("//", i):
                j = s.find("\n", i)
                i = len(s) if j < 0 else j
            elif s.startswith("/*", i):
                j = s.find("*/", i + 2)
                i = len(s) if j < 0 else j + 2
            else:
                break
        return i + 1 if i < len(s) and s[i] == ";" else e

    # -- variable lists
    @staticmethod
    def visible(scopes):
        seen, out = set(), []
        for name, kind in reversed([v for sc in scopes for v in sc]):
            if name not in seen:
                seen.add(name)
                out.append((name, kind))
        return out[::-1]

    def vars_text(self, scopes):
        parts = []
        for name, kind in self.visible(scopes):
            if kind == "this":
                parts.append("__lt::VThis(this)")
            elif kind == "f":
                parts.append('__lt::F("%s")' % name)
            else:
                parts.append('__lt::V("%s", %s)' % (name, name))
        return "{" + ", ".join(parts) + "}"

    def hook(self, ln, scopes):
        return "__lt_fr.line(%d, %s);" % (ln, self.vars_text(scopes))

    # -- declarations
    def decl(self, n, owner=None):
        k = n.get("kind")
        if n.get("isImplicit"):
            return
        if k in ("NamespaceDecl", "LinkageSpecDecl"):
            for c in kids(n):
                self.decl(c)
        elif k == "CXXRecordDecl":
            self.record(n)
        elif k == "ClassTemplateDecl":
            for c in kids(n):
                if c.get("kind") == "CXXRecordDecl":
                    self.record(c)
                    break
        elif k == "FunctionTemplateDecl":
            for c in kids(n):
                if c.get("kind") in ("FunctionDecl", "CXXMethodDecl", "CXXConstructorDecl"):
                    self.decl(c, owner)
                    break
        elif k in ("FunctionDecl", "CXXMethodDecl", "CXXConstructorDecl"):
            self.function(n, owner)
        elif k in ("VarDecl", "FieldDecl"):
            for c in kids(n):
                self.expr(c, n.get("name"))

    def record(self, n):
        if not n.get("completeDefinition") or n.get("tagUsed") == "union":
            return
        name, tag = n.get("name") or "", n.get("tagUsed")
        fields = [c["name"] for c in kids(n) if c["kind"] == "FieldDecl" and c.get("name")]
        info = {"tag": tag, "fields": fields, "methods": [], "ctors": []}
        if name:
            self.classes.setdefault(name, info)
        if fields:
            b = rng(n)[0]
            brace = self.src.find("{", loc_off(n.get("loc")) or b)
            if brace >= 0:
                text = "public: void __lt_fields(::__lt::Fields& __f) const { %s} %s" % (
                    "".join('__f("%s", %s); ' % (f, f) for f in fields),
                    "private: " if tag == "class" else "")
                self.add(brace + 1, FRAME_KEY, text)
        access = "private" if tag == "class" else "public"
        owner = {"name": name, "fields": bool(fields), "info": info}
        for c in kids(n):
            k = c.get("kind")
            if k == "AccessSpecDecl":
                access = c.get("access", access)
            elif k in ("CXXMethodDecl", "CXXConstructorDecl", "FunctionTemplateDecl"):
                target = c
                if k == "FunctionTemplateDecl":
                    target = next((x for x in kids(c) if x["kind"] in ("CXXMethodDecl", "CXXConstructorDecl")), None)
                if target is None or target.get("isImplicit"):
                    continue
                params = [(p.get("name") or "", qual(p)) for p in kids(target) if p["kind"] == "ParmVarDecl"]
                entry = {"name": target.get("name"), "access": access, "params": params,
                         "ret": ret_type(qual(target)), "node": target}
                (info["ctors"] if target["kind"] == "CXXConstructorDecl" else info["methods"]).append(entry)
                self.decl(c, owner)
            elif k in ("CXXRecordDecl", "ClassTemplateDecl"):
                self.decl(c)
            elif k in ("FieldDecl", "VarDecl"):
                self.decl(c)

    def function(self, n, owner):
        if n.get("isImplicit") or n.get("constexpr") or n.get("consteval"):
            return
        body = next((c for c in reversed(kids(n)) if c["kind"] == "CompoundStmt"), None)
        params = [p for p in kids(n) if p["kind"] == "ParmVarDecl"]
        if owner is None and n["kind"] == "FunctionDecl" and n.get("name") != "main":
            self.functions.append({"name": n.get("name"), "params": [(p.get("name") or "", qual(p)) for p in params],
                                   "ret": ret_type(qual(n)), "node": n})
        if body is None:
            return
        name = n.get("name") or "?"
        if n["kind"] == "CXXConstructorDecl" and owner:
            name = owner["name"]
        scope = []
        if (n["kind"] in ("CXXMethodDecl", "CXXConstructorDecl") and owner and owner["fields"]
                and n.get("storageClass") != "static"):
            scope.append(("this", "this"))
        for p in params:
            if p.get("name"):
                scope.append((p["name"], "f" if is_fn_type(qual(p)) else "v"))
        decl_off = loc_off(n.get("loc")) or rng(n)[0]
        self.body(name, decl_off, scope, body, ret_type(qual(n)))

    def body(self, name, decl_off, scope, body, rtype):
        b0, b1 = rng(body)
        if b0 is None or b1 is None or b0 in self.bodies:
            return
        self.bodies.add(b0)
        scopes = [scope]
        self.add(b0 + 1, FRAME_KEY, ' __lt::Frame __lt_fr("%s", %d, %s); ' % (
            name, self.line(decl_off), self.vars_text(scopes)))
        self.rets.append(rtype)
        self.compound(body, scopes, 1)
        self.rets.pop()
        self.add(b1 - 1, END_KEY, " __lt_fr.end(%d); " % self.line(b1 - 1))

    def lambda_(self, n, hint):
        body = next((c for c in reversed(kids(n)) if c["kind"] == "CompoundStmt"), None)
        if body is None:
            return
        method = None

        def find(x):
            nonlocal method
            for c in kids(x):
                if method is None and c["kind"] == "CXXMethodDecl" and c.get("name") == "operator()":
                    method = c
                elif c["kind"] in ("CXXRecordDecl", "FunctionTemplateDecl"):
                    find(c)
        for c in kids(n):
            if c["kind"] == "CXXRecordDecl":
                find(c)
        scope = []
        rtype = None
        if method is not None:
            for p in kids(method):
                if p["kind"] == "ParmVarDecl" and p.get("name"):
                    scope.append((p["name"], "f" if is_fn_type(qual(p)) else "v"))
            rtype = ret_type(qual(method))
        self.body(hint or "lambda", rng(n)[0], scope, body, rtype)

    # -- expressions: only looked at for lambdas
    WRAPPERS = {"ExprWithCleanups", "CXXConstructExpr", "MaterializeTemporaryExpr", "CXXBindTemporaryExpr",
                "ImplicitCastExpr", "CXXFunctionalCastExpr", "ParenExpr", "CXXStaticCastExpr", "InitListExpr"}

    def expr(self, n, hint=None):
        k = n.get("kind")
        if k == "LambdaExpr":
            self.lambda_(n, hint)
            return
        if k in ("CompoundStmt", "CXXRecordDecl"):
            return
        sub = hint if k in self.WRAPPERS else None
        for c in kids(n):
            self.expr(c, sub)

    # -- statements
    def compound(self, n, scopes, depth):
        scopes = scopes + [[]]
        for c in kids(n):
            self.stmt(c, scopes, depth, True)

    def wrap(self, n, scopes, depth, extra=""):
        """A loop/if body: braces get added around a lone statement so hooks can go in front of it."""
        if n.get("kind") == "CompoundStmt":
            b0 = rng(n)[0]
            if extra and b0 is not None:
                self.add(b0 + 1, (1, depth), " " + extra + " ")
            self.compound(n, scopes, depth + 1)
            return
        b, e = rng(n)[0], self.stmt_end(n)
        if b is None or e is None:
            return
        self.add(b, (1, depth), "{ " + (extra + " " if extra else ""))
        self.stmt(n, scopes + [[]], depth + 1, True)
        self.add(e, (0, -depth), " }")

    def declare(self, n, scope):
        """Adds the variables of a DeclStmt (or a lone VarDecl) to `scope`."""
        for d in ([n] if n.get("kind") != "DeclStmt" else kids(n)):
            k = d.get("kind")
            if k == "VarDecl" and d.get("name"):
                for c in kids(d):
                    self.expr(c, d["name"])
                if not d.get("isImplicit") and not d["name"].startswith("__"):
                    scope.append((d["name"], "f" if is_fn_type(qual(d)) else "v"))
            elif k == "DecompositionDecl":
                for c in kids(d):
                    if c["kind"] == "BindingDecl" and c.get("name"):
                        scope.append((c["name"], "v"))
                    else:
                        self.expr(c)
            elif k == "CXXRecordDecl":
                self.record(d)

    def cond_hook(self, cond, scopes, depth, ln):
        b, e = rng(cond)
        if b is None or e is None:
            return False
        self.expr(cond)
        self.add(b, (1, depth), "(%s, " % self.hook(ln, scopes)[:-1])
        self.add(e, (0, -depth), ")")
        return True

    def stmt(self, n, scopes, depth, hook):
        k = n.get("kind")
        if not k or k == "NullStmt":
            return
        b, _ = rng(n)
        if b is None:
            return
        ln = self.line(b)
        if k in ("CaseStmt", "DefaultStmt", "LabelStmt", "AttributedStmt"):
            sub = kids(n)
            if sub:
                self.stmt(sub[-1], scopes, depth, hook)
            return
        inner = n.get("inner") or []
        loop_cond = None
        if k == "ForStmt" and len(inner) == 5 and inner[2].get("kind"):
            loop_cond = inner[2]
        elif k == "WhileStmt" and len([c for c in inner if c.get("kind")]) == 2:
            loop_cond = kids(n)[0]
        skip = k in ("CompoundStmt", "CXXTryStmt", "CXXForRangeStmt") or loop_cond is not None
        if hook and not skip:
            self.add(b, (1, depth), self.hook(ln, scopes) + " ")

        if k == "CompoundStmt":
            self.compound(n, scopes, depth + 1)
        elif k == "DeclStmt":
            self.declare(n, scopes[-1])
        elif k == "IfStmt":
            ks = kids(n)
            has_else = bool(n.get("hasElse"))
            branches = ks[-2:] if has_else else ks[-1:]
            head = ks[:len(ks) - len(branches)]
            local = []
            for h in head:
                if h["kind"] in ("DeclStmt", "VarDecl"):
                    self.declare(h, local)
                else:
                    self.expr(h)
            for br in branches:
                self.wrap(br, scopes + [local], depth)
        elif k == "ForStmt":
            init, _, cond, inc, body = (inner + [{}] * 5)[:5]
            local = []
            if init.get("kind") == "DeclStmt":
                self.declare(init, local)
            elif init.get("kind"):
                self.expr(init)
            if loop_cond is not None:
                self.cond_hook(cond, scopes + [local], depth, ln)
            if inc.get("kind"):
                self.expr(inc)
            self.wrap(body, scopes + [local], depth)
        elif k == "WhileStmt":
            ks = kids(n)
            local = []
            for h in ks[:-1]:
                if h["kind"] in ("DeclStmt", "VarDecl"):
                    self.declare(h, local)
            if loop_cond is not None:
                self.cond_hook(loop_cond, scopes, depth, ln)
            self.wrap(ks[-1], scopes + [local], depth)
        elif k == "DoStmt":
            body, cond = kids(n)[:2]
            self.wrap(body, scopes, depth)
            self.cond_hook(cond, scopes, depth, self.line(rng(cond)[0] or b))
        elif k == "CXXForRangeStmt":
            ks = [c for c in inner if c.get("kind")]
            body, loopvar = ks[-1], ks[-2]
            for c in ks[:-2]:
                self.expr(c)
            local = []
            self.declare(loopvar, local)
            self.wrap(body, scopes + [local], depth, self.hook(ln, scopes + [local]))
        elif k == "SwitchStmt":
            ks = kids(n)
            local = []
            for h in ks[:-1]:
                if h["kind"] in ("DeclStmt", "VarDecl"):
                    self.declare(h, local)
                else:
                    self.expr(h)
            if ks and ks[-1]["kind"] == "CompoundStmt":
                self.compound(ks[-1], scopes + [local], depth + 1)
        elif k == "CXXTryStmt":
            for c in kids(n):
                if c["kind"] == "CompoundStmt":
                    self.compound(c, scopes, depth + 1)
                elif c["kind"] == "CXXCatchStmt":
                    local = []
                    for x in kids(c):
                        if x["kind"] == "VarDecl":
                            self.declare(x, local)
                        elif x["kind"] == "CompoundStmt":
                            self.compound(x, scopes + [local], depth + 1)
        elif k == "ReturnStmt":
            ks = kids(n)
            if ks:
                ex = ks[0]
                eb, ee = rng(ex)
                self.expr(ex)
                if eb is None or ee is None:
                    return
                rtype = self.rets[-1] if self.rets else None
                if self.src[eb] == "{":
                    if not rtype:
                        return
                    open_ = "__lt_fr.ret<%s>(%d, " % (rtype, ln)
                else:
                    open_ = "__lt_fr.ret(%d, " % ln
                self.add(eb, (1, depth + 1), open_)
                self.add(ee, (0, -(depth + 1)), ")")
            else:
                e = self.stmt_end(n)
                self.add(b, (1, depth + 1), "{ __lt_fr.retv(%d); " % ln)
                self.add(e, (0, -(depth + 1)), " }")
        else:
            self.expr(n)


# ---------------------------------------------------------------- test case -> C++


def split_types(s):
    out, depth, cur = [], 0, ""
    for c in s:
        if c == "<":
            depth += 1
        elif c == ">":
            depth -= 1
        if c == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += c
    if cur.strip():
        out.append(cur.strip())
    return out


def base_type(t):
    t = re.sub(r"\b(const|volatile)\b", "", t)
    t = t.replace("&", "")
    return re.sub(r"\s+", " ", t).strip()


def cstr(s):
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append("\\%03o" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


INTS = {"int", "long", "long long", "short", "unsigned", "unsigned int", "unsigned long", "unsigned long long",
        "size_t", "int64_t", "int32_t", "uint64_t", "uint32_t", "ll", "LL"}
FLOATS = {"double", "float", "long double"}


def cpp_lit(v, t, tree_vars):
    core = re.sub(r"\b(std|%s)::" % NS, "", base_type(t)).replace(" *", "*").strip()
    m = re.match(r"^(vector|deque|list|set|unordered_set|multiset|array)<(.*)>$", core)
    if m and isinstance(v, list):
        inner = split_types(m.group(2))[0]
        return "{" + ", ".join(cpp_lit(x, inner, tree_vars) for x in v) + "}"
    m = re.match(r"^pair<(.*)>$", core)
    if m and isinstance(v, list) and len(v) == 2:
        a, b = (split_types(m.group(1)) + ["", ""])[:2]
        return "{%s, %s}" % (cpp_lit(v[0], a, tree_vars), cpp_lit(v[1], b, tree_vars))
    if core.endswith("*"):
        node = core[:-1].strip()
        if v is None:
            return "nullptr"
        if node == "TreeNode":
            if isinstance(v, list):
                return "__lt::tree<TreeNode>({%s})" % ", ".join(
                    "std::nullopt" if x is None else "%dLL" % int(x) for x in v)
            if isinstance(v, (int, float)) and tree_vars:
                return "__lt::find<TreeNode>(%s, %dLL)" % (tree_vars[0], int(v))
        if node == "ListNode" and isinstance(v, list):
            return "__lt::linked<ListNode>({%s})" % ", ".join("%dLL" % int(x) for x in v)
    if core == "bool":
        return "true" if v else "false"
    if core in ("char", "unsigned char", "signed char"):
        if isinstance(v, str) and len(v) == 1:
            return "'%s'" % ("\\'" if v == "'" else "\\\\" if v == "\\" else v)
        return str(int(v))
    if core in ("string", "string_view"):
        return cstr(v if isinstance(v, str) else json.dumps(v))
    if core in FLOATS and isinstance(v, (int, float)):
        return repr(float(v))
    if core in INTS and isinstance(v, (int, float, bool)):
        return "%dLL" % int(v) if abs(int(v)) > 2 ** 31 - 1 else str(int(v))
    # unknown type: best effort from the value itself
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return cstr(v)
    if isinstance(v, list):
        return "{" + ", ".join(cpp_lit(x, "", tree_vars) for x in v) + "}"
    if v is None:
        return "nullptr"
    raise UserError("Couldn't turn this test-case value into C++: %s" % json.dumps(v)[:60])


def decl_args(params, values, prefix):
    """C++ declarations for one call's arguments. Returns (lines, names)."""
    lines, names, tree_vars = [], [], []
    for i, ((pname, ptype), v) in enumerate(zip(params, values)):
        var = "%s%d" % (prefix, i)
        t = base_type(ptype)
        lines.append("%s %s = %s;" % (t, var, cpp_lit(v, ptype, tree_vars)))
        if re.sub(r"\s", "", t).endswith("TreeNode*") and isinstance(v, list):
            tree_vars.append(var)
        names.append(var)
    return lines, names


# ---------------------------------------------------------------- compile + run


ERR_RE = re.compile(r"^(.*?):(\d+):(\d+): (?:fatal )?error: (.*)$", re.M)


def first_error(stderr, filename):
    for m in ERR_RE.finditer(stderr):
        if os.path.basename(m.group(1)) == filename:
            return m.group(4), int(m.group(2))
    m = ERR_RE.search(stderr)
    if m:
        return m.group(4), None
    tail = [l for l in stderr.strip().splitlines() if l.strip()][-3:]
    return ("\n".join(tail) or "unknown compiler error"), None


def strip_comments(code):
    code = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
    return re.sub(r"//[^\n]*", "", code)


def run_cpp(req):
    code = req.get("code") or ""
    tc = req.get("testcase") or ""
    want = req.get("method") or None
    if not code.strip():
        return err("Paste your solution code first.")
    cxx = find_cxx()
    if not cxx:
        return err("C++ needs Apple's command line developer tools. Install them with `xcode-select --install`, "
                   "then try again.")

    # #include lines go: the runtime already includes the whole standard library
    lines = ["" if re.match(r"\s*#\s*include\b", l) else l for l in code.split("\n")]
    src = PREFIX + "\n".join(lines) + "\n}\n"
    bare = strip_comments(code)
    defines = []
    if re.search(r"\b(struct|class)\s+TreeNode\s*\{", bare):
        defines.append("-DLT_USER_TREENODE")
    if re.search(r"\b(struct|class)\s+ListNode\s*\{", bare):
        defines.append("-DLT_USER_LISTNODE")

    tmp = tempfile.mkdtemp(prefix="leettrace-")
    try:
        return trace_in(tmp, cxx, src, defines, tc, want)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def trace_in(tmp, cxx, src, defines, tc, want):
    sol = os.path.join(tmp, "solution.cpp")
    with open(sol, "w") as f:
        f.write(src)

    # 1. parse (this is also where the user's compile errors show up)
    p = subprocess.run([cxx, STD, "-fsyntax-only", "-w", "-ferror-limit=5", "-Xclang", "-ast-dump=json",
                        "-Xclang", "-ast-dump-filter=" + NS, "-include", RUNTIME] + defines + [sol],
                       capture_output=True, text=True, errors="replace", timeout=60)
    if p.returncode != 0:
        msg, line = first_error(p.stderr, "solution.cpp")
        return err("Compile error: " + msg, line)
    root = None
    dec, text, i = json.JSONDecoder(), p.stdout, 0
    while i < len(text):
        j = text.find("{", i)
        if j < 0:
            break
        obj, i = dec.raw_decode(text, j)
        if obj.get("kind") == "NamespaceDecl" and obj.get("name") == NS:
            root = obj
            break
    if root is None:
        return err("LeetTrace couldn't read your code (no syntax tree came back from clang).")

    # 2. instrument
    ins = Instrumenter(src)
    for d in kids(root):
        ins.decl(d)
    traced = ins.apply()
    with open(sol, "w") as f:
        f.write(traced)

    # 3. harness
    try:
        harness, info = build_harness(ins, tc, want)
    except UserError as e:
        return err(e.msg, e.line)
    main = os.path.join(tmp, "main.cpp")
    with open(main, "w") as f:
        f.write('#include "solution.cpp"\nusing namespace %s;\n%s' % (NS, harness))

    exe = os.path.join(tmp, "prog")
    p = subprocess.run([cxx] + COMPILE_FLAGS + ["-include", RUNTIME] + defines + [main, "-o", exe],
                       capture_output=True, text=True, errors="replace", timeout=120)
    if p.returncode != 0:
        msg, line = first_error(p.stderr, "main.cpp")
        if any(os.path.basename(m.group(1)) == "main.cpp" for m in ERR_RE.finditer(p.stderr)):
            return err("The test case doesn't fit %s's parameters: %s" % (info["entry"], msg))
        msg, line = first_error(p.stderr, "solution.cpp")
        return err("LeetTrace couldn't build the traced version of your code: " + msg, line)

    # 4. run
    trace_path = os.path.join(tmp, "trace.json")
    out_path = os.path.join(tmp, "stdout.txt")
    with open(out_path, "wb") as out:
        try:
            p = subprocess.run([exe, trace_path], stdout=out, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                               cwd=tmp, timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            return err("Your program ran for more than %d s without finishing." % RUN_TIMEOUT)
    with open(out_path, encoding="utf-8", errors="replace") as f:
        stdout = f.read()
    try:
        with open(trace_path, encoding="utf-8", errors="replace") as f:
            t = json.loads(f.read())
    except (OSError, ValueError):
        tail = p.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        return err("Your program crashed before LeetTrace could save the trace (exit code %s). %s"
                   % (p.returncode, " ".join(tail)))

    if not t["steps"]:
        e = t.get("error") or {}
        return err(e.get("msg") or "Nothing ran — no steps were recorded.", e.get("line"))
    finished = t.get("error") is None and t.get("truncated") is None
    return {
        "ok": True,
        "lang": "cpp",
        "entry": info["entry"],
        "methods": info["methods"],
        "args": info["args"],
        "result": t.get("result") if finished else None,
        "stdout": stdout,
        "error": t.get("error"),
        "truncated": t.get("truncated"),
        "maxSteps": MAX_STEPS,
        "steps": t["steps"],
        "calls": t["calls"],
    }


def pick_entry(methods):
    """The public method no other method calls (the one LeetCode calls)."""
    names = [m["name"] for m in methods]
    referenced = set()
    for m in methods:
        refs = referenced_names(m["node"], set())
        referenced |= {r for r in refs if r in names and r != m["name"]}
    cands = [m for m in methods if m["name"] not in referenced] or methods
    public = [m for m in cands if m.get("access", "public") == "public"]
    return (public or cands)[-1]


def build_harness(ins, tc, want):
    # design problems: ["MinStack","push","top"] / [[],[5],[]]
    lines = [l for l in tc.strip().splitlines() if l.strip()]
    if len(lines) == 2:
        try:
            a, b = parse_value(lines[0]), parse_value(lines[1])
        except UserError:
            a = b = None
        if (isinstance(a, list) and a and all(isinstance(x, str) for x in a) and isinstance(b, list)
                and len(a) == len(b) and a[0] in ins.classes):
            return design_harness(ins, a, b)

    sol = ins.classes.get("Solution")
    if sol and sol["methods"]:
        methods = [m for m in sol["methods"] if not (m["name"] or "").startswith("operator")]
        owner = "__sol."
    else:
        methods = ins.functions
        owner = ""
    if not methods:
        raise UserError("Couldn't find a function to run. Paste a LeetCode `class Solution` or a function.")
    names = []
    for m in methods:
        if m["name"] not in names:
            names.append(m["name"])
    entry = next((m for m in methods if m["name"] == want), None) or pick_entry(methods)
    params = [p for p in entry["params"]]

    positional, named = parse_testcase(tc)
    if named is not None:
        missing = [n for n, _ in params if n not in named]
        if missing and len(named) == len(params):
            values = list(named.values())  # renamed parameters: go by order
        elif missing:
            raise UserError("Test case is missing: %s" % ", ".join(missing))
        else:
            values = [named[n] for n, _ in params]
    else:
        if len(positional) != len(params):
            raise UserError("%s(%s) needs %d input line(s) but the test case has %d. "
                            "Put each argument on its own line." %
                            (entry["name"], ", ".join(n for n, _ in params), len(params), len(positional)))
        values = positional

    decls, args = decl_args(params, values, "__a")
    first = args[0] if args else None
    call = "%s%s(%s)" % (owner, entry["name"], ", ".join(args))
    body = ["int main(int argc, char **argv) {", "    __lt::init(argv[1]);"]
    body += ["    " + d for d in decls]
    if owner:
        body.append("    Solution __sol;")
    body.append("    __lt::start();")
    if first:
        body.append("    std::string __r = __lt::run([&]() { return %s; }, [&]() { return __lt::display(%s); });"
                    % (call, first))
    else:
        body.append("    std::string __r = __lt::run([&]() { return %s; });" % call)
    body += ["    __lt::done(__r);", "}"]
    info = {"entry": entry["name"], "methods": names,
            "args": [[n, display(v)] for (n, _), v in zip(params, values)]}
    return "\n".join(body) + "\n", info


def design_harness(ins, ops, params):
    cls = ins.classes[ops[0]]
    body = ["int main(int argc, char **argv) {", "    __lt::init(argv[1]);",
            "    std::vector<std::string> __outs;", "    %s *__obj = nullptr;" % ops[0], "    __lt::start();"]
    for i, (op, args) in enumerate(zip(ops, params)):
        args = args if isinstance(args, list) else [args]
        if i == 0:
            ctor = next((c for c in cls["ctors"] if len(c["params"]) == len(args)), None)
            ps = ctor["params"] if ctor else [("", "")] * len(args)
            decls, names = decl_args(ps, args, "__c%d_" % i)
            body.append("    { %s __obj = new %s(%s); __outs.push_back(\"null\"); }"
                        % (" ".join(decls), op, ", ".join(names)))
            continue
        m = next((m for m in cls["methods"] if m["name"] == op and len(m["params"]) == len(args)), None)
        if m is None:
            raise UserError("%s has no method %s taking %d argument(s)." % (ops[0], op, len(args)))
        decls, names = decl_args(m["params"], args, "__c%d_" % i)
        body.append("    { %s __outs.push_back(__lt::run([&]() { return __obj->%s(%s); })); }"
                    % (" ".join(decls), op, ", ".join(names)))
    body += ['    std::string __r = "[";',
             '    for (size_t i = 0; i < __outs.size(); ++i) __r += (i ? "," : "") + __outs[i];',
             '    __lt::done(__r + "]");', "}"]
    info = {"entry": ops[0], "methods": [],
            "args": [["operations", display(ops)], ["arguments", display(params)]]}
    return "\n".join(body) + "\n", info
