"""v0.2.1 background worker. One JSON request, atomic progress, cancellable work."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
import uuid

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE/'_deps'))


class Cancelled(Exception):
    pass


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 19: raise
            time.sleep(.05)


class Job:
    def __init__(self, request_path):
        self.request_path = Path(request_path).resolve()
        self.directory = self.request_path.parent
        self.started = time.perf_counter()
        self.request = {}
        self.last_progress = 0

    def cancelled(self):
        return (self.directory/'cancel.flag').exists()

    def check_cancel(self):
        if self.cancelled(): raise Cancelled('处理已停止，原文档未修改。')

    def status(self, message, progress=None, state='running', **extra):
        if state == 'running': self.check_cancel()
        if progress is not None: self.last_progress = max(self.last_progress, min(100, float(progress)))
        data = dict(extra)
        data.update(version='0.2.1', state=state, progress=self.last_progress,
                    message=str(message), elapsedSeconds=time.perf_counter()-self.started)
        atomic_json(self.directory/'status.json', data)

    def load_request(self):
        self.status('正在读取当前 EPS 与任务设置…', 1)
        self.request = json.loads(self.request_path.read_text(encoding='utf-8-sig'))
        r = self.request
        if r.get('version') != '0.2.1' or r.get('mode') not in ('odb','template'):
            raise ValueError('不支持的任务版本或处理模式。')
        if r.get('jobDir') and Path(r['jobDir']).resolve() != self.directory:
            raise ValueError('任务临时目录不一致。')
        eps = Path(r['epsPath']).resolve()
        if not eps.is_file() or eps.suffix.lower() != '.eps':
            raise ValueError('请选择 Illustrator 中打开的原始 Abaqus EPS。')
        r['epsPath'] = str(eps)
        r['cacheDir'] = str(Path(r.get('cacheDir') or Path.home()/'AppData/Local/SurfaceMeshRebuild/cache').resolve())
        r['outputDir'] = str(Path(r.get('outputDir') or eps.parent/'SurfaceMeshRebuild_v0.2.1').resolve())
        for folder in ('cacheDir','outputDir'): Path(r[folder]).mkdir(parents=True, exist_ok=True)

    def new_output(self):
        folder = Path(self.request['outputDir'])
        stem = re.sub(r'[<>:"/\\|?*]', '_', Path(self.request['epsPath']).stem)
        suffix = '_mesh_v0.2.1_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
        available = min(80, 235-len(str(folder))-len(suffix)-5)
        if available < 12:
            raise ValueError('输出目录路径太长，请选择较短的输出目录。')
        return folder/(stem[:available]+suffix+'.eps')

    def odb_progress(self, event):
        names = {
            'cache.lookup': '正在核对已有帧缓存…',
            'cache.hit': '已命中帧缓存，无需启动 Abaqus。',
            'cache.miss': '首次读取此工况或帧，正在启动 Abaqus…',
            'cache.saved': '模型及当前帧数据已缓存。',
            'odb.open': '正在只读打开 ODB 并核对帧；大文件可能需要几十秒…',
            'odb.displacements': '正在读取当前帧的位移…',
            'odb.mesh': '正在读取模型节点和单元…',
            'odb.done': '已完成 ODB 读取。',
            'odb.complete': '已完成 ODB 读取。',
        }
        message = names.get(event.get('stage'), event.get('message','正在读取 ODB…')) if isinstance(event, dict) else str(event)
        self.status(message, 10)

    def run_odb(self):
        from odb_cache import load_frame, read_eps_metadata
        from camera_sources import discover
        from fit_projection import fit_projection
        from deformation import factors_from_fit
        from eps_io import read_eps, write_rebuilt_eps
        from surface_visibility import exterior_topology, discretize, visible_paths, clip_paths_to_rect
        import numpy as np
        r = self.request
        odb = Path(r.get('odbPath') or '')
        if not odb.is_file() or odb.suffix.lower() != '.odb':
            raise ValueError('请先选择与当前 EPS 对应的 ODB 文件。')
        eps_metadata = read_eps_metadata(r['epsPath'])
        if eps_metadata['deformation'] is None:
            raise ValueError('EPS 缺少明确的变形倍率标注，不能推测倍率。')
        if eps_metadata['deformed_variable'] not in (None, 'U'):
            raise ValueError('当前 EPS 使用的变形变量不是 U；v0.2.1 不能据此可靠重建。')
        caption = '{} · Step Time={} · {}'.format(eps_metadata['step_name'], eps_metadata['step_time_text'], eps_metadata['primary_variable'] or '变量名未标注')
        factors = eps_metadata['deformation']['factors']
        caption += ' · 变形倍率 X={:g} Y={:g} Z={:g}'.format(*factors)
        self.status('已识别 '+caption+'，正在匹配 ODB 帧…', 4)
        frame = load_frame(str(odb), r['epsPath'], r['cacheDir'],
                           abaqus_command=r.get('abaqusCommand') or None,
                           step_name=r.get('stepName') or None, frame_index=r.get('frameIndex'),
                           progress=self.odb_progress, cancel=self.cancelled)
        self.check_cancel()
        self.status('正在配准 EPS 与真实网格…', 20)
        mesh_path = Path(frame['mesh_path'])
        mesh = json.loads(mesh_path.read_text(encoding='utf-8-sig'))
        if len(mesh['instances']) != 1:
            raise ValueError('v0.2.1 当前支持单个网格实例。')
        errors, fit, chosen_camera = [], None, None
        document = read_eps(r['epsPath'])
        expected_ids = sorted(s.index for s in document.removable_strokes)
        candidates = discover(r['epsPath'], str(odb), r.get('cameraPath'), r['cacheDir'])
        for i, (camera, source) in enumerate(candidates):
            self.check_cancel()
            self.status('正在验证视角 '+str(i+1)+'/'+str(len(candidates))+'…', 20)
            candidate_path = self.directory/('camera_'+str(i)+'.json')
            atomic_json(candidate_path, camera)
            try:
                candidate_fit = fit_projection(r['epsPath'], mesh_path, candidate_path)
                if sorted(s['blackStrokeIndex'] for s in candidate_fit['strokeMatches']) != expected_ids:
                    raise ValueError('此视角无法对应当前 EPS 的全部网格黑线。')
                fit = candidate_fit
                chosen_camera = camera
                break
            except (ValueError, KeyError, np.linalg.LinAlgError) as exc:
                errors.append(str(exc))
        if fit is None:
            raise ValueError('无法确定与 EPS 一致的视角。可在高级设置选择从 Abaqus 导出的相机 JSON；原图未修改。\n'+('\n'.join(errors[-2:]) if errors else '未找到相机配置。'))
        fit_path = self.directory/'fit.json'
        atomic_json(fit_path, fit)
        selected = sorted(s['blackStrokeIndex'] for s in fit['strokeMatches'])
        if selected != sorted(s.index for s in document.removable_strokes):
            raise ValueError('当前 EPS 中仍有无法对应真实网格的黑线；已停止，未替换原图。')
        if fit['sourceEPSsha256'] != document.source_sha256:
            raise ValueError('计算期间源 EPS 发生更改，请重新运行。')
        instance = mesh['instances'][0]
        disp = {row[0]:np.asarray(row[1:]) for row in instance['displacements']}
        factors = np.asarray(factors_from_fit(fit), dtype=float)
        coordinates = {row[0]:np.asarray(row[1:])+factors*disp[row[0]] for row in instance['nodes']}
        faces, edges = exterior_topology(instance)
        from fit_projection import project_world
        def project(xyz):
            xy, depth = project_world(xyz, fit)
            if not np.all(np.isfinite(xy)) or not np.all(np.isfinite(depth)) or np.any(depth >= 0):
                raise ValueError('模型穿过相机平面，无法可靠重建。')
            return np.column_stack((xy,depth))
        self.status('正在建立曲面和空间索引…', 30)
        triangles, owners, paths = discretize(faces, edges, coordinates, project, subdivisions=8)
        self.check_cancel()
        def progress(n,total): self.status('判断三维遮挡：'+str(n)+' / '+str(total), 35+40*n/total)
        visible = visible_paths(triangles, owners, paths, progress, depth_tolerance=1e-9)
        visible = clip_paths_to_rect(visible, document.mesh_bounds_pt)
        if not visible: raise ValueError('没有找到可以可靠重建的可见网格边。')
        self.status('正在保存模型缓存…', 80)
        model_hash = frame['model_hash']
        camera_key = hashlib.sha256(json.dumps(chosen_camera, sort_keys=True).encode()).hexdigest()[:12]
        atomic_json(Path(r['cacheDir'])/'cameras'/(camera_key+'.json'), chosen_camera)
        template_path = Path(r['cacheDir'])/'templates'/(str(model_hash)[:16]+'_'+camera_key+'.meshcache.json')
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_warning = None
        try:
            from template_reuse import create_template
            # Persist a self-contained model/view template. Other frame U is never substituted.
            create_template(r['epsPath'], str(mesh_path), str(fit_path), str(template_path))
        except (ValueError, KeyError) as exc:
            template_warning = str(exc)
            template_path = None
        self.check_cancel()
        output = self.new_output()
        self.status('正在写入重建副本…', 93)
        write_rebuilt_eps(document, output, selected, visible)
        report_path = output.with_suffix('.json')
        report = dict(version='0.2.1', mode='odb', sourceEPS=r['epsPath'], sourceEPSsha256=document.source_sha256,
                      sourceODB=str(odb.resolve()), frameMetadata=frame.get('metadata'),
                      modelHash=model_hash, modelCacheHit=frame['model_cache_hit'], frameCacheHit=frame['frame_cache_hit'],
                      deformation=fit['deformation'],
                      validation=fit['validation'], removedStrokes=len(selected), visiblePaths=len(visible),
                      clipBoundsPt=document.mesh_bounds_pt, outputEPS=str(output),
                      templatePath=str(template_path) if template_path else None, templateWarning=template_warning,
                      elapsedSeconds=time.perf_counter()-self.started)
        atomic_json(report_path, report)
        return dict(outputEPS=str(output), templatePath=str(template_path) if template_path else '', reportPath=str(report_path),
                    message='重建完成：'+caption+'。'+('模型缓存已保存，下次可选择缓存处理同模型 EPS。' if template_path else 'ODB 帧缓存已保存；此模型暂不能生成免 ODB 模板。'))

    def run_template(self):
        from template_reuse import rebuild_from_template
        from odb_cache import read_eps_metadata
        r = self.request
        template = Path(r.get('templatePath') or '')
        if not template.is_file(): raise ValueError('请选择之前保存的模型缓存文件。')
        self.status('正在读取模型缓存并匹配当前 EPS…', 5)
        def progress(*args, **kwargs):
            if args and isinstance(args[0], dict):
                event=args[0]; self.status(event.get('message','正在核验当前 EPS…'), 10+.8*float(event.get('progress',0)))
            elif len(args)>=2 and isinstance(args[0],(int,float)) and isinstance(args[1],(int,float)):
                self.status('正在核验当前 EPS…', 10+75*args[0]/max(args[1],1))
            else:
                self.status(str(args[0]) if args else '正在核验当前 EPS…')
        result = rebuild_from_template(r['epsPath'], str(template), r['outputDir'], progress=progress, cancel=self.cancelled)
        self.check_cancel()
        result.setdefault('templatePath', str(template.resolve()))
        metadata = read_eps_metadata(r['epsPath'])
        result.setdefault('message','已使用模型缓存重建：{} · Step Time={} · {}。未打开 ODB。'.format(
            metadata['step_name'],metadata['step_time_text'],metadata['primary_variable'] or '变量名未标注'))
        return result

    def run(self):
        try:
            self.load_request()
            result = self.run_odb() if self.request['mode']=='odb' else self.run_template()
            message = result.pop('message','重建完成。')
            self.status(message, 100, state='done', **result)
            return 0
        except BaseException as exc:
            cancelled = self.cancelled() or isinstance(exc, Cancelled)
            message = '处理已停止，原文档未修改。' if cancelled else str(exc)
            (self.directory/'error.log').write_text(traceback.format_exc(), encoding='utf-8')
            self.status(message, state='cancelled' if cancelled else 'error', error=message)
            return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    sys.exit(Job(parser.parse_args().request).run())
