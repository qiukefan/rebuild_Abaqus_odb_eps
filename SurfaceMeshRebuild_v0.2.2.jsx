#target illustrator
/* ExtendScript ES3 front end. Geometry is rebuilt by the packaged Python worker. */
var SurfaceMeshRebuildV020 = (function () {
    var VERSION = "0.2.2", TITLE = "表面网格重建 v0.2.2", SETTINGS = "/SurfaceMeshRebuild/v0.2.2";
    function fail(message) { throw new Error(message); }
    function trim(value) { return String(value || "").replace(/^\s+|\s+$/g, ""); }
    function own(object, key) { return Object.prototype.hasOwnProperty.call(object, key); }
    // Strict JSON parser for older ExtendScript engines. Never evaluates executable text.
    function parseJSON(input) {
        var text = String(input).replace(/^\uFEFF/, ""), at = 0;
        function error() { fail("JSON 格式无效（位置 " + at + "）。"); }
        function space() { while (at < text.length && /[\x20\t\r\n]/.test(text.charAt(at))) { at++; } }
        function string() {
            if (text.charAt(at++) !== '"') { error(); }
            var result = "", c, code, hex, escapes = {'"':'"', "\\":"\\", "/":"/", b:"\b", f:"\f", n:"\n", r:"\r", t:"\t"};
            while (at < text.length) {
                c = text.charAt(at++);
                if (c === '"') { return result; }
                if (c === "\\") {
                    c = text.charAt(at++);
                    if (c === "u") {
                        hex = text.substr(at, 4); if (!/^[0-9a-fA-F]{4}$/.test(hex)) { error(); }
                        result += String.fromCharCode(parseInt(hex, 16)); at += 4;
                    } else if (own(escapes, c)) { result += escapes[c]; } else { error(); }
                } else { code = c.charCodeAt(0); if (code < 32) { error(); } result += c; }
            }
            error();
        }
        function value(depth) {
            if (depth > 60) { fail("JSON 嵌套过深。"); }
            space(); var c = text.charAt(at), result, key, match;
            if (c === '"') { return string(); }
            if (c === "{") {
                result = {}; at++; space(); if (text.charAt(at) === "}") { at++; return result; }
                while (true) {
                    space(); if (text.charAt(at) !== '"') { error(); } key = string();
                    if (key === "__proto__" || key === "constructor" || key === "prototype") { fail("JSON 包含不允许的属性。"); }
                    space(); if (text.charAt(at++) !== ":") { error(); } result[key] = value(depth + 1);
                    space(); c = text.charAt(at++); if (c === "}") { return result; } if (c !== ",") { error(); }
                }
            }
            if (c === "[") {
                result = []; at++; space(); if (text.charAt(at) === "]") { at++; return result; }
                while (true) { result.push(value(depth + 1)); space(); c = text.charAt(at++); if (c === "]") { return result; } if (c !== ",") { error(); } }
            }
            if (text.substr(at, 4) === "true") { at += 4; return true; }
            if (text.substr(at, 5) === "false") { at += 5; return false; }
            if (text.substr(at, 4) === "null") { at += 4; return null; }
            match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.substr(at));
            if (!match) { error(); } at += match[0].length; result = Number(match[0]); if (!isFinite(result)) { error(); } return result;
        }
        var result = value(0); space(); if (at !== text.length) { error(); } return result;
    }
    function stringifyJSON(input) {
        function quote(s) {
            return '"' + String(s).replace(/["\\\x00-\x1f]/g, function(c) {
                if (c === '"') { return '\\"'; } if (c === "\\") { return "\\\\"; }
                var h = c.charCodeAt(0).toString(16); return "\\u" + ("0000" + h).slice(-4);
            }) + '"';
        }
        function encode(v, depth) {
            if (depth > 60) { fail("对象嵌套过深。"); }
            if (v === null) { return "null"; }
            if (typeof v === "string") { return quote(v); }
            if (typeof v === "boolean") { return v ? "true" : "false"; }
            if (typeof v === "number") { if (!isFinite(v)) { fail("不能写入非有限数字。"); } return String(v); }
            var items = [], i;
            if (v instanceof Array) { for (i = 0; i < v.length; i++) { items.push(encode(v[i], depth + 1)); } return "[" + items.join(",") + "]"; }
            if (typeof v === "object") { for (i in v) { if (own(v, i) && v[i] !== undefined) { items.push(quote(i) + ":" + encode(v[i], depth + 1)); } } return "{" + items.join(",") + "}"; }
            fail("不支持的 JSON 值。");
        }
        return encode(input, 0);
    }
    function ensureFolder(path) {
        var folder = Folder(path); if (folder.exists) { return folder; }
        var parent = folder.parent;
        if (!parent || parent.fsName === folder.fsName) { fail("无法创建目录：" + folder.fsName); }
        if (!parent.exists) { ensureFolder(parent.fsName); }
        if (!folder.create()) { fail("无法创建目录：" + folder.fsName); } return folder;
    }
    function readText(file, maxBytes) {
        file = File(file); if (file.length > maxBytes) { fail("文件超过读取大小限制：" + file.fsName); }
        file.encoding = "UTF-8"; if (!file.open("r")) { fail("无法读取：" + file.fsName); }
        try { return file.read(); } finally { file.close(); }
    }
    function writeText(file, text) {
        file = File(file); ensureFolder(file.parent.fsName); file.encoding = "UTF-8";
        if (!file.open("w")) { fail("无法写入：" + file.fsName); }
        try { if (!file.write(text)) { fail("写入失败：" + file.fsName); } } finally { file.close(); }
    }
    // Windows Script Host accepts UTF-16LE with BOM; plain UTF-8 corrupts non-ASCII paths.
    function writeStarter(file, text) {
        file = File(file); var bytes = String.fromCharCode(255, 254), i, code;
        for (i = 0; i < text.length; i++) { code = text.charCodeAt(i); bytes += String.fromCharCode(code & 255, code >>> 8); }
        file.encoding = "BINARY"; if (!file.open("w")) { fail("无法写入启动脚本：" + file.fsName); }
        try { if (!file.write(bytes)) { fail("写入启动脚本失败：" + file.fsName); } } finally { file.close(); }
    }
    function settingsFile() { return File(Folder.userData.fsName + SETTINGS + "/settings.json"); }
    function loadSettings() {
        var file = settingsFile(); if (!file.exists) { return {}; }
        try { var result = parseJSON(readText(file, 131072)); return result && typeof result === "object" && !(result instanceof Array) ? result : {}; }
        catch (ignore) { return {}; }
    }
    function saveSettings(settings) { writeText(settingsFile(), stringifyJSON(settings)); }
    function validateDocument(host) {
        if (!host.documents.length) { fail("请先打开由 Abaqus 导出的原始 EPS 文档。"); }
        var doc = host.activeDocument, file;
        try { file = File(doc.fullName); } catch (e) { fail("当前文档没有原始 EPS 路径。请打开由 Abaqus 导出的原始 EPS。"); }
        if (!/\.eps$/i.test(file.fsName)) { fail("当前文档不是 EPS。请打开由 Abaqus 导出的原始 EPS 后运行重建。"); }
        if (doc.saved !== true) { fail("当前文档有未保存修改。请先另存保留编辑，再重新打开原始 Abaqus EPS；本版按原始 EPS 重建。请勿用 Illustrator 保存覆盖原始 EPS。"); }
        if (!file.exists) { fail("当前 EPS 的磁盘文件不存在：" + file.fsName); }
        return {document: doc, file: file};
    }
    function packageAt(path) {
        if (!trim(path)) { return null; } var folder = Folder(path);
        if (File(folder.fsName + "/launch_job.vbs").exists) { return folder; }
        folder = Folder(folder.fsName + "/v0.2.2");
        return File(folder.fsName + "/launch_job.vbs").exists ? folder : null;
    }
    function resolvePackage(scriptFile, settings, chooser) {
        var folder = packageAt(File(scriptFile).parent.fsName + "/v0.2.2") || packageAt(settings.packageDir);
        if (!folder) {
            var picked = chooser("请选择程序的 v0.2.2 文件夹（其中应有 launch_job.vbs）。");
            if (!picked) { return null; } folder = packageAt(picked.fsName);
            if (!folder) { fail("所选目录没有 launch_job.vbs，请选择完整解压后的 v0.2.2 文件夹。"); }
        }
        settings.packageDir = folder.fsName; return folder;
    }
    function makeStarter(launcherPath, requestPath) {
        function safePath(p) { p = String(p); if (/["\r\n\x00]/.test(p)) { fail("路径包含无效字符。"); } return p; }
        var command = 'wscript.exe "' + safePath(launcherPath) + '" "' + safePath(requestPath) + '"';
        return 'Option Explicit\r\nCreateObject("WScript.Shell").Run "' + command.replace(/"/g, '""') + '", 0, False\r\n';
    }
    function buildRequest(options) {
        if (options.mode !== "odb" && options.mode !== "template") { fail("请选择 ODB 或模型缓存模式。"); }
        var source = trim(options.sourcePath), ext = options.mode === "odb" ? /\.odb$/i : /\.meshcache\.json$/i;
        if (!source || !ext.test(source) || !File(source).exists) { fail(options.mode === "odb" ? "请选择存在的 ODB 文件。" : "请选择存在的 .meshcache.json 模型缓存。"); }
        var frameText = trim(options.frame), frame = null;
        if (frameText) { if (!/^\d+$/.test(frameText) || Number(frameText) > 2147483647) { fail("帧编号须为从 0 开始的非负整数，或留空自动推断。"); } frame = Number(frameText); }
        var eps = File(options.epsPath);
        var request = {version: VERSION, mode: options.mode, epsPath: eps.fsName,
            odbPath: options.mode === "odb" ? File(source).fsName : null,
            templatePath: options.mode === "template" ? File(source).fsName : null,
            stepName: trim(options.step) || null, frameIndex: frame,
            outputDir: trim(options.outputDir) || (eps.parent.fsName + "/SurfaceMeshRebuild_v0.2.2"),
            cacheDir: trim(options.cacheDir) || (Folder.userData.fsName + SETTINGS + "/cache"), jobDir: options.jobDir};
        var optional = ["abaqusCommand", "pythonExecutable", "cameraPath"];
        for (var i = 0; i < optional.length; i++) { var key = optional[i]; if (trim(options[key])) { request[key] = trim(options[key]); } }
        return request;
    }
    function runJob(request, launcherPath, hooks) {
        hooks = hooks || {};
        var now = hooks.now || function() { return new Date().getTime(); };
        var sleep = hooks.sleep || function(ms) { $.sleep(ms); };
        var start = now(), received = false, cancelledAt = null, lastReadError = "", status = null;
        var startupTimeout = hooks.startupTimeout === undefined ? 30000 : hooks.startupTimeout;
        var totalTimeout = hooks.totalTimeout === undefined ? 1800000 : hooks.totalTimeout;
        var cancelTimeout = hooks.cancelTimeout === undefined ? 15000 : hooks.cancelTimeout;
        ensureFolder(request.jobDir);
        var requestFile = File(request.jobDir + "/request.json"), starter = File(request.jobDir + "/start.vbs"), statusFile = File(request.jobDir + "/status.json");
        var cancelFile = File(request.jobDir + "/cancel.flag"), terminalFile = File(request.jobDir + "/terminal.json");
        if (!File(launcherPath).exists) { fail("启动程序不存在：" + launcherPath); }
        writeText(requestFile, stringifyJSON(request)); writeStarter(starter, makeStarter(launcherPath, requestFile.fsName));
        if (!starter.execute()) { fail("无法启动后台程序。请检查 Windows Script Host，任务目录：" + request.jobDir); }
        while (true) {
            if (hooks.isCancelled && hooks.isCancelled() && cancelledAt === null) { writeText(cancelFile, "cancel\n"); cancelledAt = now(); }
            var currentStatusFile = terminalFile.exists ? terminalFile : statusFile;
            if (currentStatusFile.exists) {
                try {
                    var parsed = parseJSON(readText(currentStatusFile, 1048576));
                    if (!parsed || !/^(running|done|error|cancelled)$/.test(parsed.state)) { fail("后台状态缺少有效 state。"); }
                    status = parsed; received = true; lastReadError = "";
                } catch (e) { lastReadError = String(e); }
            }
            if (hooks.onProgress) {
                hooks.onProgress(status || {state: "running", progress: 0, message: "正在启动后台程序……"}, cancelledAt !== null);
            }
            // Window.update() can deliver a cancel click during the progress callback.
            if (hooks.isCancelled && hooks.isCancelled() && cancelledAt === null) { writeText(cancelFile, "cancel\n"); cancelledAt = now(); }
            if (status) {
                if (status.state === "error") { fail(String(status.error || status.message || "后台重建失败。") + "\n任务目录：" + request.jobDir); }
                if (status.state === "cancelled") { return status; }
                if (status.state === "done") {
                    if (cancelledAt !== null) { return {state: "cancelled", pending: false, completedBeforeCancel: true, message: "后台已完成；已按取消请求停止自动打开结果。", outputEPS: status.outputEPS}; }
                    return status;
                }
            }
            if (cancelledAt !== null && now() - cancelledAt >= cancelTimeout) {
                return {state: "cancelled", pending: true, message: "已发送取消请求，后台尚未确认停止；本窗口不会打开结果。任务目录：" + request.jobDir};
            }
            if ((!received && now() - start >= startupTimeout) || now() - start >= totalTimeout) {
                writeText(cancelFile, "cancel\n");
                fail((received ? "重建超过 30 分钟，已请求取消。" : "启动 30 秒后仍无有效状态，已请求取消；请检查程序目录及后台日志。") + (lastReadError ? "\n" + lastReadError : "") + "\n任务目录：" + request.jobDir);
            }
            sleep(150);
        }
    }
    function openResult(host, result, sourceEPS) {
        var output = trim(result.outputEPS);
        if (!/\.eps$/i.test(output) || !File(output).exists) { fail("后台没有生成可打开的 EPS：" + output); }
        if (File(output).fsName.toLowerCase() === File(sourceEPS).fsName.toLowerCase()) { fail("后台返回了原 EPS 路径；为保护原文档，不打开该结果。"); }
        var docs = [], i; for (i = 0; i < host.documents.length; i++) { docs.push(host.documents[i]); }
        var opened = host.open(File(output)); if (!opened) { fail("Illustrator 未返回结果文档。"); }
        for (i = 0; i < docs.length; i++) { if (opened === docs[i]) { return {document: opened, alreadyOpen: true}; } }
        var layer = opened.layers.length ? opened.layers[0] : opened.layers.add(); layer.name = TITLE;
        return {document: opened, alreadyOpen: false};
    }
    function main() {
        var source = validateDocument(app), settings = loadSettings(), w = new Window("dialog", TITLE);
        w.orientation = "column"; w.alignChildren = "fill";
        w.add("statictext", undefined, "读取由 Abaqus 导出的原始 EPS，依据 ODB 网格或模型缓存重建；结果在新文档打开。");
        w.add("statictext", undefined, "不支持 Illustrator 重新保存的 EPS。已有编辑请另存保留，再打开原始 Abaqus EPS。");
        w.add("statictext", undefined, "当前 EPS：" + source.file.name);
        var modeRow = w.add("group"); modeRow.add("statictext", undefined, "数据来源");
        var mode = modeRow.add("dropdownlist", undefined, ["选择 ODB 并重建", "选择模型缓存重建（无需 ODB）"]); mode.selection = settings.mode === "template" ? 1 : 0;
        var pathRow = w.add("group"), sourcePath = pathRow.add("edittext", undefined, mode.selection.index === 1 ? (settings.lastTemplatePath || "") : (settings.lastOdbPath || ""));
        sourcePath.characters = 65; var browse = pathRow.add("button", undefined, "选择…");
        var outputRow = w.add("group"); outputRow.add("statictext", undefined, "输出目录");
        var output = outputRow.add("edittext", undefined, settings.outputDir || ""); output.characters = 53;
        var outputBrowse = outputRow.add("button", undefined, "选择…");
        w.add("statictext", undefined, "输出目录留空：在当前 EPS 旁建立 SurfaceMeshRebuild_v0.2.2 文件夹。");
        var advancedToggle = w.add("checkbox", undefined, "高级设置（分析步 / 帧 / 相机 / 运行环境，可留空）");
        var advanced = w.add("panel", undefined, "可选参数"); advanced.orientation = "column"; advanced.alignChildren = "fill";
        var frameRow = advanced.add("group"); frameRow.add("statictext", undefined, "分析步"); var step = frameRow.add("edittext", undefined, ""); step.characters = 18;
        frameRow.add("statictext", undefined, "帧编号（0 起）"); var frame = frameRow.add("edittext", undefined, ""); frame.characters = 10;
        advanced.add("statictext", undefined, "自动读取 EPS 的 Step、Step Time、变量及统一 / X、Y、Z 变形倍率。");
        function field(label, value) { var row = advanced.add("group"); row.add("statictext", undefined, label); var edit = row.add("edittext", undefined, value || ""); edit.characters = 55; return edit; }
        var camera = field("相机 JSON", settings.cameraPath), abaqus = field("Abaqus 命令", settings.abaqusCommand), python = field("Python 路径", settings.pythonExecutable);
        advanced.visible = false; advanced.maximumSize = [10000, 0];
        advancedToggle.onClick = function() { advanced.visible = advancedToggle.value; advanced.maximumSize = [10000, advancedToggle.value ? 10000 : 0]; w.layout.layout(true); w.center(); };
        var meter = w.add("progressbar", undefined, 0, 100); meter.preferredSize = [720, 18];
        var status = w.add("edittext", undefined, "准备就绪。原 EPS 不会被覆盖；结果不会自动保存为 AI。", {multiline: true, readonly: true}); status.preferredSize = [720, 75];
        var cacheText = w.add("edittext", undefined, settings.lastTemplatePath || "成功后将在这里显示可复用的模型缓存。", {readonly: true}); cacheText.characters = 85;
        var buttons = w.add("group"); buttons.alignment = "right";
        var run = buttons.add("button", undefined, "重建并打开副本"), close = buttons.add("button", undefined, "关闭");
        var busy = false, cancel = false, currentMode = mode.selection.index === 1 ? "template" : "odb";
        function remember() {
            settings.mode = currentMode; settings[currentMode === "odb" ? "lastOdbPath" : "lastTemplatePath"] = trim(sourcePath.text);
            settings.outputDir = trim(output.text); settings.cameraPath = trim(camera.text); settings.abaqusCommand = trim(abaqus.text); settings.pythonExecutable = trim(python.text);
        }
        mode.onChange = function() { remember(); currentMode = mode.selection.index === 1 ? "template" : "odb"; sourcePath.text = settings[currentMode === "odb" ? "lastOdbPath" : "lastTemplatePath"] || ""; };
        browse.onClick = function() {
            var file = File.openDialog(currentMode === "odb" ? "选择 ODB 文件" : "选择 .meshcache.json 模型缓存", currentMode === "odb" ? "*.odb" : "*.meshcache.json", false);
            if (file) { sourcePath.text = file.fsName; remember(); try { saveSettings(settings); } catch (e) { status.text = "设置未保存：" + e; } }
        };
        outputBrowse.onClick = function() { var folder = Folder.selectDialog("选择重建结果目录（不覆盖原 EPS）"); if (folder) { output.text = folder.fsName; } };
        close.onClick = function() { if (busy) { cancel = true; close.text = "正在请求取消…"; } else { w.close(); } };
        w.onClose = function() { if (busy) { cancel = true; return false; } return true; };
        function enabled(value) { run.enabled = mode.enabled = sourcePath.enabled = browse.enabled = output.enabled = outputBrowse.enabled = advancedToggle.enabled = advanced.enabled = value; }
        run.onClick = function() {
            if (busy) { return; }
            try {
                var current = validateDocument(app);
                if (current.document !== source.document || current.file.fsName !== source.file.fsName) { fail("当前 EPS 已改变。请关闭本窗口后重新运行脚本。"); }
                remember();
                var packageDir = resolvePackage($.fileName, settings, function(prompt) { return Folder.selectDialog(prompt); });
                if (!packageDir) { status.text = "未选择程序目录，尚未启动重建。"; return; }
                var jobDir = Folder.temp.fsName + "/SurfaceMeshRebuild-v0.2.2-" + new Date().getTime() + "-" + Math.floor(Math.random() * 1000000000);
                var request = buildRequest({mode: currentMode, sourcePath: sourcePath.text, epsPath: source.file.fsName, step: step.text, frame: frame.text,
                    outputDir: output.text, cacheDir: settings.cacheDir, abaqusCommand: abaqus.text, pythonExecutable: python.text, cameraPath: camera.text, jobDir: jobDir});
                saveSettings(settings);
                busy = true; cancel = false; enabled(false); close.text = "取消"; meter.value = 0; status.text = "正在准备后台重建……"; w.update();
                var uiStarted = new Date().getTime();
                var result = runJob(request, packageDir.fsName + "/launch_job.vbs", {isCancelled: function() { return cancel; }, onProgress: function(s, cancelling) {
                    var progress = typeof s.progress === "number" && isFinite(s.progress) ? s.progress : 0;
                    meter.value = Math.max(0, Math.min(100, progress)); status.text = cancelling ? "正在请求取消，请稍等……" : String(s.message || "后台处理中……") + "\n已用 " + Math.round((new Date().getTime() - uiStarted) / 1000) + " 秒"; w.update();
                }});
                if (result.state === "cancelled") { status.text = result.message || "后台已取消；原文档未修改。"; return; }
                if (trim(result.templatePath)) { settings.lastTemplatePath = String(result.templatePath); cacheText.text = settings.lastTemplatePath; saveSettings(settings); }
                status.text = "重建完成，正在导入 EPS 副本。Illustrator 导入期间请稍等……"; close.enabled = false; w.update();
                if (cancel) { status.text = "已取消自动打开结果，重建文件已保留。"; return; }
                var opened = openResult(app, result, source.file.fsName); meter.value = 100;
                status.text = String(result.message || "") + "\n" + (opened.alreadyOpen ? "结果文档已打开，未修改已有文档。" : "已打开重建副本，原文档未修改。") + (result.reportPath ? "\n报告：" + result.reportPath : "");
            } catch (e) { status.text = "执行失败：" + String(e); alert(status.text); }
            finally { busy = false; enabled(true); close.enabled = true; close.text = "关闭"; w.update(); }
        };
        w.layout.layout(true); w.center(); w.show();
    }
    return {version: VERSION, parseJSON: parseJSON, stringifyJSON: stringifyJSON, validateDocument: validateDocument,
        resolvePackage: resolvePackage, makeStarter: makeStarter, buildRequest: buildRequest, runJob: runJob,
        openResult: openResult, loadSettings: loadSettings, saveSettings: saveSettings, main: main};
}());
if (typeof SURFACE_MESH_REBUILD_TEST === "undefined") {
    try { SurfaceMeshRebuildV020.main(); } catch (e) { alert("表面网格重建 v0.2.2：\n" + e + "\n行号：" + e.line); }
}
