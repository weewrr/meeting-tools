# -*- coding: utf-8 -*-
"""环境管理：扫描已安装环境、下载安装到 envs/ 目录、手动设置路径，配置持久化"""
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.request
import zipfile

IS_WIN = os.name == 'nt'
NO_WINDOW = 0x08000000 if IS_WIN else 0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
ENVS_DIR = os.path.join(BASE_DIR, 'envs')
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) server-manager/1.0'


class Component:
    def __init__(self, cid, name, exe_names, version_args, version_re, note=''):
        self.id = cid
        self.name = name
        self.exe_names = exe_names        # 用于 PATH 扫描的可执行名
        self.version_args = version_args  # 取版本的参数
        self.version_re = version_re      # 版本号正则
        self.note = note


COMPONENTS = [
    Component('jdk', 'JDK (Java 21)', ['java.exe'], ['-version'], r'version "([^"]+)"',
              '运行后端与前端服务器，需要 21+'),
    Component('maven', 'Maven', ['mvn.cmd', 'mvn.bat', 'mvn'], ['-v'], r'Apache Maven (\S+)',
              '构建后端 jar'),
    Component('node', 'Node.js', ['node.exe'], ['-v'], r'v?([\d.]+)',
              '构建前端（npm/vite）'),
    Component('mysql', 'MySQL', ['mysqld.exe'], ['--version'], r'Ver ([\d.]+)',
              '数据库服务，端口 3306'),
    Component('redis', 'Redis', ['redis-server.exe'], ['--version'], r'v=([\d.]+)',
              '缓存服务，端口 6379'),
    Component('livekit', 'LiveKit Server', ['livekit-server.exe'], ['--version'], r'(\d+\.\d+\.\d+)',
              'SFU 媒体服务器，端口 7880'),
    Component('whisper', 'Whisper Server (whisper.cpp)', ['whisper-server.exe'], ['--help'],
              r'v?(\d+\.\d+\.\d+)', '语音转写引擎，端口 8301'),
]

# 默认下载地址（按顺序尝试，均失败可在设置里填自定义地址）
DOWNLOAD_URLS = {
    'jdk': [
        'https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jdk/hotspot/normal/eclipse',
    ],
    'maven': [
        'https://archive.apache.org/dist/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.zip',
        'https://mirrors.tuna.tsinghua.edu.cn/apache/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.zip',
    ],
    'node': [
        'https://cdn.npmmirror.com/binaries/node/v22.14.0/node-v22.14.0-win-x64.zip',
        'https://nodejs.org/dist/v22.14.0/node-v22.14.0-win-x64.zip',
    ],
    'mysql': [
        'https://mirrors.sjtug.sjtu.edu.cn/mysql/downloads/MySQL-8.0/mysql-8.0.39-winx64.zip',
        'https://mirrors.tuna.tsinghua.edu.cn/mysql/downloads/MySQL-8.0/mysql-8.0.39-winx64.zip',
        'https://dev.mysql.com/get/Downloads/MySQL-8.0/mysql-8.0.39-winx64.zip',
    ],
    'redis': [
        'https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip',
    ],
    'livekit': [],  # 动态获取 GitHub 最新版
    'whisper': [
        # whisper.cpp 官方 Windows x64 预编译包（内含 Release\whisper-server.exe）
        'https://github.com/ggerganov/whisper.cpp/releases/download/v1.8.3/whisper-bin-x64.zip',
        'https://ghfast.top/https://github.com/ggerganov/whisper.cpp/releases/download/v1.8.3/whisper-bin-x64.zip',
        'https://gh-proxy.com/https://github.com/ggerganov/whisper.cpp/releases/download/v1.8.3/whisper-bin-x64.zip',
    ],
}

# 下载解压后用于定位的 exe（rglob 搜索）
LOCATE_FILES = {
    'jdk': 'java.exe',
    'maven': 'mvn.cmd',
    'node': 'node.exe',
    'mysql': 'mysqld.exe',
    'redis': 'redis-server.exe',
    'livekit': 'livekit-server.exe',
    'whisper': 'whisper-server.exe',
}


def _run_version(path, args):
    try:
        p = subprocess.run([path] + list(args), capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=20,
                           creationflags=NO_WINDOW)
        return (p.stderr or '') + (p.stdout or '')
    except Exception:
        return ''


class EnvManager:
    def __init__(self, on_event=None):
        self.on_event = on_event or (lambda *a: None)
        self.config = self._load_config()
        self.results = {}   # 扫描结果 {cid: {...}}

    # ---------- 配置 ----------
    def _load_config(self):
        cfg = {'project_root': os.path.dirname(BASE_DIR), 'paths': {}, 'urls': {}}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    cfg.update(json.load(f))
                cfg.setdefault('paths', {})
                cfg.setdefault('urls', {})
            except Exception:
                pass
        return cfg

    def save_config(self):
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @property
    def project_root(self):
        return self.config.get('project_root', os.path.dirname(BASE_DIR))

    def set_project_root(self, root):
        self.config['project_root'] = os.path.abspath(root)
        self.save_config()

    def set_path(self, cid, path):
        if path:
            self.config['paths'][cid] = os.path.abspath(path)
        else:
            self.config['paths'].pop(cid, None)
        self.save_config()
        self.scan_async()

    def set_url(self, cid, url):
        if url and url.strip():
            self.config['urls'][cid] = url.strip()
        else:
            self.config['urls'].pop(cid, None)
        self.save_config()

    # ---------- 删除（仅限本程序下载安装的环境） ----------
    def is_managed(self, cid):
        """路径是否位于 server-manager\\envs 下（即通过环境管理下载安装）"""
        p = self.config.get('paths', {}).get(cid)
        if not p:
            return False
        try:
            envs_root = os.path.realpath(ENVS_DIR)
            return os.path.realpath(p).startswith(envs_root + os.sep)
        except Exception:
            return False

    def delete(self, cid):
        """删除通过环境管理下载的环境；返回 (成功, 消息)"""
        if not self.is_managed(cid):
            return False, '只能删除通过「环境管理」下载安装的环境'
        dest = os.path.join(ENVS_DIR, cid)
        if not os.path.isdir(dest):
            self.config['paths'].pop(cid, None)
            self.save_config()
            return True, '环境已不存在，配置已清除'
        try:
            shutil.rmtree(dest)
        except PermissionError:
            return False, '删除失败：文件被占用，请先停止相关服务后重试'
        except OSError as e:
            return False, f'删除失败：{e}'
        if os.path.isdir(dest):
            return False, '删除失败：部分文件被占用，请先停止相关服务后重试'
        self.config['paths'].pop(cid, None)
        self.save_config()
        self.scan_async()
        return True, '环境已删除'

    # ---------- 扫描 ----------
    def _find_in_paths(self, exe_names):
        for name in exe_names:
            p = shutil.which(name)
            if p:
                return p
        return None

    def scan_async(self):
        threading.Thread(target=self.scan, daemon=True).start()

    def scan(self):
        for comp in COMPONENTS:
            info = {'name': comp.name, 'note': comp.note, 'found': False,
                    'path': '', 'version': '', 'source': ''}
            # 1) 用户配置的路径优先
            p = self.config['paths'].get(comp.id)
            if p and os.path.exists(p):
                info.update(found=True, path=p, source='自定义路径')
            else:
                # 2) PATH 扫描
                p = self._find_in_paths(comp.exe_names)
                if p:
                    info.update(found=True, path=p, source='系统 PATH')
            if info['found']:
                out = _run_version(info['path'], comp.version_args)
                m = re.search(comp.version_re, out)
                if m:
                    info['version'] = m.group(1)
                elif out.strip():
                    info['version'] = '已安装'
            self.results[comp.id] = info
        self.on_event('env_scan', None, self.results)
        return self.results

    # ---------- 供 servers.py 使用的环境路径 ----------
    def get_server_env(self):
        """把扫描结果映射为 servers.ServiceManager 需要的 env 字典"""
        def pick(cid, extra_check=None):
            info = self.results.get(cid) or {}
            return info.get('path') or None

        env = {
            'java': pick('jdk'),
            'mvn': pick('maven'),
            # MySQL 需要 mysqld（服务端）执行初始化与启动；用户可能配置的是 mysql.exe（客户端），
            # 因此优先从同目录取 mysqld.exe
            'mysqld': self._sibling(pick('mysql'), 'mysqld.exe') or pick('mysql'),
            'mysql': self._sibling(pick('mysql'), 'mysql.exe'),
            'redis': pick('redis'),
            'livekit': pick('livekit'),
            'node': pick('node'),
            'whisper': pick('whisper'),
        }
        # node 存在则 npm 在同目录
        if env['node']:
            env['npm'] = self._sibling(env['node'], 'npm.cmd')
        else:
            env['npm'] = shutil.which('npm.cmd') or shutil.which('npm')
        # maven 未配置时回退 PATH
        if not env['mvn']:
            env['mvn'] = shutil.which('mvn.cmd') or shutil.which('mvn')
        # mysql 客户端
        if not env['mysql']:
            env['mysql'] = shutil.which('mysql.exe') or shutil.which('mysql')
        return env

    @staticmethod
    def _sibling(path, filename):
        if not path:
            return None
        cand = os.path.join(os.path.dirname(path), filename)
        return cand if os.path.exists(cand) else None

    # ---------- 下载安装 ----------
    def _livekit_urls(self):
        """从 GitHub API 获取最新版 LiveKit Windows 下载地址"""
        api = 'https://api.github.com/repos/livekit/livekit/releases/latest'
        try:
            req = urllib.request.Request(api, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.load(r)
            for asset in data.get('assets', []):
                name = asset.get('name', '')
                if 'windows_amd64' in name and name.endswith('.zip'):
                    return [asset['browser_download_url']]
        except Exception:
            pass
        return ['https://github.com/livekit/livekit/releases/download/v1.13.6/livekit_1.13.6_windows_amd64.zip']

    def download_async(self, cid, progress_cb=None):
        threading.Thread(target=self._download_thread, args=(cid, progress_cb), daemon=True).start()

    def _download_thread(self, cid, progress_cb):
        comp = next((c for c in COMPONENTS if c.id == cid), None)
        if not comp:
            return
        emit = self.on_event
        try:
            emit('dl_state', cid, ('downloading', '正在获取下载地址...'))
            urls = [self.config['urls'].get(cid)] if self.config.get('urls', {}).get(cid) else []
            if cid == 'livekit':
                urls += self._livekit_urls()
            else:
                urls += DOWNLOAD_URLS.get(cid, [])

            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            zip_path = None
            last_err = None
            for url in urls:
                try:
                    emit('dl_state', cid, ('downloading', f'开始下载: {url}'))
                    zip_path = self._download_file(url, os.path.join(DOWNLOAD_DIR, cid + '.zip'),
                                                   cid, progress_cb)
                    break
                except Exception as e:
                    last_err = e
                    emit('dl_state', cid, ('downloading', f'该地址失败: {e}，尝试下一个...'))
            if not zip_path:
                raise RuntimeError(f'所有下载地址均失败: {last_err}')

            emit('dl_state', cid, ('extracting', '下载完成，正在解压...'))
            dest = self._extract(cid, zip_path)
            emit('dl_state', cid, ('extracting', '正在定位程序文件...'))
            exe = self._locate(cid, dest)
            self.config['paths'][cid] = exe
            self.save_config()
            self.scan()
            emit('dl_state', cid, ('done', f'安装完成: {exe}'))
        except Exception as e:
            emit('dl_state', cid, ('error', str(e)))

    def _download_file(self, url, dest, cid, progress_cb):
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=30) as resp, open(dest, 'wb') as f:
            total = int(resp.headers.get('Content-Length') or 0)
            loaded = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                loaded += len(chunk)
                if progress_cb:
                    progress_cb(cid, loaded, total)
                self.on_event('dl_progress', cid, (loaded, total))
        return dest

    def _extract(self, cid, zip_path):
        dest = os.path.join(ENVS_DIR, cid)
        if os.path.exists(dest):
            shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            if bad:
                raise RuntimeError(f'压缩包损坏: {bad}')
            z.extractall(dest)
        try:
            os.remove(zip_path)
        except Exception:
            pass
        return dest

    def _locate(self, cid, dest):
        fname = LOCATE_FILES[cid]
        for root, dirs, files in os.walk(dest):
            if fname in files:
                return os.path.join(root, fname)
        raise RuntimeError(f'解压后未找到 {fname}')
