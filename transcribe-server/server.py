# -*- coding: utf-8 -*-
"""
语音转写网关服务器（独立服务，端口 8300）
- 对外提供 OpenAI 兼容接口 POST /v1/audio/transcriptions、GET /v1/models
- API Key 鉴权、限时开放（每日时段 / 日期范围 / 永久）
- model 字段热切换（调用 whisper-server /load）
- 模型下载/删除（供 server-manager 调用）
内部转发到 whisper-server.exe（127.0.0.1:8301）
仅使用 Python 标准库
"""
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IS_WIN = os.name == 'nt'
NO_WINDOW = 0x08000000 if IS_WIN else 0

ROOT = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(ROOT, 'models')
CONFIG_PATH = os.path.join(ROOT, 'config.json')
WHISPER_EXE = (os.environ.get('WHISPER_SERVER_EXE') or ''
               or os.path.normpath(os.path.join(ROOT, '..', 'Release', 'whisper-server.exe')))

GATE_PORT = 8300          # 对外网关端口
ENGINE_PORT = 8301        # 内部 whisper-server 端口（仅本机）
DEFAULT_MODEL = 'base'    # 无模型时启动参数占位，实际不可转写

# HuggingFace ggml 模型清单（名称 -> (大小描述, hf-mirror URL)）
MODELS_CATALOG = {
    'tiny':         ('75MB',  'https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin'),
    'base':         ('142MB', 'https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-base.bin'),
    'small':        ('466MB', 'https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-small.bin'),
    'medium':       ('1.5GB', 'https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin'),
    'large-v3-turbo': ('1.6GB', 'https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin'),
    'large-v3':     ('3.1GB', 'https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin'),
}

_config_lock = threading.RLock()   # 可重入：load_config 缺文件时会嵌套调 save_config
_engine_proc = None
_engine_lock = threading.RLock()   # 可重入：start_engine 持锁时会调 _engine_running()
_downloads = {}   # name -> {'progress': 0-100, 'status': 'downloading'|'done'|'error', 'msg': str}


# ---------------- 配置 ----------------

def default_config():
    key = 'sk-' + secrets.token_hex(16)
    return {
        'apiKey': key,
        # 多 Key 列表：{key, enabled, note, created}
        'apiKeys': [{'key': key, 'enabled': True, 'note': '默认',
                     'created': datetime.now().strftime('%Y-%m-%d %H:%M')}],
        'schedule': {'mode': 'always'},   # always | daily | range
        # daily:  {'mode':'daily', 'start':'09:00', 'end':'18:00'}
        # range:  {'mode':'range', 'start':'2026-08-29 10:00', 'end':'2026-09-05 22:00'}
        'currentModel': None,
    }


def load_config():
    with _config_lock:
        if not os.path.exists(CONFIG_PATH):
            cfg = default_config()
            save_config(cfg)
            return cfg
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            # 兼容缺字段（apiKeys 不在此注入，由 load_keys 统一迁移，避免覆盖旧 key）
            d = default_config()
            for k, v in d.items():
                if k != 'apiKeys':
                    cfg.setdefault(k, v)
            return cfg
        except Exception:
            return default_config()


def save_config(cfg):
    with _config_lock:
        tmp = CONFIG_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)


def update_config(fn):
    """读-改-写配置（线程安全）"""
    cfg = load_config()
    fn(cfg)
    save_config(cfg)
    return cfg


# ---------------- 限时开放 ----------------

def _parse_hm(s):
    h, m = s.split(':')
    return int(h), int(m)


def is_open_now(cfg=None):
    """当前是否在开放时段内"""
    cfg = cfg or load_config()
    sch = cfg.get('schedule') or {'mode': 'always'}
    mode = sch.get('mode', 'always')
    if mode == 'always':
        return True
    now = datetime.now()
    if mode == 'daily':
        try:
            sh, sm = _parse_hm(sch.get('start', '00:00'))
            eh, em = _parse_hm(sch.get('end', '23:59'))
        except Exception:
            return True
        cur = now.hour * 60 + now.minute
        s = sh * 60 + sm
        e = eh * 60 + em
        if s <= e:
            return s <= cur <= e
        # 跨天时段（如 22:00-06:00）
        return cur >= s or cur <= e
    if mode == 'range':
        try:
            st = datetime.strptime(sch.get('start', ''), '%Y-%m-%d %H:%M')
            et = datetime.strptime(sch.get('end', ''), '%Y-%m-%d %H:%M')
        except Exception:
            return True
        return st <= now <= et
    return True


# ---------------- API Key 管理（多 Key）----------------

def _now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M')


def load_keys():
    """返回 Key 列表 [{key, enabled, note, created}]。旧版单个 apiKey 配置自动迁移。"""
    cfg = load_config()
    keys = cfg.get('apiKeys')
    if isinstance(keys, list) and keys and all(isinstance(k, dict) for k in keys):
        return keys
    old = cfg.get('apiKey') or 'sk-' + secrets.token_hex(16)
    keys = [{'key': old, 'enabled': True, 'note': '默认', 'created': _now_str()}]
    update_config(lambda c: c.update({'apiKeys': keys, 'apiKey': old}))
    return keys


def save_keys(keys):
    """写回 Key 列表；apiKey 字段保持 = 第一个启用 Key（兼容旧字段）"""
    def fn(c):
        c['apiKeys'] = keys
        for k in keys:
            if k.get('enabled'):
                c['apiKey'] = k['key']
                break
        else:
            if keys:
                c['apiKey'] = keys[0]['key']
    update_config(fn)
    return keys


def list_keys():
    return load_keys()


def add_key(key, note=''):
    """新增 Key（默认启用）。返回 (ok, msg)"""
    key = (key or '').strip()
    if not key:
        return False, 'Key 不能为空'
    keys = load_keys()
    if any(k['key'] == key for k in keys):
        return False, '该 Key 已存在'
    keys.append({'key': key, 'enabled': True, 'note': note or '手动添加', 'created': _now_str()})
    save_keys(keys)
    return True, '已添加 Key'


def delete_key(key):
    """删除指定 Key。返回 (ok, msg)；至少保留一个启用 Key。"""
    keys = load_keys()
    before = len(keys)
    keys = [k for k in keys if k['key'] != key]
    if len(keys) == before:
        return False, '未找到该 Key'
    if not any(k.get('enabled') for k in keys):
        return False, '不能删除最后一个启用的 Key（至少保留一个可用 Key）'
    save_keys(keys)
    return True, '已删除 Key'


def set_key_enabled(key, enabled):
    """启用/禁用指定 Key。返回 (ok, msg)。"""
    keys = load_keys()
    for k in keys:
        if k['key'] == key:
            if bool(k.get('enabled')) == bool(enabled):
                return True, '已是该状态'
            if not enabled and sum(1 for x in keys if x.get('enabled')) <= 1:
                return False, '至少保留一个启用的 Key'
            k['enabled'] = bool(enabled)
            save_keys(keys)
            return True, ('已禁用该 Key' if not enabled else '已启用该 Key')
    return False, '未找到该 Key'


# ---------------- 模型管理 ----------------

def model_path(name):
    return os.path.join(MODELS_DIR, 'ggml-%s.bin' % name)


def installed_models():
    """已下载模型名列表（按文件修改时间新→旧）"""
    if not os.path.isdir(MODELS_DIR):
        return []
    out = []
    for f in os.listdir(MODELS_DIR):
        m = re.match(r'^ggml-(.+)\.bin$', f)
        if m:
            p = os.path.join(MODELS_DIR, f)
            out.append((m.group(1), os.path.getmtime(p)))
    out.sort(key=lambda x: -x[1])
    return [n for n, _ in out]


def get_current_model():
    cfg = load_config()
    cur = cfg.get('currentModel')
    if cur and os.path.exists(model_path(cur)):
        return cur
    models = installed_models()
    if models:
        return models[0]
    return None


def delete_model(name):
    """删除模型文件；若删的是当前模型，自动切换到其他模型"""
    p = model_path(name)
    if not os.path.exists(p):
        return False, '模型不存在'
    if name == get_current_model():
        others = [m for m in installed_models() if m != name]
        if others:
            switch_model(others[0])
        else:
            update_config(lambda c: c.update({'currentModel': None}))
    try:
        os.remove(p)
    except Exception as e:
        return False, str(e)
    return True, '已删除 %s' % name


def switch_model(name, wait=True):
    """热切换模型：写配置；若引擎(8301)在运行（无论哪个进程拉起），调 /load 热加载"""
    if not os.path.exists(model_path(name)):
        return False, '模型 %s 未下载' % name
    update_config(lambda c: c.update({'currentModel': name}))
    # 引擎未运行时无需热加载，网关下次请求/启动时按配置加载
    if not port_open(ENGINE_PORT, 0.3):
        return True, '已设定默认模型 %s（引擎未运行，下次启动生效）' % name
    try:
        req = urllib.request.Request(
            'http://127.0.0.1:%d/load' % ENGINE_PORT,
            data=json.dumps({'model_path': model_path(name)}).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=30)
        return True, '已切换到模型 %s' % name
    except Exception as e:
        return False, '切换失败：%s' % e


def download_model(name, on_progress=None):
    """后台线程下载模型（供 server-manager 调用）。
    on_progress(downloaded, total, status, msg)"""
    if name not in MODELS_CATALOG:
        return
    url = MODELS_CATALOG[name][1]
    os.makedirs(MODELS_DIR, exist_ok=True)
    dest = model_path(name)
    tmp = dest + '.part'

    def report(d, t, st, msg):
        _downloads[name] = {'progress': int(d * 100 / t) if t else 0, 'status': st, 'msg': msg}
        if on_progress:
            try:
                on_progress(d, t, st, msg)
            except Exception:
                pass

    report(0, 0, 'downloading', '连接下载源...')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get('Content-Length') or 0)
            d = 0
            t0 = time.time()
            with open(tmp, 'wb') as f:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    d += len(chunk)
                    if total and int(d * 100 / total) != int((d - len(chunk)) * 100 / total):
                        speed = d / max(time.time() - t0, 0.1) / 1024 / 1024
                        report(d, total, 'downloading',
                               '%d%%（%.1fMB，速度 %.2fMB/s）' % (d * 100 / total, d / 1024 / 1024, speed))
        os.replace(tmp, dest)
        report(1, 1, 'done', '下载完成 %s（%s）' % (name, MODELS_CATALOG[name][0]))
        # 首个模型下载完成且未设定默认模型时，自动设为当前模型
        if not load_config().get('currentModel'):
            update_config(lambda c: c.update({'currentModel': name}))
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        report(0, 0, 'error', '下载失败：%s' % e)


def download_model_async(name):
    """启动后台下载线程；重复调用同一模型会被忽略"""
    if name in _downloads and _downloads[name]['status'] == 'downloading':
        return False
    if os.path.exists(model_path(name)):
        return False
    threading.Thread(target=download_model, args=(name,), daemon=True).start()
    return True


def download_status():
    return dict(_downloads)


# ---------------- whisper-server 引擎进程 ----------------

def _engine_running():
    """引擎可用 = 本进程拉起且存活，或 8301 端口已被监听（外部进程拉起）"""
    with _engine_lock:
        if _engine_proc is not None and _engine_proc.poll() is None:
            return True
    return port_open(ENGINE_PORT, 0.2)


def start_engine(on_log=lambda line: None):
    """拉起 whisper-server.exe 子进程；无模型时返回错误"""
    global _engine_proc
    with _engine_lock:
        if _engine_running():
            return True, '引擎已在运行'
        if not os.path.exists(WHISPER_EXE):
            return False, '未找到 %s' % WHISPER_EXE
        cur = get_current_model()
        if not cur:
            return False, '尚未下载任何模型，请先在 server-manager「转写服务」页下载'
        cmd = [WHISPER_EXE, '--host', '127.0.0.1', '--port', str(ENGINE_PORT),
               '-m', model_path(cur)]
        on_log('$ ' + ' '.join(cmd))
        try:
            _engine_proc = subprocess.Popen(
                [str(c) for c in cmd], cwd=os.path.dirname(WHISPER_EXE),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                creationflags=NO_WINDOW)
        except Exception as e:
            return False, '启动引擎失败：%s' % e
        threading.Thread(target=_read_engine_pipe, args=(on_log,), daemon=True).start()
    # 等端口就绪
    t0 = time.time()
    while time.time() - t0 < 60:
        if _engine_proc is not None and _engine_proc.poll() is not None:
            return False, '引擎进程已退出（退出码 %s）' % _engine_proc.returncode
        if port_open(ENGINE_PORT, 0.3):
            return True, '引擎已启动（端口 %d，模型 %s）' % (ENGINE_PORT, cur)
        time.sleep(0.4)
    return False, '引擎在 60 秒内未监听端口 %d' % ENGINE_PORT


def stop_engine():
    global _engine_proc
    with _engine_lock:
        if _engine_proc is None:
            return True
        proc = _engine_proc
        _engine_proc = None
    try:
        if proc.poll() is None:
            if IS_WIN:
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               capture_output=True, creationflags=NO_WINDOW)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        return True
    except Exception as e:
        return False


def ensure_engine(on_log=lambda line: None):
    """引擎未运行则拉起（按当前配置模型）"""
    if _engine_running():
        return True, 'ok'
    return start_engine(on_log)


def _read_engine_pipe(on_log):
    try:
        for line in _engine_proc.stdout:
            on_log(line.rstrip('\r\n'))
    except Exception:
        pass


def port_open(port, timeout=0.3):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex(('127.0.0.1', port)) == 0
    except OSError:
        return False


def lan_ips():
    """本机所有非回环 IPv4 列表（自动检测，随网卡/网络变化更新，供连接信息展示）。
    能上外网的出口 IP（通常是主用物理网卡）排在最前，其余（虚拟网卡等）随后。"""
    out = []
    # 1) 首选：UDP 出口 IP（能上外网的那张网卡，最常用）
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            if not ip.startswith('127.'):
                out.append(ip)
    except Exception:
        pass
    # 2) 其余本机 IPv4（去重、过滤回环/APIPA）
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if (ip not in out and not ip.startswith('127.')
                    and not ip.startswith('169.254.')):
                out.append(ip)
    except Exception:
        pass
    return out


def lan_ip():
    """主用局域网 IP（优先常见私网网段，无则回退 127.0.0.1）"""
    ips = lan_ips()
    for ip in ips:
        if ip.startswith('192.168.') or ip.startswith('10.') or ip.startswith('172.'):
            return ip
    return ips[0] if ips else '127.0.0.1'


# ---------------- HTTP 网关 ----------------

class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'TranscribeGW/1.0'

    # 日志重定向由 serve() 注入；staticmethod 防止 self.log_fn() 绑定实例
    log_fn = staticmethod(lambda line: None)

    def log_message(self, fmt, *args):
        try:
            self.log_fn('%s - %s' % (self.address_string(), fmt % args))
        except Exception:
            pass

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        # 关闭连接：避免 keep-alive 连接上残留未消费请求体被误读为下一个请求（如 '3ff5' 400）
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _auth_ok(self):
        key = self.headers.get('Authorization', '')
        if key.startswith('Bearer '):
            key = key[7:].strip()
        # 常数时间比较，防时序侧信道；遍历所有启用 Key
        target = hashlib.sha256(key.encode()).digest()
        for k in load_keys():
            if k.get('enabled') and hashlib.sha256((k['key'] or '').encode()).digest() == target:
                return True
        return False

    def _check_access(self):
        """访问控制：限时开放窗口内免 Key；永久开放 / 窗口外需 Key。
        返回 (ok, http_status, error_message)。"""
        cfg = load_config()
        sch = cfg.get('schedule') or {'mode': 'always'}
        mode = sch.get('mode', 'always')
        if mode != 'always' and is_open_now(cfg):
            # 限时开放窗口内：免 Key 放行
            return True, 200, ''
        if self._auth_ok():
            return True, 200, ''
        if mode == 'always':
            return False, 401, 'API Key 无效或缺失'
        return False, 403, '当前不在开放时段：限时开放窗口内免 Key，窗口外需有效 Key'

    # ---- GET ----
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ('/v1/models', '/models'):
            ok, status, msg = self._check_access()
            if not ok:
                self._send_json(status, {'error': {'message': msg}})
                return
            models = installed_models()
            self._send_json(200, {'object': 'list', 'data': [
                {'id': m, 'object': 'model', 'owned_by': 'whisper.cpp'} for m in models]})
            return
        if path == '/health':
            self._send_json(200, {'ok': True, 'engine': _engine_running(),
                                   'model': get_current_model(),
                                   'open': is_open_now()})
            return
        self._send_json(404, {'error': {'message': 'Not Found: %s' % path}})

    # ---- POST ----
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path not in ('/v1/audio/transcriptions', '/audio/transcriptions'):
            self._send_json(404, {'error': {'message': 'Not Found: %s' % path}})
            return
        ok, status, msg = self._check_access()
        if not ok:
            self._send_json(status, {'error': {'message': msg}})
            return

        # 读取请求体（支持 chunked 与 Content-Length 两种传输方式）
        try:
            te = (self.headers.get('Transfer-Encoding') or '').lower()
            if 'chunked' in te:
                body = self._read_chunked()
            else:
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
        except Exception:
            self._send_json(400, {'error': {'message': '请求体读取失败'}})
            return

        # 解析 multipart：提取 model / language 与音频字节
        model, language, audio = self._parse_multipart(body, self.headers.get('Content-Type', ''))
        if audio is None:
            self.log_fn('[警告] multipart 解析失败 CT=%r len=%d body_head=%r' % (
                self.headers.get('Content-Type', ''), len(body), body[:120]))
            self._send_json(400, {'error': {'message': '未找到音频文件字段 file'}})
            return
        if not audio:
            self._send_json(400, {'error': {'message': '音频内容为空'}})
            return

        # 引擎就绪（未运行则拉起）
        ok, msg = ensure_engine(on_log=self.log_fn)
        if not ok:
            self._send_json(503, {'error': {'message': msg}})
            return

        # model 字段热切换（不同于当前模型且已下载）
        cur = get_current_model()
        if model and model != cur:
            if os.path.exists(model_path(model)):
                ok, msg = switch_model(model)
                if not ok:
                    self.log_fn('[警告] %s' % msg)
            else:
                self.log_fn('[提示] 请求模型 %s 未下载，继续使用 %s' % (model, cur))

        # 重新组装 multipart 转发给引擎
        fwd = self._build_multipart(cur, language, audio)
        try:
            req = urllib.request.Request(
                'http://127.0.0.1:%d/inference' % ENGINE_PORT,
                data=fwd, headers={'Content-Type': 'multipart/form-data; boundary=litemeetgw'})
            with urllib.request.urlopen(req, timeout=600) as resp:
                rb = resp.read()
                sc = resp.status
        except urllib.error.HTTPError as e:
            rb = e.read()
            sc = e.code
        except Exception as e:
            self._send_json(502, {'error': {'message': '转写引擎访问失败：%s' % e}})
            return

        # 引擎 JSON 转为 OpenAI 响应格式
        try:
            data = json.loads(rb.decode('utf-8', 'replace'))
            text = data.get('text', '')
        except Exception:
            text = rb.decode('utf-8', 'replace')
        if sc // 100 != 2:
            self._send_json(sc, {'error': {'message': '转写失败：' + text[:300]}})
            return
        self._send_json(200, {'text': text})

    # ---- multipart 工具 ----
    def _read_chunked(self):
        """读取 HTTP/1.1 chunked 请求体（Java HttpClient 等客户端可能使用）"""
        data = b''
        while True:
            line = self.rfile.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                size = int(line.split(b';')[0].strip(), 16)
            except ValueError:
                break
            if size == 0:
                # 消费 trailer 到空行
                while True:
                    t = self.rfile.readline()
                    if t in (b'\r\n', b'\n', b''):
                        break
                break
            data += self.rfile.read(size)
            self.rfile.read(2)  # 块尾 \r\n
        return data

    def _parse_multipart(self, body, ctype):
        """简易 multipart 解析：返回 (model, language, audio_bytes)"""
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if not m:
            return None, None, None
        boundary = ('--' + m.group(1)).encode()
        parts = body.split(boundary)
        model, language, audio = None, None, None
        for part in parts:
            if not part or part in (b'--', b'--\r\n', b'\r\n', b''):
                continue
            seg = part
            if seg.startswith(b'\r\n'):
                seg = seg[2:]
            if seg.endswith(b'\r\n'):
                seg = seg[:-2]
            if seg.startswith(b'--'):
                continue
            # 分离头部与内容
            if b'\r\n\r\n' in seg:
                head, content = seg.split(b'\r\n\r\n', 1)
            elif b'\n\n' in seg:
                head, content = seg.split(b'\n\n', 1)
            else:
                continue
            try:
                head_text = head.decode('utf-8', 'replace')
            except Exception:
                continue
            name_m = re.search(r'name="([^"]+)"', head_text)
            if not name_m:
                continue
            name = name_m.group(1)
            if name == 'file':
                audio = content
            elif name == 'model':
                model = content.decode('utf-8', 'replace').strip()
            elif name == 'language':
                language = content.decode('utf-8', 'replace').strip()
        return model, language, audio

    @staticmethod
    def _build_multipart(model, language, audio):
        out = []
        out.append(b'--litemeetgw\r\nContent-Disposition: form-data; name="model"\r\n\r\n'
                   + (model or 'base').encode() + b'\r\n')
        if language and language != 'auto':
            out.append(b'--litemeetgw\r\nContent-Disposition: form-data; name="language"\r\n\r\n'
                       + language.encode() + b'\r\n')
        out.append(b'--litemeetgw\r\nContent-Disposition: form-data; name="file"; filename="chunk.wav"\r\n'
                   b'Content-Type: application/octet-stream\r\n\r\n' + audio + b'\r\n')
        out.append(b'--litemeetgw--\r\n')
        return b''.join(out)


def serve(port=GATE_PORT, log_fn=lambda line: None):
    """启动网关（阻塞）；server-manager 以子进程方式运行本文件时走这里"""
    # staticmethod：避免 self.log_fn() 把实例绑定为第一个参数
    GatewayHandler.log_fn = staticmethod(log_fn)
    # 预拉起转写引擎：首次加载模型需数秒，提前就绪避免首个转写请求长时间等待
    threading.Thread(target=lambda: start_engine(on_log=log_fn), daemon=True).start()
    httpd = ThreadingHTTPServer(('0.0.0.0', port), GatewayHandler)
    log_fn('[网关] 语音转写服务已启动: http://0.0.0.0:%d/v1' % port)
    log_fn('[网关] 局域网地址: http://%s:%d/v1' % (lan_ip(), port))
    models = installed_models()
    log_fn('[网关] 已安装模型: %s' % (', '.join(models) if models else '（无，请先下载）'))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_engine()
        httpd.server_close()


# ---------------- 本地管理命令（供 server-manager 以 -c 参数调用）----------------

def cli():
    args = sys.argv[1:]
    if not args or args[0] == 'serve':
        # 前台运行网关（输出到 stdout，供 server-manager 捕获日志）
        serve(log_fn=lambda line: print(line, flush=True))
        return
    cmd = args[0]
    if cmd == 'status':
        print(json.dumps({
            'gateway': port_open(GATE_PORT),
            'engine': _engine_running(),
            'model': get_current_model(),
            'open': is_open_now(),
            'models': installed_models(),
        }, ensure_ascii=False))
    elif cmd == 'models':
        print(json.dumps({'installed': installed_models(),
                          'catalog': {k: v[0] for k, v in MODELS_CATALOG.items()},
                          'downloads': download_status()}, ensure_ascii=False))
    elif cmd == 'regen-key':
        cfg = update_config(lambda c: c.update({'apiKey': 'sk-' + secrets.token_hex(16)}))
        print(cfg['apiKey'])
    elif cmd == 'schedule':
        # schedule "always" | "daily 09:00 18:00" | "range 2026-08-29 10:00 2026-09-05 22:00"
        parts = args[1:]
        mode = parts[0] if parts else 'always'
        sch = {'mode': mode}
        if mode == 'daily' and len(parts) >= 3:
            sch = {'mode': 'daily', 'start': parts[1], 'end': parts[2]}
        elif mode == 'range' and len(parts) >= 3:
            sch = {'mode': 'range', 'start': parts[1] + ' ' + parts[2], 'end': parts[3] + ' ' + parts[4]}
        update_config(lambda c: c.update({'schedule': sch}))
        print('ok')
    elif cmd == 'delete':
        ok, msg = delete_model(args[1])
        print(('ok: ' if ok else 'err: ') + msg)
    elif cmd == 'list-keys':
        print(json.dumps(load_keys(), ensure_ascii=False))
    elif cmd == 'add-key':
        # add-key <key> [note]
        ok, msg = add_key(args[1], args[2] if len(args) > 2 else '手动添加')
        print(('ok: ' if ok else 'err: ') + msg)
    elif cmd == 'del-key':
        ok, msg = delete_key(args[1])
        print(('ok: ' if ok else 'err: ') + msg)
    elif cmd == 'toggle-key':
        # toggle-key <key> [on|off]
        enabled = args[2].lower() != 'off' if len(args) > 2 else True
        ok, msg = set_key_enabled(args[1], enabled)
        print(('ok: ' if ok else 'err: ') + msg)
    else:
        print('用法: server.py [serve|status|models|list-keys|add-key <key> [note]|'
              'del-key <key>|toggle-key <key> [on|off]|schedule ...|delete <name>]')


if __name__ == '__main__':
    cli()
