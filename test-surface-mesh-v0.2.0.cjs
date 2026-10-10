// Mock contracts only. Does not start WSH/Python, open Illustrator, or write source data.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const scriptPath = path.join(__dirname, 'SurfaceMeshRebuild_v0.2.0.jsx');
const raw = fs.readFileSync(scriptPath);
const script = raw.toString('utf8').replace(/^\uFEFF/, '').replace(/^#target.*$/m, '');
const normalize = value => path.posix.normalize(String(value && value.fsName || value).replace(/\\/g, '/'));
const plain = value => JSON.parse(JSON.stringify(value));
const defaults = {epsPath:'C:/input/源图.eps', sourcePath:'C:/model/模型.odb', mode:'odb', jobDir:'C:/temp/job'};

function environment(options = {}) {
  const files = new Map(), folders = new Set(['C:/', 'C:/input', 'C:/model', 'C:/temp', 'C:/user', 'C:/package', 'C:/package/v0.2.0']);
  const controls = [], events = [], alerts = [], writes = [];
  let clock = 0, sleepCount = 0, currentRead = '', openedMode = '', counter = 0;
  const e = {files, folders, controls, events, alerts, writes, options,
    put(name, text = '') {files.set(normalize(name), String(text)); return name;},
    tick(ms) {clock += ms;}, get clock() {return clock;},
    status(status, jobDir = defaults.jobDir) {e.put(jobDir + '/status.json', typeof status === 'string' ? status : JSON.stringify(status));},
    read(name) {return files.get(normalize(name));}
  };
  e.put(defaults.epsPath, '%!PS'); e.put(defaults.sourcePath); e.put('C:/model/网格.meshcache.json');
  e.put('C:/package/v0.2.0/launch_job.vbs'); e.put('C:/output/result.eps');
  function Folder(value) {
    const name = normalize(value);
    return {fsName:name, get exists() {return folders.has(name);}, get parent() {return Folder(path.posix.dirname(name));},
      create() {if (options.failFolder) return false; folders.add(name); return true;}, toString() {return name;}};
  }
  Folder.userData = Folder('C:/user'); Folder.temp = Folder('C:/temp');
  Folder.selectDialog = prompt => {events.push({action:'folderDialog', prompt}); return options.chooseFolder ? Folder(options.chooseFolder) : null;};
  function File(value) {
    if (value === undefined || value === null) throw new Error('no fullName');
    const name = normalize(value);
    let mode;
    const file = {fsName:name, name:path.posix.basename(name), get parent() {return Folder(path.posix.dirname(name));},
      get exists() {return files.has(name);}, get length() {return (files.get(name) || '').length;},
      open(nextMode) {mode = nextMode; if (options.failWrite && nextMode === 'w') return false; if (nextMode === 'r' && !files.has(name)) return false; return true;},
      read() {return files.get(name);}, write(text) {if (options.failWrite) return false; files.set(name, String(text)); writes.push({name, text:String(text), encoding:file.encoding}); return true;}, close() {},
      execute() {events.push({action:'execute', path:name}); if (options.onExecute) options.onExecute(e); return options.execute !== false;}, toString() {return name;}};
    return file;
  }
  File.openDialog = (prompt, filter, multi) => {events.push({action:'fileDialog', prompt, filter, multi}); return options.chooseFile ? File(options.chooseFile) : null;};
  const originalLayer = {name:'Original layer'}, originalDoc = {name:'源图.eps', fullName:File(defaults.epsPath), saved:true, layers:[originalLayer]};
  if (options.unsaved) originalDoc.saved = false;
  if (options.untitled) Object.defineProperty(originalDoc, 'fullName', {get() {throw new Error('untitled');}});
  if (options.nonEPS) {originalDoc.fullName = File('C:/input/source.ai'); e.put(originalDoc.fullName);}
  const app = {documents:options.noDoc ? [] : [originalDoc], activeDocument:options.noDoc ? null : originalDoc,
    open(file) {
      events.push({action:'open', path:file.fsName});
      if (options.failOpen) throw new Error('import failed');
      if (options.returnExisting) return originalDoc;
      const layers = options.noLayers ? [] : [{name:'Imported layer'}];
      layers.add = () => {const layer = {name:''}; layers.push(layer); return layer;};
      const doc = {name:file.name, fullName:file, saved:true, layers}; app.documents.push(doc); app.activeDocument = doc; return doc;
    }};
  function control(type, text, parent) {
    const c = {id:counter++, type, text, parent, enabled:true, visible:true, value:0, children:[],
      add(kind, bounds, label) {const child = control(kind, label, c); c.children.push(child); controls.push(child); return child;},
      layout:{layout() {}}, center() {}, show() {}, close() {events.push({action:'close'});},
      update() {events.push({action:'update', text:controls.map(v=>v.text), progress:controls.find(v=>v.type==='progressbar')?.value}); if (options.onUpdate) options.onUpdate(e);}};
    if (type === 'dropdownlist') {let selection; Object.defineProperty(c, 'selection', {get(){return selection;},set(v){selection=typeof v==='number'?{index:v}:v;}});}
    return c;
  }
  function ClockDate() {this.getTime = () => clock;}
  const context = {SURFACE_MESH_REBUILD_TEST:true, app, File, Folder, Date:ClockDate,
    $:{fileName:options.scriptFile || 'C:/package/SurfaceMeshRebuild_v0.2.0.jsx', sleep(ms) {clock+=ms; sleepCount++; if(options.onSleep)options.onSleep(e,sleepCount);}},
    alert(message) {alerts.push(String(message));}, Window:function(type,title){events.push({action:'window',title}); const w=control(type,title); e.window=w;return w;}};
  vm.createContext(context); vm.runInContext(script, context, {filename:scriptPath});
  e.api=context.SurfaceMeshRebuildV020; e.context=context; e.app=app; e.originalDoc=originalDoc; e.originalLayer=originalLayer; e.File=File; e.Folder=Folder;
  e.launch=()=>{e.api.main();return e;}; e.run=()=>controls.find(c=>c.type==='button' && c.text==='重建并打开副本').onClick();
  e.request=extra=>e.api.buildRequest({...defaults,...extra});
  e.hooks=extra=>({now:()=>clock,sleep(ms){clock+=ms; sleepCount++;if(options.onSleep)options.onSleep(e,sleepCount);},...extra});
  e.assertUntouched=()=>{assert.equal(originalDoc.name,'源图.eps');assert.equal(originalDoc.layers.length,1);assert.equal(originalLayer.name,'Original layer');assert.equal(originalDoc.saved,!options.unsaved);};
  return e;
}

let count=0;
function test(name, fn) {fn(); count++; console.log('PASS '+name);}
function runJob(e, hooks={}) {return e.api.runJob(e.request(), 'C:/package/v0.2.0/launch_job.vbs',e.hooks(hooks));}

test('source is syntactically valid and versioned consistently',()=>{assert.equal(environment().api.version,'0.2.0');assert(!/Py30|Py60|q1165|C:[\\/]ABAQUS/i.test(script));});
test('strict JSON handles Unicode, escapes, nested values, BOM and exponents',()=>{
  const {api}=environment(); const data='\uFEFF{"path":"C:\\\\中文\\n\\u0041","a":[true,false,null,-1.5e2,{}]}';
  assert.deepEqual(plain(api.parseJSON(data)),JSON.parse(data.slice(1)));
  assert.equal(api.stringifyJSON(api.parseJSON(data)),JSON.stringify(JSON.parse(data.slice(1))).replace(/\\n/g,'\\u000a'));
});
test('strict parser rejects executable text, trailing text, bad numbers, and control characters',()=>{
  const {api}=environment(); for(const value of ['({a:1})','null;alert(1)','[1,]','{"a":1,}','01','NaN','Infinity','1e999','"line\nfeed"','"\\q"','{"a":undefined}','']) assert.throws(()=>api.parseJSON(value),undefined,value);
});
test('JSON prototype pollution and excessive nesting are rejected',()=>{
  const {api}=environment(); for(const key of ['__proto__','constructor','prototype'])assert.throws(()=>api.parseJSON('{"'+key+'":{}}'));
  assert.throws(()=>api.parseJSON('['.repeat(62)+'0'+']'.repeat(62))); assert.throws(()=>api.stringifyJSON(Infinity));
});
test('settings round trip uses the versioned user-data directory',()=>{
  const e=environment(), value={lastOdbPath:'D:/中文 路径/模型.odb',packageDir:'C:/some folder',outputDir:''};
  e.api.saveSettings(value);assert.deepEqual(plain(e.api.loadSettings()),value);assert(e.read('C:/user/SurfaceMeshRebuild/v0.2.0/settings.json'));
});
test('invalid, array, oversized settings safely reset without executing contents',()=>{
  const e=environment();for(const value of ['alert(1)','[]','x'.repeat(140000)]){e.put('C:/user/SurfaceMeshRebuild/v0.2.0/settings.json',value);assert.deepEqual(plain(e.api.loadSettings()),{});}
});
test('document guards reject no document, untitled, unsaved, non-EPS and absent source',()=>{
  for(const options of [{noDoc:true},{untitled:true},{unsaved:true},{nonEPS:true}]){const e=environment(options);assert.throws(()=>e.api.validateDocument(e.app));assert.equal(e.events.length,0);}
  const e=environment();e.files.delete(defaults.epsPath);assert.throws(()=>e.api.validateDocument(e.app));
});
test('guards direct users to original Abaqus EPS and never ask to overwrite it',()=>{
  for(const options of [{noDoc:true},{untitled:true},{unsaved:true},{nonEPS:true}]){const e=environment(options);assert.throws(()=>e.api.validateDocument(e.app),/Abaqus/);}
  const e=environment({unsaved:true});assert.throws(()=>e.api.validateDocument(e.app),/另存保留编辑[\s\S]*重新打开原始 Abaqus EPS/);
  const ui=environment().launch();assert(ui.controls.some(v=>String(v.text).includes('不支持 Illustrator 重新保存的 EPS')));assert(!/请先保存 EPS|请先保存为 EPS/.test(script));
});
test('saved EPS passes document validation without mutation',()=>{const e=environment();const result=e.api.validateDocument(e.app);assert.equal(result.document,e.originalDoc);assert.equal(result.file.fsName,defaults.epsPath);e.assertUntouched();});
test('request defaults infer step and frame, output beside EPS, cache in user-data',()=>{
  const e=environment(),r=e.request();assert.equal(r.stepName,null);assert.equal(r.frameIndex,null);assert.equal(r.odbPath,defaults.sourcePath);assert.equal(r.templatePath,null);
  assert.equal(r.outputDir,'C:/input/SurfaceMeshRebuild_v0.2.0');assert.equal(r.cacheDir,'C:/user/SurfaceMeshRebuild/v0.2.0/cache');assert.equal(r.version,'0.2.0');
});
test('template mode requires a cache and does not require any ODB',()=>{
  const e=environment();e.files.delete(defaults.sourcePath);const r=e.request({mode:'template',sourcePath:'C:/model/网格.meshcache.json'});assert.equal(r.odbPath,null);assert.equal(r.templatePath,'C:/model/网格.meshcache.json');
});
test('source modes, extensions and missing input are checked',()=>{const e=environment();for(const args of [{mode:'arbitrary'},{sourcePath:''},{sourcePath:'C:/absent.odb'},{mode:'template'},{sourcePath:'C:/model/网格.meshcache.json'}])assert.throws(()=>e.request(args));});
test('zero-based frame overrides preserve 0 and reject invalid integers',()=>{
  const e=environment();assert.equal(e.request({frame:'0',step:' Step-9 '}).frameIndex,0);assert.equal(e.request({frame:'0',step:' Step-9 '}).stepName,'Step-9');
  for(const frame of ['-1','1.0','x','1e3','2147483648'])assert.throws(()=>e.request({frame}));
});
test('only named optional fields are passed; arbitrary shell field is discarded',()=>{
  const r=environment().request({abaqusCommand:' abq2024 ',pythonExecutable:' C:/python.exe ',cameraPath:' C:/camera.json ',shell:'delete everything'});
  assert.equal(r.abaqusCommand,'abq2024');assert.equal(r.pythonExecutable,'C:/python.exe');assert.equal(r.cameraPath,'C:/camera.json');assert(!('shell'in r));
});
test('sibling package wins without asking for a directory',()=>{
  const e=environment(),settings={};const p=e.api.resolvePackage('C:/package/SurfaceMeshRebuild_v0.2.0.jsx',settings,()=>{throw Error('unexpected picker');});assert.equal(p.fsName,'C:/package/v0.2.0');assert.equal(settings.packageDir,p.fsName);
});
test('copied JSX finds a previously selected program directory',()=>{
  const e=environment();const p=e.api.resolvePackage('C:/Illustrator/Scripts/rebuild.jsx',{packageDir:'C:/package/v0.2.0'},()=>{throw Error('unexpected picker');});assert.equal(p.fsName,'C:/package/v0.2.0');
});
test('first-run picker accepts the version folder or package root and remembers it',()=>{
  for(const selected of ['C:/package','C:/package/v0.2.0']){const e=environment(),settings={};const p=e.api.resolvePackage('C:/Scripts/rebuild.jsx',settings,()=>e.Folder(selected));assert.equal(p.fsName,'C:/package/v0.2.0');assert.equal(settings.packageDir,p.fsName);}
});
test('package picker cancellation is harmless; invalid selection reports an error',()=>{
  const e=environment();assert.equal(e.api.resolvePackage('C:/Scripts/rebuild.jsx',{},()=>null),null);assert.throws(()=>e.api.resolvePackage('C:/Scripts/rebuild.jsx',{},()=>e.Folder('C:/wrong')));assert.equal(e.events.length,0);
});
test('starter quotes spaced and Unicode paths as VBScript string content',()=>{
  const text=environment().api.makeStarter('C:/完整 程序/launch_job.vbs','C:/作业 路径/request.json');
  assert.equal(text,'Option Explicit\r\nCreateObject("WScript.Shell").Run "wscript.exe ""C:/完整 程序/launch_job.vbs"" ""C:/作业 路径/request.json""", 0, False\r\n');
});
test('starter rejects quote, newline and NUL command injection',()=>{const {api}=environment();for(const invalid of ['a"b','a\nb','a\rb','a\0b'])assert.throws(()=>api.makeStarter(invalid,'C:/request.json'));});
test('worker completes with UTF-8 JSON and UTF-16LE BOM VBS starter',()=>{
  const e=environment({onExecute(e){e.status({state:'done',progress:100,outputEPS:'C:/output/result.eps'});}});const result=runJob(e);assert.equal(result.state,'done');
  const write=e.writes.find(w=>w.name.endsWith('/start.vbs'));assert.equal(write.encoding,'BINARY');const bytes=Buffer.from(write.text,'latin1');assert.deepEqual([...bytes.subarray(0,2)],[255,254]);
  assert.equal(bytes.subarray(2).toString('utf16le'),e.api.makeStarter('C:/package/v0.2.0/launch_job.vbs','C:/temp/job/request.json'));
  assert.equal(JSON.parse(e.read('C:/temp/job/request.json')).epsPath,defaults.epsPath);
});
test('launcher absence and File.execute failure are explicit errors',()=>{
  const e=environment();e.files.delete('C:/package/v0.2.0/launch_job.vbs');assert.throws(()=>runJob(e),/启动程序不存在/);assert.equal(e.events.length,0);
  assert.throws(()=>runJob(environment({execute:false})),/无法启动后台程序/);
});
test('startup without status times out and writes cancellation flag',()=>{const e=environment();assert.throws(()=>runJob(e,{startupTimeout:300}),/无有效状态/);assert(e.read('C:/temp/job/cancel.flag'));assert.equal(e.clock,300);});
test('malformed and partial state is retried until atomically replaced',()=>{
  const seen=[];const e=environment({onExecute(e){e.status('{"state":');},onSleep(e,n){e.status(n===1?{state:'running',progress:25,message:'working'}:{state:'done',progress:100,outputEPS:'C:/output/result.eps'});}});
  assert.equal(runJob(e,{onProgress:s=>seen.push(s.progress)}).state,'done');assert.deepEqual(seen,[0,25,100]);
});
test('unknown or unsafe state never executes and eventually times out',()=>{
  const e=environment({onExecute(e){e.status('{"state":"running","__proto__":{}}');}});assert.throws(()=>runJob(e,{startupTimeout:150}),/无有效状态/);
});
test('backend failure is reported with the retained job directory',()=>{
  const e=environment({onExecute(e){e.status({state:'error',error:'mesh mismatch'});}});assert.throws(()=>runJob(e),/mesh mismatch[\s\S]*C:\/temp\/job/);e.assertUntouched();
});
test('progress does not disable the total timeout after startup',()=>{
  const e=environment({onExecute(e){e.status({state:'running',progress:10});}});assert.throws(()=>runJob(e,{totalTimeout:300}),/超过 30 分钟/);assert(e.read('C:/temp/job/cancel.flag'));
});
test('cancellation writes flag and returns only after worker confirmation',()=>{
  const e=environment({onExecute(e){e.status({state:'running',progress:5});},onSleep(e){if(e.read('C:/temp/job/cancel.flag'))e.status({state:'cancelled',message:'stopped'});}});
  const result=runJob(e,{isCancelled:()=>true});assert.equal(result.state,'cancelled');assert.equal(result.message,'stopped');assert(e.read('C:/temp/job/cancel.flag'));
});
test('unconfirmed cancellation is bounded and explicitly marked pending',()=>{
  const e=environment({onExecute(e){e.status({state:'running'});}});const result=runJob(e,{isCancelled:()=>true,cancelTimeout:300});assert.equal(result.pending,true);assert.match(result.message,/尚未确认停止/);assert.equal(e.clock,300);
});
test('cancel click delivered during final progress callback suppresses opening',()=>{
  let cancel=false;const e=environment({onExecute(e){e.status({state:'done',outputEPS:'C:/output/result.eps'});}});const result=runJob(e,{isCancelled:()=>cancel,onProgress(){cancel=true;}});
  assert.equal(result.state,'cancelled');assert.equal(result.completedBeforeCancel,true);assert(e.read('C:/temp/job/cancel.flag'));assert.equal(e.events.filter(v=>v.action==='open').length,0);
});
test('openResult imports only a separate existing EPS and preserves original',()=>{
  const e=environment();const result=e.api.openResult(e.app,{outputEPS:'C:/output/result.eps'},defaults.epsPath);assert.equal(result.alreadyOpen,false);assert.equal(result.document.layers[0].name,'表面网格重建 v0.2.0');assert.equal(e.app.documents.length,2);e.assertUntouched();
});
test('missing, non-EPS, and source-path outputs are never opened',()=>{
  const e=environment();for(const outputEPS of ['',defaults.epsPath,'C:/missing.eps','C:/wrong.ai'])assert.throws(()=>e.api.openResult(e.app,{outputEPS},defaults.epsPath));assert.equal(e.events.filter(v=>v.action==='open').length,0);e.assertUntouched();
});
test('existing documents and import failure cannot rename the source',()=>{
  const e=environment({returnExisting:true});assert.equal(e.api.openResult(e.app,{outputEPS:'C:/output/result.eps'},defaults.epsPath).alreadyOpen,true);e.assertUntouched();
  const f=environment({failOpen:true});assert.throws(()=>f.api.openResult(f.app,{outputEPS:'C:/output/result.eps'},defaults.epsPath),/import failed/);f.assertUntouched();
});
test('new result with no layers receives its own layer',()=>{const e=environment({noLayers:true});e.api.openResult(e.app,{outputEPS:'C:/output/result.eps'},defaults.epsPath);assert.equal(e.app.activeDocument.layers[0].name,'表面网格重建 v0.2.0');e.assertUntouched();});
test('UI displays dynamic modes, blank frame, collapsed optional runtime, no prechosen model',()=>{
  const e=environment().launch();assert.equal(e.events[0].title,'表面网格重建 v0.2.0');assert.equal(e.controls.find(v=>v.type==='dropdownlist').selection.index,0);
  const panel=e.controls.find(v=>v.type==='panel');assert.equal(panel.visible,false);assert(e.controls.some(v=>String(v.text).includes('不沿用上一次帧号')));assert.equal(e.controls.filter(v=>v.type==='edittext')[0].text,'');e.assertUntouched();
});
test('UI remembers independent ODB and cache paths when switching modes',()=>{
  const e=environment();e.api.saveSettings({lastOdbPath:defaults.sourcePath,lastTemplatePath:'C:/model/网格.meshcache.json'});e.launch();
  const mode=e.controls.find(v=>v.type==='dropdownlist'),source=e.controls.find(v=>v.type==='edittext');assert.equal(source.text,defaults.sourcePath);mode.selection=1;mode.onChange();assert.equal(source.text,'C:/model/网格.meshcache.json');mode.selection=0;mode.onChange();assert.equal(source.text,defaults.sourcePath);
});
test('UI rechecks unsaved changes before launching and retains source',()=>{
  const e=environment().launch();e.originalDoc.saved=false;e.run();assert(e.alerts.some(v=>/未保存修改/.test(v)));assert.equal(e.events.filter(v=>v.action==='execute').length,0);assert.equal(e.app.documents.length,1);
});
test('UI runs complete job and flushes import status before blocking app.open',()=>{
  const e=environment({onExecute(e){const request=JSON.parse(e.read(e.events.find(v=>v.action==='execute').path.replace('start.vbs','request.json')));e.status({state:'done',progress:100,outputEPS:'C:/output/result.eps',templatePath:'C:/model/new.meshcache.json',reportPath:'C:/output/report.json'},request.jobDir);}});
  e.api.saveSettings({lastOdbPath:defaults.sourcePath});e.launch();e.run();assert.deepEqual(e.alerts,[]);assert.equal(e.app.documents.length,2);e.assertUntouched();
  const index=e.events.findIndex(v=>v.action==='open'), prior=e.events.slice(0,index).filter(v=>v.action==='update').pop();assert(prior.text.some(v=>String(v).includes('正在导入 EPS 副本')));
  assert.equal(e.api.loadSettings().lastTemplatePath,'C:/model/new.meshcache.json');assert(e.controls.some(v=>v.text==='C:/model/new.meshcache.json'));assert(e.controls.some(v=>String(v.text).includes('C:/output/report.json')));
});
test('UI cancellation during final import repaint also suppresses opening',()=>{
  const e=environment({onExecute(e){const request=JSON.parse(e.read(e.events.find(v=>v.action==='execute').path.replace('start.vbs','request.json')));e.status({state:'done',outputEPS:'C:/output/result.eps'},request.jobDir);},onUpdate(e){if(e.controls.some(v=>String(v.text).includes('正在导入 EPS 副本')))e.window.onClose();}});
  e.api.saveSettings({lastOdbPath:defaults.sourcePath});e.launch();e.run();assert.equal(e.events.filter(v=>v.action==='open').length,0);assert(e.controls.some(v=>String(v.text).includes('已取消自动打开')));e.assertUntouched();
});
test('UI error restores controls and leaves the original EPS untouched',()=>{
  const e=environment({execute:false});e.api.saveSettings({lastOdbPath:defaults.sourcePath});e.launch();e.run();assert(e.alerts.some(v=>/无法启动后台程序/.test(v)));assert.equal(e.controls.find(v=>v.text==='重建并打开副本').enabled,true);e.assertUntouched();
});
test('front end never saves or scans artwork and never evaluates unknown JSON',()=>{
  assert(!/\.(?:save|saveAs|exportFile|executeMenuCommand)\s*\(/.test(script));assert(!/\.(?:pathItems|pageItems)\b/.test(script));assert(!/\beval\s*\(/.test(script));
});
test('JSX delivery is UTF-8 with BOM',()=>assert.deepEqual([...raw.subarray(0,3)],[239,187,191]));
console.log(`Completed ${count} mock tests. Native Illustrator/WSH execution is not covered by these mocks.`);
