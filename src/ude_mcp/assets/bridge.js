// ude-mcp bridge agent — runs INSIDE UDE as a startup macro (JScript).
// Protocol:
//   POST /next  (body: agent status JSON)   -> returns {"cmd": <name>, "args": {...}} or {"cmd":"idle"}
//   POST /result (body: {"id":N,"ok":bool,"value":...,"error":...}) -> "ok"
// Placeholders __PORT__ __WSX__ __CFG__ __MODE__ are substituted by the MCP server.

var PORT = "__PORT__";
var WSX  = "__WSX__";
var CFG  = "__CFG__";
var MODE = "__MODE__"; // "create" (CreateWorkspaceExt with CFG) or "load" (LoadWorkspace WSX)

var out = "";
function log(s) { out += s + "\n"; }
function busyWait(ms) { var t = new Date().getTime(); while (new Date().getTime() - t < ms) { } }

function jesc(s) {
    s = String(s);
    var r = s.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n").replace(/\r/g, "");
    return r;
}
function jstr(v) {
    if (typeof v === "undefined" || v === null) return "null";
    var t = typeof v;
    if (t === "string") return '"' + jesc(v) + '"';
    if (t === "number") return isFinite(v) ? String(v) : "null";
    if (t === "boolean") return v ? "true" : "false";
    return '"' + jesc(v) + '"';
}

function httpPost(path, body, timeoutMs) {
    var http = new ActiveXObject("WinHttp.WinHttpRequest.5.1");
    http.SetTimeouts(5000, 5000, timeoutMs || 35000, 5000);
    http.Open("POST", "http://127.0.0.1:" + PORT + path, false);
    http.SetRequestHeader("Content-Type", "application/json");
    http.Send(body);
    if (http.Status !== 200) throw new Error("HTTP " + http.Status);
    return http.ResponseText;
}

// ---- object model handles ----
var app = null, wsp = null;
var cores = [];   // [{name, dbg}]

function coreList() {
    var arr = [];
    try {
        var n = wsp.CoreDebuggerCnt;
        for (var i = 0; i < n; i++) {
            arr.push(wsp.CoreDebugger(i));
        }
    } catch (e) { }
    return arr;
}

function coreStatus() {
    var parts = [];
    cores = coreList();
    for (var i = 0; i < cores.length; i++) {
        var d = cores[i];
        var s = "{";
        s += '"index":' + i;
        try { s += ',"name":' + jstr(d.Name); } catch (e) { s += ',"name":"?"'; }
        try { s += ',"connected":' + (d.Connected ? "true" : "false"); } catch (e) { s += ',"connected":null'; }
        try { s += ',"running":' + (d.Running ? "true" : "false"); } catch (e) { s += ',"running":null'; }
        try { s += ',"state":' + jstr(d.State); } catch (e) { s += ',"state":null'; }
        try { s += ',"location":' + jstr(d.Location); } catch (e) { s += ',"location":null'; }
        try { s += ',"bpHit":' + (d.Breakpoints.BreakpointHit ? "true" : "false"); } catch (e) { s += ',"bpHit":null'; }
        s += "}";
        parts.push(s);
    }
    return parts;
}

function statusJson() {
    var s = '{"agent":"ude-mcp-bridge"';
    s += ',"ude":' + jstr(app ? app.VersionInfo : null);
    s += ',"workspace":' + jstr(wsp ? wsp.ProjectTitle : null);
    s += ',"cores":[' + coreStatus().join(",") + ']';
    s += ',"log":' + jstr(out.length > 4000 ? out.substring(out.length - 4000) : out);
    s += "}";
    return s;
}

function withCore(args, fn) {
    var idx = 0;
    if (args && typeof args.core === "number") idx = args.core;
    cores = coreList();
    if (idx >= cores.length) throw new Error("no such core: " + idx + " (have " + cores.length + ")");
    return fn(cores[idx]);
}

// ---- command implementations; each returns a JSON fragment (value part) ----
var handlers = {
    "status": function (args) {
        return null; // value = full status snapshot, handled by caller
    },
    "connect": function (args) {
        var t = (args && args.timeout) || 15000;
        var r = wsp.ConnectTarget(t, 0);
        var ok = wsp.WaitForTargetConnected(t);
        return jstr(ok);
    },
    "disconnect": function (args) {
        var t = (args && args.timeout) || 10000;
        wsp.DisconnectTarget(t, 0);
        return "true";
    },
    "load_program": function (args) {
        return withCore(args, function (d) {
            return jstr(d.LoadProgramFile(args.path));
        });
    },
    "flash": function (args) {
        return withCore(args, function (d) {
            if (args.options) return jstr(d.LoadAndFlash(args.path, args.options));
            return jstr(d.LoadAndFlash(args.path));
        });
    },
    "go": function (args) { return withCore(args, function (d) { d.Go(); return "true"; }); },
    "halt": function (args) { return withCore(args, function (d) { d.Break(); return "true"; }); },
    "step_in": function (args) { return withCore(args, function (d) { d.StepIn(); return "true"; }); },
    "step_over": function (args) { return withCore(args, function (d) { d.StepOver(); return "true"; }); },
    "step_out": function (args) { return withCore(args, function (d) { d.StepOut(); return "true"; }); },
    "reset": function (args) { return withCore(args, function (d) { d.ResetTarget(); return "true"; }); },
    "wait_halt": function (args) {
        return withCore(args, function (d) {
            return jstr(d.WaitForHalt((args && args.timeout) || 5000));
        });
    },
    "bp_add": function (args) {
        return withCore(args, function (d) {
            return jstr(d.Breakpoints.AddAndGetResult(args.desc));
        });
    },
    "bp_remove": function (args) {
        return withCore(args, function (d) {
            return jstr(d.Breakpoints.RemoveAndGetResult(args.desc));
        });
    },
    "bp_clear": function (args) {
        return withCore(args, function (d) { d.Breakpoints.RemoveAll(); return "true"; });
    },
    "bp_list": function (args) {
        return withCore(args, function (d) {
            var bps = d.Breakpoints;
            var n = bps.Count;
            var arr = [];
            for (var i = 0; i < n; i++) {
                var b = bps.Item(i);
                var s = "{";
                try { s += '"desc":' + jstr(b.Description); } catch (e) { s += '"desc":"?"'; }
                try { s += ',"enabled":' + (b.Enabled ? "true" : "false"); } catch (e) { }
                s += "}";
                arr.push(s);
            }
            return "[" + arr.join(",") + "]";
        });
    },
    "var_read": function (args) {
        return withCore(args, function (d) { return jstr(d.ReadVariable(args.expr)); });
    },
    "var_write": function (args) {
        return withCore(args, function (d) { return jstr(d.WriteVariable(args.expr, args.value)); });
    },
    "reg_read": function (args) {
        return withCore(args, function (d) { return jstr(d.ReadRegister(args.name)); });
    },
    "reg_write": function (args) {
        return withCore(args, function (d) { return jstr(d.WriteRegister(args.name, args.value)); });
    },
    "mem_read": function (args) {
        return withCore(args, function (d) {
            var a = args.addr, w = args.width || 32;
            if (w === 8) return jstr(d.ReadMemory8(a));
            if (w === 16) return jstr(d.ReadMemory16(a));
            return jstr(d.ReadMemory32(a));
        });
    },
    "mem_write": function (args) {
        return withCore(args, function (d) {
            var a = args.addr, v = args.value, w = args.width || 32;
            if (w === 8) return jstr(d.WriteMemory8(a, v));
            if (w === 16) return jstr(d.WriteMemory16(a, v));
            return jstr(d.WriteMemory32(a, v));
        });
    },
    "callstack": function (args) {
        return withCore(args, function (d) {
            var cs = d.CallStack;
            var parts = [];
            try {
                var n = cs.Count;
                for (var i = 0; i < n; i++) {
                    var fr = cs.Item(i);
                    var s = "{";
                    try { s += '"frame":' + jstr(String(fr)); } catch (e) { s += '"frame":"?"'; }
                    s += "}";
                    parts.push(s);
                }
            } catch (e) {
                return '{"raw":' + jstr(String(cs)) + ',"error":' + jstr(e.message) + '}';
            }
            if (parts.length === 0) return '{"raw":' + jstr(String(cs)) + "}";
            return "[" + parts.join(",") + "]";
        });
    },
    "eval": function (args) {
        return withCore(args, function (d) {
            var ex = d.Expression(args.expr);
            return jstr(ex.Value);
        });
    },
    "shutdown": function (args) {
        return "__SHUTDOWN__";
    }
};

function dispatch(cmd, args) {
    var h = handlers[cmd];
    if (!h) throw new Error("unknown command: " + cmd);
    if (cmd === "status") return statusJson();
    return h(args);
}

function runCommand(id, cmdName, args) {
    var resp;
    try {
        if (cmdName === "status") {
            resp = '{"id":' + id + ',"ok":true,"value":' + statusJson() + '}';
        } else {
            var v = dispatch(cmdName, args);
            if (v === "__SHUTDOWN__") {
                resp = '{"id":' + id + ',"ok":true,"value":"shutting down"}';
                httpPost("/result", resp, 10000);
                return "stop";
            }
            resp = '{"id":' + id + ',"ok":true,"value":' + v + '}';
        }
    } catch (e) {
        resp = '{"id":' + id + ',"ok":false,"error":' + jstr(e.message) + '}';
    }
    try {
        httpPost("/result", resp, 10000);
    } catch (e2) { log("result POST FAIL: " + e2.message); }
    return "continue";
}

// ================= main =================
(function main() {
    // 1. bind to the live application object model
    try {
        app = new ActiveXObject("UDESTK.ApplicationRoot.2021");
    } catch (e) {
        log("FATAL: cannot create ApplicationRoot: " + e.message);
        return;
    }
    log("bound to UDE " + app.VersionInfo);

    // 2. open workspace (create with cfg, or load existing wsx)
    try {
        if (MODE === "create") {
            if (!app.CreateWorkspaceExt(WSX, CFG, 0)) log("CreateWorkspaceExt returned false");
        } else {
            if (!app.LoadWorkspace(WSX)) log("LoadWorkspace returned false");
        }
        wsp = app.Workspace;
        log("workspace: " + wsp.ProjectTitle);
    } catch (e) {
        log("workspace open FAIL: " + e.message);
    }

    // 3. wait for cores to appear
    var waited = 0;
    while (wsp && wsp.CoreDebuggerCnt === 0 && waited < 30000) { busyWait(500); waited += 500; }
    log("cores: " + (wsp ? wsp.CoreDebuggerCnt : "n/a"));

    // 4. long-poll loop
    var seq = 0;
    while (true) {
        try {
            var req = '{"seq":' + seq + ',"status":' + statusJson() + '}';
            var respText = httpPost("/next", req, 35000);
            seq++;
            var cmd = eval("(" + respText + ")");
            if (!cmd || !cmd.cmd) { continue; }
            if (cmd.cmd === "idle") { continue; }
            var r = runCommand(cmd.id, cmd.cmd, cmd.args);
            if (r === "stop") break;
        } catch (e) {
            // MCP server not reachable — back off and retry
            log("loop err: " + e.message);
            busyWait(2000);
        }
    }
    log("bridge exiting");
})();
