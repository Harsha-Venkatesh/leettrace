// LeetTrace C++ runtime.
//
// Force-included ahead of the user's (instrumented) solution. It plays the role
// LeetCode's environment does (headers, `using namespace std`, TreeNode, ListNode)
// and records the trace: the instrumenter in tracer_cpp.py inserts calls to
// __lt::Frame at every function entry, statement, loop test and return, and each
// call snapshots the whole call stack plus every object reachable from it.
// The trace is written as JSON in the same shape tracer.py produces.
#pragma once

#include <algorithm>
#include <array>
#include <bitset>
#include <cassert>
#include <cctype>
#include <cfloat>
#include <climits>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <functional>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <list>
#include <map>
#include <memory>
#include <numeric>
#include <optional>
#include <queue>
#include <random>
#include <set>
#include <sstream>
#include <stack>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <typeindex>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include <chrono>
#include <csignal>
#include <cxxabi.h>
#include <exception>
#include <sys/ucontext.h>
#include <unistd.h>

using namespace std;

// Solutions often turn off stdio syncing for speed; keep it on so print output
// lines up with the steps that produced it.
#define sync_with_stdio(x) sync_with_stdio(true)

#ifndef LT_USER_TREENODE
struct TreeNode {
    int val;
    TreeNode *left;
    TreeNode *right;
    TreeNode() : val(0), left(nullptr), right(nullptr) {}
    TreeNode(int x) : val(x), left(nullptr), right(nullptr) {}
    TreeNode(int x, TreeNode *left, TreeNode *right) : val(x), left(left), right(right) {}
};
#endif

#ifndef LT_USER_LISTNODE
struct ListNode {
    int val;
    ListNode *next;
    ListNode() : val(0), next(nullptr) {}
    ListNode(int x) : val(x), next(nullptr) {}
    ListNode(int x, ListNode *next) : val(x), next(next) {}
};
#endif

namespace __lt {

constexpr size_t MAX_STEPS = 2500;
constexpr size_t MAX_HEAP = 400;
constexpr size_t MAX_ITEMS = 60;
constexpr double TIME_LIMIT = 10.0;

struct Snap;
struct Fields;

// ---------------------------------------------------------------- text helpers

inline std::string jstr(const std::string &s) {
    std::string o = "\"";
    for (unsigned char c : s) {
        switch (c) {
            case '"': o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n"; break;
            case '\r': o += "\\r"; break;
            case '\t': o += "\\t"; break;
            default:
                if (c < 0x20) {
                    char b[8];
                    snprintf(b, sizeof b, "\\u%04x", c);
                    o += b;
                } else {
                    o += (char)c;
                }
        }
    }
    return o + "\"";
}

inline std::string quote(std::string_view s, size_t limit = 0) {
    std::string body(s);
    bool cut = limit && body.size() > limit;
    if (cut) body = body.substr(0, limit - 3);
    std::string o = "\"";
    for (char c : body) {
        if (c == '"' || c == '\\') o += '\\', o += c;
        else if (c == '\n') o += "\\n";
        else if (c == '\t') o += "\\t";
        else o += c;
    }
    return o + (cut ? "…\"" : "\"");
}

inline std::string char_lit(char c) {
    switch (c) {
        case '\n': return "'\\n'";
        case '\t': return "'\\t'";
        case '\0': return "'\\0'";
        case '\'': return "'\\''";
        case '\\': return "'\\\\'";
    }
    if ((unsigned char)c < 0x20) return "'\\x" + std::to_string((int)(unsigned char)c) + "'";
    return std::string("'") + c + "'";
}

template <class F>
std::string fmt_float(F d) {
    if (std::isnan(d)) return "nan";
    if (std::isinf(d)) return d > 0 ? "inf" : "-inf";
    char buf[64];
    for (int p = 1; p <= 17; ++p) {  // shortest text that reads back as the same value
        snprintf(buf, sizeof buf, "%.*g", p, (double)d);
        if ((F)strtod(buf, nullptr) == d) break;
    }
    std::string s = buf;
    if (s.find_first_of(".en") == std::string::npos) s += ".0";
    return s;
}

inline std::string clip(const std::string &s, size_t n = 18) {
    return s.size() <= n ? s : s.substr(0, n - 1) + "…";
}

inline std::string type_name(const std::type_info &t) {
    int status = 0;
    char *d = abi::__cxa_demangle(t.name(), nullptr, nullptr, &status);
    std::string s = (status == 0 && d) ? d : t.name();
    free(d);
    for (const char *junk : {"__lt_user::", "std::__1::", "std::"}) {
        for (size_t at; (at = s.find(junk)) != std::string::npos;) s.erase(at, strlen(junk));
    }
    return s;
}

// ---------------------------------------------------------------- type traits

template <class T, class = void> struct is_tree : std::false_type {};
template <class T>
struct is_tree<T, std::void_t<decltype(std::declval<T &>().val), decltype(std::declval<T &>().left),
                              decltype(std::declval<T &>().right)>> : std::true_type {};

template <class T, class = void> struct is_ll : std::false_type {};
template <class T>
struct is_ll<T, std::void_t<decltype(std::declval<T &>().val), decltype(std::declval<T &>().next)>>
    : std::bool_constant<!is_tree<T>::value> {};

template <class T, class = void> struct has_fields : std::false_type {};
template <class T>
struct has_fields<T, std::void_t<decltype(std::declval<const T &>().__lt_fields(std::declval<Fields &>()))>>
    : std::true_type {};

template <class T, class = void> struct streamable : std::false_type {};
template <class T>
struct streamable<T, std::void_t<decltype(std::declval<std::ostream &>() << std::declval<const T &>())>>
    : std::true_type {};

// container kinds: 1 sequence, 2 set, 3 map, 4 adapter, 5 pair, 6 tuple, 7 smart pointer, 8 optional, 9 std::function
template <class T> struct ck { static constexpr int k = 0; static constexpr const char *n = ""; };
#define LT_CK(TPL, K, NAME) \
    template <class... A> struct ck<TPL<A...>> { static constexpr int k = K; static constexpr const char *n = NAME; };
LT_CK(std::vector, 1, "vector")
LT_CK(std::deque, 1, "deque")
LT_CK(std::list, 1, "list")
LT_CK(std::set, 2, "set")
LT_CK(std::multiset, 2, "multiset")
LT_CK(std::unordered_set, 2, "unordered_set")
LT_CK(std::unordered_multiset, 2, "unordered_multiset")
LT_CK(std::map, 3, "map")
LT_CK(std::multimap, 3, "multimap")
LT_CK(std::unordered_map, 3, "unordered_map")
LT_CK(std::unordered_multimap, 3, "unordered_multimap")
LT_CK(std::stack, 4, "stack")
LT_CK(std::queue, 4, "queue")
LT_CK(std::priority_queue, 4, "priority_queue")
LT_CK(std::pair, 5, "pair")
LT_CK(std::tuple, 6, "tuple")
LT_CK(std::shared_ptr, 7, "shared_ptr")
LT_CK(std::unique_ptr, 7, "unique_ptr")
LT_CK(std::optional, 8, "optional")
LT_CK(std::function, 9, "function")
#undef LT_CK
template <class T, size_t N> struct ck<std::array<T, N>> { static constexpr int k = 1; static constexpr const char *n = "array"; };

template <class T> constexpr int kind_of = ck<std::remove_cv_t<T>>::k;

template <class T>
constexpr bool is_charlike = std::is_same_v<T, char> || std::is_same_v<T, signed char> || std::is_same_v<T, unsigned char>;

template <class T>
constexpr bool is_simple = std::is_arithmetic_v<T> || std::is_enum_v<T> || std::is_same_v<T, std::string> ||
                           std::is_same_v<T, std::string_view>;

// the protected container inside stack / queue / priority_queue
template <class A>
const typename A::container_type &underlying(const A &a) {
    struct H : A {
        static const typename A::container_type &get(const A &x) { return x.*(&H::c); }
    };
    return H::get(a);
}

// ---------------------------------------------------------------- state

struct Frame;

struct Call {
    int id, parent;
    std::string name, args;
    std::vector<int> refs;
    size_t start;
    long end = -1;
    std::string ret;
    bool has_ret = false;
};

struct State {
    std::vector<std::string> steps;
    std::vector<Call> calls;
    std::vector<Frame *> stack;
    std::map<std::pair<const void *, std::type_index>, int> ids;
    bool on = false;
    int last_line = 0;
    std::chrono::steady_clock::time_point t0;
    std::string trace_path;
};

inline State &st() {
    static State *s = new State;  // never destroyed: hooks can run during static teardown
    return *s;
}

inline int oid(const void *p, const std::type_info &t) {
    auto &ids = st().ids;
    auto key = std::make_pair(p, std::type_index(t));
    auto it = ids.find(key);
    if (it != ids.end()) return it->second;
    int id = (int)ids.size() + 1;
    ids.emplace(key, id);
    return id;
}

struct Pending {
    int id;
    const void *p;
    void (*desc)(const void *, Snap &, std::string &);
};

struct Snap {
    std::unordered_set<int> seen;
    std::deque<Pending> q;
};

// ---------------------------------------------------------------- display (LeetCode-style output)

template <class T> std::string display(const T &v);

template <class C>
std::string display_seq(const C &c) {
    std::string o = "[";
    bool first = true;
    for (const auto &x : c) {
        if (!first) o += ",";
        first = false;
        o += display(x);
    }
    return o + "]";
}

template <class N>
std::string display_tree(const N *root) {
    std::vector<const N *> out;
    std::deque<const N *> q{root};
    std::unordered_set<const N *> seen;
    while (!q.empty() && out.size() < 2000) {
        const N *n = q.front();
        q.pop_front();
        if (!n || seen.count(n)) {
            out.push_back(nullptr);
            continue;
        }
        seen.insert(n);
        out.push_back(n);
        q.push_back(n->left);
        q.push_back(n->right);
    }
    while (!out.empty() && !out.back()) out.pop_back();
    std::string o = "[";
    for (size_t i = 0; i < out.size(); ++i) o += (i ? "," : "") + (out[i] ? display(out[i]->val) : std::string("null"));
    return o + "]";
}

template <class T>
std::string display(const T &v) {
    using U = std::remove_cv_t<T>;
    if constexpr (std::is_array_v<U>) {
        return display_seq(v);
    } else if constexpr (std::is_same_v<U, bool>) {
        return v ? "true" : "false";
    } else if constexpr (is_charlike<U>) {
        return quote(std::string(1, (char)v));
    } else if constexpr (std::is_integral_v<U>) {
        return std::to_string(v);
    } else if constexpr (std::is_enum_v<U>) {
        return std::to_string((long long)v);
    } else if constexpr (std::is_floating_point_v<U>) {
        char b[64];
        snprintf(b, sizeof b, "%.5f", (double)v);
        return b;
    } else if constexpr (std::is_same_v<U, std::string> || std::is_same_v<U, std::string_view>) {
        return quote(v);
    } else if constexpr (std::is_same_v<U, std::nullptr_t>) {
        return "null";
    } else if constexpr (std::is_same_v<U, const char *> || std::is_same_v<U, char *>) {
        return v ? quote(v) : "null";
    } else if constexpr (std::is_pointer_v<U>) {
        using P = std::remove_cv_t<std::remove_pointer_t<U>>;
        if (!v) return "null";
        if constexpr (is_tree<P>::value) {
            return display_tree<P>(v);
        } else if constexpr (is_ll<P>::value) {
            std::string o = "[";
            std::unordered_set<const P *> seen;
            for (const P *n = v; n && !seen.count(n) && seen.size() < 2000; n = n->next) {
                o += (seen.empty() ? "" : ",") + display(n->val);
                seen.insert(n);
            }
            return o + "]";
        } else if constexpr (!std::is_void_v<P> && !std::is_function_v<P>) {
            return display(*v);
        } else {
            return "<pointer>";
        }
    } else if constexpr (kind_of<U> == 1 || kind_of<U> == 2) {
        return display_seq(v);
    } else if constexpr (kind_of<U> == 3) {
        std::string o = "{";
        bool first = true;
        for (const auto &kv : v) {
            o += (first ? "" : ",") + display(kv.first) + ":" + display(kv.second);
            first = false;
        }
        return o + "}";
    } else if constexpr (kind_of<U> == 4) {
        return display_seq(underlying(v));
    } else if constexpr (kind_of<U> == 5) {
        return "[" + display(v.first) + "," + display(v.second) + "]";
    } else if constexpr (kind_of<U> == 6) {
        std::string o = "[";
        std::apply([&](const auto &...xs) { ((o += (o.size() > 1 ? "," : "") + display(xs)), ...); }, v);
        return o + "]";
    } else if constexpr (kind_of<U> == 7) {
        return display(v.get());
    } else if constexpr (kind_of<U> == 8) {
        return v ? display(*v) : "null";
    } else if constexpr (streamable<U>::value) {
        std::ostringstream ss;
        ss << v;
        return ss.str();
    } else {
        return "<" + type_name(typeid(U)) + ">";
    }
}

/** Compact text for call-tree labels. */
template <class T>
std::string shortv(const T &v) {
    using U = std::remove_cv_t<T>;
    if constexpr (std::is_pointer_v<U>) {
        using P = std::remove_cv_t<std::remove_pointer_t<U>>;
        if constexpr (is_tree<P>::value || is_ll<P>::value) {
            if (v) return "node " + display(v->val);
            return "nullptr";
        }
    }
    if constexpr (std::is_same_v<U, std::nullptr_t>) return "nullptr";
    return clip(display(v));
}

template <class T>
int node_id(const T &v) {
    using U = std::remove_cv_t<T>;
    if constexpr (std::is_pointer_v<U>) {
        using P = std::remove_cv_t<std::remove_pointer_t<U>>;
        if constexpr (is_tree<P>::value || is_ll<P>::value) {
            if (v) return oid(v, typeid(P));
        }
    }
    return 0;
}

// ---------------------------------------------------------------- snapshot encoding (same shapes as tracer.py)

inline void prim(std::string &o, const std::string &t) { o += "[\"p\"," + jstr(t) + "]"; }
inline void fnv(std::string &o, const std::string &t) { o += "[\"f\"," + jstr(t) + "]"; }

template <class T> void ref(const T &v, Snap &S, std::string &o);
template <class T> void describe(const T &v, Snap &S, std::string &o);

template <class T>
struct Desc {
    static void d(const void *p, Snap &S, std::string &o) { describe(*static_cast<const T *>(p), S, o); }
};

template <class T>
void enqueue(const T &obj, Snap &S, std::string &o) {
    int id = oid(&obj, typeid(T));
    if (S.seen.insert(id).second) S.q.push_back({id, &obj, &Desc<T>::d});
    o += "[\"r\"," + std::to_string(id) + "]";
}

template <class T>
constexpr bool is_object_like() {
    using U = std::remove_cv_t<T>;
    return std::is_array_v<U> || is_tree<U>::value || is_ll<U>::value || has_fields<U>::value ||
           (kind_of<U> >= 1 && kind_of<U> <= 4);
}

template <class T>
void ref(const T &v, Snap &S, std::string &o) {
    using U = std::remove_cv_t<T>;
    if constexpr (std::is_array_v<U>) {
        enqueue(v, S, o);
    } else if constexpr (std::is_same_v<U, bool>) {
        prim(o, v ? "true" : "false");
    } else if constexpr (is_charlike<U>) {
        prim(o, char_lit((char)v));
    } else if constexpr (std::is_integral_v<U>) {
        prim(o, std::to_string(v));
    } else if constexpr (std::is_enum_v<U>) {
        prim(o, std::to_string((long long)v));
    } else if constexpr (std::is_floating_point_v<U>) {
        prim(o, fmt_float(v));
    } else if constexpr (std::is_same_v<U, std::string> || std::is_same_v<U, std::string_view>) {
        prim(o, quote(v, 60));
    } else if constexpr (std::is_same_v<U, std::nullptr_t>) {
        prim(o, "nullptr");
    } else if constexpr (std::is_same_v<U, const char *> || std::is_same_v<U, char *>) {
        if (v) prim(o, quote(v, 60));
        else prim(o, "nullptr");
    } else if constexpr (std::is_pointer_v<U>) {
        using P = std::remove_cv_t<std::remove_pointer_t<U>>;
        if (!v) return prim(o, "nullptr");
        if constexpr (std::is_function_v<P>) {
            fnv(o, "function");
        } else if constexpr (!std::is_void_v<P> && is_object_like<P>()) {
            enqueue(*v, S, o);
        } else {
            char b[32];
            snprintf(b, sizeof b, "%p", (const void *)v);
            prim(o, b);
        }
    } else if constexpr (kind_of<U> == 5 || kind_of<U> == 6) {
        bool simple;
        if constexpr (kind_of<U> == 5)
            simple = is_simple<std::decay_t<decltype(v.first)>> && is_simple<std::decay_t<decltype(v.second)>>;
        else
            simple = std::apply([](const auto &...xs) { return (is_simple<std::decay_t<decltype(xs)>> && ...); }, v);
        if (simple) {
            std::string t = "{";
            if constexpr (kind_of<U> == 5) {
                t += display(v.first) + ", " + display(v.second);
            } else {
                std::apply([&](const auto &...xs) { ((t += (t.size() > 1 ? ", " : "") + display(xs)), ...); }, v);
            }
            prim(o, t + "}");
        } else {
            enqueue(v, S, o);
        }
    } else if constexpr (kind_of<U> == 7) {
        ref(v.get(), S, o);
    } else if constexpr (kind_of<U> == 8) {
        if (v) ref(*v, S, o);
        else prim(o, "nullopt");
    } else if constexpr (kind_of<U> == 9) {
        fnv(o, "function");
    } else if constexpr (is_object_like<U>()) {
        enqueue(v, S, o);
    } else if constexpr (streamable<U>::value) {
        std::ostringstream ss;
        ss << v;
        prim(o, clip(ss.str(), 60));
    } else {
        std::string n = type_name(typeid(U));
        if (n.find("lambda") != std::string::npos || n.find("$_") != std::string::npos) fnv(o, "function");
        else prim(o, "{…}");
    }
}

template <class C>
void describe_seq(const C &c, size_t len, const char *kind, const std::string &cls, Snap &S, std::string &o) {
    o += "{\"k\":\"" + std::string(kind) + "\",\"c\":" + jstr(cls) + ",\"i\":[";
    size_t n = 0;
    for (const auto &x : c) {
        if (n == MAX_ITEMS) break;
        if (n++) o += ",";
        ref(x, S, o);
    }
    o += "],\"len\":" + std::to_string(len) + "}";
}

struct Fields {
    Snap &S;
    std::string &o;
    bool first = true;
    template <class T>
    void operator()(const char *name, const T &x) {
        o += first ? "" : ",";
        first = false;
        o += "[" + jstr(name) + ",";
        ref(x, S, o);
        o += "]";
    }
};

template <class T>
void describe(const T &v, Snap &S, std::string &o) {
    using U = std::remove_cv_t<T>;
    if constexpr (std::is_array_v<U>) {
        describe_seq(v, std::extent_v<U>, "list", "array", S, o);
    } else if constexpr (is_tree<U>::value) {
        o += "{\"k\":\"tree\",\"c\":" + jstr(type_name(typeid(U))) + ",\"val\":";
        ref(v.val, S, o);
        o += ",\"l\":";
        ref(v.left, S, o);
        o += ",\"r\":";
        ref(v.right, S, o);
        o += "}";
    } else if constexpr (is_ll<U>::value) {
        o += "{\"k\":\"ll\",\"c\":" + jstr(type_name(typeid(U))) + ",\"val\":";
        ref(v.val, S, o);
        o += ",\"n\":";
        ref(v.next, S, o);
        o += "}";
    } else if constexpr (kind_of<U> == 1) {
        describe_seq(v, v.size(), "list", ck<U>::n, S, o);
    } else if constexpr (kind_of<U> == 2) {
        describe_seq(v, v.size(), "set", ck<U>::n, S, o);
    } else if constexpr (kind_of<U> == 3) {
        o += "{\"k\":\"dict\",\"c\":" + jstr(ck<U>::n) + ",\"i\":[";
        size_t n = 0;
        for (const auto &kv : v) {
            if (n == MAX_ITEMS) break;
            o += n++ ? ",[" : "[";
            ref(kv.first, S, o);
            o += ",";
            ref(kv.second, S, o);
            o += "]";
        }
        o += "],\"len\":" + std::to_string(v.size()) + "}";
    } else if constexpr (kind_of<U> == 4) {
        describe_seq(underlying(v), v.size(), "list", ck<U>::n, S, o);
    } else if constexpr (kind_of<U> == 5) {
        o += "{\"k\":\"list\",\"c\":\"pair\",\"i\":[";
        ref(v.first, S, o);
        o += ",";
        ref(v.second, S, o);
        o += "],\"len\":2}";
    } else if constexpr (kind_of<U> == 6) {
        o += "{\"k\":\"list\",\"c\":\"tuple\",\"i\":[";
        bool first = true;
        std::apply([&](const auto &...xs) { ((o += first ? "" : ",", first = false, ref(xs, S, o)), ...); }, v);
        o += "],\"len\":" + std::to_string(std::tuple_size_v<U>) + "}";
    } else if constexpr (has_fields<U>::value) {
        o += "{\"k\":\"obj\",\"c\":" + jstr(type_name(typeid(U))) + ",\"a\":[";
        Fields f{S, o};
        v.__lt_fields(f);
        o += "]}";
    } else {
        o += "{\"k\":\"raw\",\"c\":" + jstr(type_name(typeid(U))) + ",\"v\":\"…\"}";
    }
}

// ---------------------------------------------------------------- variables

struct Var {
    const char *n;
    const void *p;
    void (*ref)(const void *, Snap &, std::string &);
    std::string (*sh)(const void *);
    int (*node)(const void *);
};

template <class T>
struct Th {
    static void ref(const void *p, Snap &S, std::string &o) { __lt::ref(*static_cast<const T *>(p), S, o); }
    static std::string sh(const void *p) { return shortv(*static_cast<const T *>(p)); }
    static int node(const void *p) { return node_id(*static_cast<const T *>(p)); }
};

/** `this`: p is the pointer value itself, not the address of a variable. */
template <class T>
struct ThisTh {
    static void ref(const void *p, Snap &S, std::string &o) { __lt::ref(static_cast<const T *>(p), S, o); }
    static std::string sh(const void *) { return "this"; }
    static int node(const void *) { return 0; }
};

template <class T>
Var V(const char *n, const T &x) {
    return {n, &x, &Th<T>::ref, &Th<T>::sh, &Th<T>::node};
}

template <class T>
Var VThis(const T *self) {
    return {"this", self, &ThisTh<T>::ref, &ThisTh<T>::sh, &ThisTh<T>::node};
}

/** A local that holds a lambda / std::function: shown by name, never inspected. */
inline Var F(const char *n) { return {n, nullptr, nullptr, nullptr, nullptr}; }

// ---------------------------------------------------------------- recording

[[noreturn]] void finish(const char *truncated, const std::string &error_json, const std::string *result);

inline long stdout_pos() {
    std::cout.flush();
    fflush(stdout);
    off_t p = lseek(1, 0, SEEK_CUR);
    return p < 0 ? 0 : (long)p;
}

void record(const char *ev, const Var *ret);

struct Frame {
    bool active = false;
    bool returned = false;
    int fid = -1;
    const char *name;
    int ln;
    std::vector<Var> vars;

    Frame(const char *n, int decl_line, std::initializer_list<Var> args) : name(n), ln(decl_line) {
        State &s = st();
        if (!s.on) return;
        active = true;
        vars.assign(args);
        fid = (int)s.calls.size();
        Call c{fid, s.stack.empty() ? -1 : s.stack.back()->fid, n, "", {}, s.steps.size()};
        std::string a = "[";
        for (const Var &v : vars) {
            if (!v.p || std::string_view(v.n) == "this") continue;
            int node = v.node(v.p);
            a += (a.size() > 1 ? ",[" : "[") + jstr(v.n) + "," + jstr(v.sh(v.p)) + "," + (node ? "true" : "false") + "]";
            if (node) c.refs.push_back(node);
        }
        c.args = a + "]";
        s.calls.push_back(std::move(c));
        s.stack.push_back(this);
        record("call", nullptr);
    }

    ~Frame() {
        if (!active) return;
        State &s = st();
        if (!returned) {  // left by an exception
            Call &c = s.calls[fid];
            c.end = (long)s.steps.size() - 1;
            c.ret = "exception";
            c.has_ret = true;
        }
        if (!s.stack.empty() && s.stack.back() == this) s.stack.pop_back();
    }

    void line(int l, std::initializer_list<Var> vs) {
        if (!active) return;
        vars.assign(vs);
        ln = l;
        record("line", nullptr);
    }

    template <class T>
    T &&ret(int l, T &&v) {
        if (active && !returned) {
            ln = l;
            Var rv = V("", v);
            record("return", &rv);
            Call &c = st().calls[fid];
            c.end = (long)st().steps.size() - 1;
            c.ret = shortv(v);
            c.has_ret = true;
            returned = true;
        }
        return std::forward<T>(v);
    }

    void retv(int l) {
        if (!active || returned) return;
        ln = l;
        record("return", nullptr);
        st().calls[fid].end = (long)st().steps.size() - 1;
        returned = true;
    }

    void end(int l) { retv(l); }
};

inline void record(const char *ev, const Var *ret) {
    State &s = st();
    if (s.stack.empty()) return;
    if (s.steps.size() >= MAX_STEPS) finish("steps", "null", nullptr);
    if (std::chrono::duration<double>(std::chrono::steady_clock::now() - s.t0).count() > TIME_LIMIT)
        finish("time", "null", nullptr);
    Frame *top = s.stack.back();
    s.last_line = top->ln;

    Snap S;
    std::string o = "{\"e\":\"" + std::string(ev) + "\",\"ln\":" + std::to_string(top->ln) + ",\"s\":[";
    for (size_t i = 0; i < s.stack.size(); ++i) {
        Frame *f = s.stack[i];
        o += (i ? ",{" : "{");
        o += "\"id\":" + std::to_string(f->fid) + ",\"n\":" + jstr(f->name) + ",\"ln\":" + std::to_string(f->ln) + ",\"v\":[";
        bool first = true;
        for (const Var &v : f->vars) {
            o += first ? "[" : ",[";
            first = false;
            o += jstr(v.n) + ",";
            if (v.p) v.ref(v.p, S, o);
            else fnv(o, std::string("function ") + v.n);
            o += "]";
        }
        o += "]";
        if (f == top && ret) {
            o += ",\"ret\":";
            ret->ref(ret->p, S, o);
        }
        o += "}";
    }
    o += "],\"h\":{";
    size_t n = 0;
    while (!S.q.empty()) {
        Pending p = S.q.front();
        S.q.pop_front();
        o += (n ? ",\"" : "\"") + std::to_string(p.id) + "\":";
        if (++n > MAX_HEAP) o += "{\"k\":\"more\"}";
        else p.desc(p.p, S, o);
    }
    o += "},\"o\":" + std::to_string(stdout_pos()) + "}";
    s.steps.push_back(std::move(o));
}

inline void finish(const char *truncated, const std::string &error_json, const std::string *result) {
    State &s = st();
    s.on = false;
    std::cout.flush();
    fflush(stdout);
    FILE *f = fopen(s.trace_path.c_str(), "w");
    if (f) {
        fputs("{\"steps\":[", f);
        for (size_t i = 0; i < s.steps.size(); ++i) {
            if (i) fputc(',', f);
            fputs(s.steps[i].c_str(), f);
        }
        fputs("],\"calls\":[", f);
        for (size_t i = 0; i < s.calls.size(); ++i) {
            const Call &c = s.calls[i];
            std::string j = "{\"id\":" + std::to_string(c.id) + ",\"p\":" + (c.parent < 0 ? "null" : std::to_string(c.parent)) +
                            ",\"n\":" + jstr(c.name) + ",\"a\":" + c.args + ",\"r\":[";
            for (size_t k = 0; k < c.refs.size(); ++k) j += (k ? "," : "") + std::to_string(c.refs[k]);
            j += "],\"s\":" + std::to_string(c.start) + ",\"e\":" + (c.end < 0 ? "null" : std::to_string(c.end)) +
                 ",\"ret\":" + (c.has_ret ? jstr(c.ret) : "null") + "}";
            if (i) fputc(',', f);
            fputs(j.c_str(), f);
        }
        fprintf(f, "],\"truncated\":%s,\"error\":%s,\"result\":%s}", truncated ? jstr(truncated).c_str() : "null",
                error_json.c_str(), result ? jstr(*result).c_str() : "null");
        fclose(f);
    }
    _exit(0);
}

inline std::string error_json(const std::string &msg) {
    State &s = st();
    return "{\"msg\":" + jstr(msg) + ",\"line\":" + (s.last_line ? std::to_string(s.last_line) : "null") + "}";
}

/** Which check fired: on arm64 the trap is `brk #imm`; UBSan uses 0x55NN (NN = check kind), libc++ uses 1. */
inline const char *trap_reason(void *ucontext) {
#if defined(__aarch64__)
    auto *uc = static_cast<ucontext_t *>(ucontext);
    uint32_t ins = *reinterpret_cast<const uint32_t *>(uc->uc_mcontext->__ss.__pc);
    if ((ins & 0xFFE0001F) != 0xD4200000) return nullptr;
    unsigned imm = (ins >> 5) & 0xFFFF;
    if (imm == 1) return "Runtime error: index out of range (or another standard-library check failed)";
    if ((imm >> 8) == 0x55) {
        switch (imm & 0xFF) {
            case 0x00: case 0x0c: case 0x0d: case 0x15: return "Runtime error: signed integer overflow";
            case 0x03: return "Runtime error: division by zero";
            case 0x0b: return "Runtime error: reached the end of a function without returning a value";
            case 0x14: return "Runtime error: shift out of range";
            case 0x16: return "Runtime error: member access through a null pointer";
        }
    }
#else
    (void)ucontext;
#endif
    return nullptr;
}

extern "C" inline void on_signal(int sig, siginfo_t *, void *ucontext) {
    signal(sig, SIG_DFL);
    const char *m = "Runtime error";
    switch (sig) {
        case SIGSEGV:
        case SIGBUS: m = "Runtime error: invalid memory access (null or dangling pointer, or recursion too deep)"; break;
        case SIGFPE: m = "Runtime error: arithmetic error (division by zero?)"; break;
        case SIGTRAP:
        case SIGILL: {
            const char *why = trap_reason(ucontext);
            m = why ? why : "Runtime error: a safety check failed (index out of range, null pointer or integer overflow)";
            break;
        }
        case SIGABRT: m = "Runtime error: aborted (failed assert?)"; break;
    }
    finish(nullptr, error_json(m), nullptr);
}

inline void on_terminate() {
    std::string m = "Uncaught exception";
    if (auto e = std::current_exception()) {
        try {
            std::rethrow_exception(e);
        } catch (const std::exception &x) {
            m = "Uncaught exception: " + type_name(typeid(x)) + ": " + x.what();
        } catch (...) {
        }
    }
    finish(nullptr, error_json(m), nullptr);
}

inline void init(const char *trace_path) {
    State &s = st();
    s.trace_path = trace_path;
    setvbuf(stdout, nullptr, _IONBF, 0);
    static char altstack[1 << 16];
    stack_t ss{};
    ss.ss_sp = altstack;
    ss.ss_size = sizeof altstack;
    sigaltstack(&ss, nullptr);
    struct sigaction sa{};
    sa.sa_sigaction = on_signal;
    sa.sa_flags = SA_ONSTACK | SA_SIGINFO;
    for (int sig : {SIGSEGV, SIGBUS, SIGFPE, SIGILL, SIGTRAP, SIGABRT}) sigaction(sig, &sa, nullptr);
    std::set_terminate(on_terminate);
}

inline void start() {
    st().t0 = std::chrono::steady_clock::now();
    st().on = true;
}

inline void done(const std::string &result) { finish(nullptr, "null", &result); }

/** Runs the entry call; a void method shows its first argument afterwards (in-place problems). */
template <class Call, class Fallback>
std::string run(Call call, Fallback fallback) {
    if constexpr (std::is_void_v<decltype(call())>) {
        call();
        return fallback();
    } else {
        return display(call());
    }
}

template <class Call>
std::string run(Call call) {
    if constexpr (std::is_void_v<decltype(call())>) {
        call();
        return "null";
    } else {
        return display(call());
    }
}

// ---------------------------------------------------------------- test-case builders

template <class N>
N *tree(std::vector<std::optional<long long>> vals) {
    if (vals.empty() || !vals[0]) return nullptr;
    N *root = new N((int)*vals[0]);
    std::deque<N *> q{root};
    size_t i = 1;
    while (!q.empty() && i < vals.size()) {
        N *n = q.front();
        q.pop_front();
        if (i < vals.size() && vals[i]) q.push_back(n->left = new N((int)*vals[i]));
        ++i;
        if (i < vals.size() && vals[i]) q.push_back(n->right = new N((int)*vals[i]));
        ++i;
    }
    return root;
}

template <class N>
N *linked(std::vector<long long> vals) {
    N *head = nullptr;
    for (auto it = vals.rbegin(); it != vals.rend(); ++it) {
        N *n = new N((int)*it);
        n->next = head;
        head = n;
    }
    return head;
}

template <class N>
N *find(N *root, long long val) {
    std::vector<N *> stk{root};
    while (!stk.empty()) {
        N *n = stk.back();
        stk.pop_back();
        if (!n) continue;
        if (n->val == val) return n;
        stk.push_back(n->left);
        stk.push_back(n->right);
    }
    return nullptr;
}

}  // namespace __lt
