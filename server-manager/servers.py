# -*- coding: utf-8 -*-
"""服务进程管理：前端 / 后端 / LiveKit / MySQL / Redis 的启动、停止、状态、日志"""
import os
import socket
import subprocess
import sys
import threading
import time
from collections import deque

IS_WIN = os.name == 'nt'
NO_WINDOW = 0x08000000 if IS_WIN else 0


def port_open(port, timeout=0.2):
    """检测本机端口是否已被监听（无论是否本程序启动）；
    Vite 等开发服务器绑定 IPv6 ::1，需 IPv4/IPv6 双栈检测"""
    for host in ('127.0.0.1', '::1'):
        try:
            with socket.socket(socket.AF_INET if host != '::1' else socket.AF_INET6,
                               socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                if s.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


class Service:
    def __init__(self, sid, name, port, desc=''):
        self.sid = sid
        self.name = name
        self.port = port
        self.desc = desc
        self.proc = None           # subprocess.Popen
        self.state = 'stopped'     # stopped / starting / running / stopping / error
        self.external = False      # 端口被外部程序占用
        self.logs = deque(maxlen=3000)
        self._lock = threading.Lock()

    def log(self, line):
        with self._lock:
            self.logs.append(line)
        return line

    def log_lines(self):
        with self._lock:
            return list(self.logs)

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None


class ServiceManager:
    """统一管理 5 个服务；所有事件通过回调 on_event(kind, sid, data) 抛给 UI"""

    def __init__(self, project_root, on_event=None):
        self.project_root = os.path.abspath(project_root)
        self.on_event = on_event or (lambda *a: None)
        self.env = {}  # 环境可执行文件路径，由外部(env_manager)刷新
        self.services = {
            'mysql':    Service('mysql', 'MySQL', 3306, '数据库（会议记录持久化）'),
            'redis':    Service('redis', 'Redis', 6379, '缓存（会议号 TTL 释放）'),
            'livekit':  Service('livekit', 'LiveKit', 7880, 'SFU 媒体服务器（音视频转发）'),
            'backend':  Service('backend', '后端 API', 5678, 'Spring Boot (HTTPS 5679)'),
            'frontend': Service('frontend', '前端网站', 5173, 'Vite 开发服务器（热更新，代理 API 到后端）'),
            'transcribe': Service('transcribe', '转写服务', 8300, '语音转写网关（whisper.cpp，OpenAI 兼容 API）'),
        }

    # ---------- 工具 ----------
    def emit(self, kind, sid, data=None):
        try:
            self.on_event(kind, sid, data)
        except Exception:
            pass

    def emit_log(self, svc, line):
        line = line.rstrip('\r\n')
        if line:
            svc.log(line)
            self.emit('log', svc.sid, line)

    def resolve(self, key):
        """获取环境路径；未配置时回退到 PATH 中的命令名"""
        fallback = {
            'java': 'java', 'mvn': None, 'npm': None,
            'mysqld': None, 'mysql': None, 'redis': None, 'livekit': None,
        }
        return self.env.get(key) or fallback.get(key)

    def _build_env(self):
        """为子进程构造环境变量：下载的 JDK/Node 不在系统 PATH，
        需补 JAVA_HOME（Maven 依赖）与 PATH（npm 的 .bin 脚本依赖）"""
        env = os.environ.copy()
        java = self.env.get('java')
        if java and os.path.exists(java):
            bindir = os.path.dirname(java)
            home = os.path.dirname(bindir)
            # 校验 home 确实是 JDK 根目录（排除 Oracle javapath 这类 shim）
            if os.path.exists(os.path.join(home, 'bin', 'java.exe')) and \
                    os.path.abspath(os.path.join(home, 'bin', 'java.exe')) == os.path.abspath(java):
                env['JAVA_HOME'] = home
                env['PATH'] = bindir + os.pathsep + env.get('PATH', '')
            else:
                env['PATH'] = bindir + os.pathsep + env.get('PATH', '')
        node = self.env.get('node')
        if node and os.path.exists(node):
            env['PATH'] = os.path.dirname(node) + os.pathsep + env.get('PATH', '')
        return env

    # ---------- 启动 ----------
    def start(self, sid):
        svc = self.services.get(sid)
        if not svc:
            return
        if svc.alive or (port_open(svc.port) and svc.state == 'running'):
            return
        t = threading.Thread(target=self._start_thread, args=(svc,), daemon=True)
        t.start()

    def _start_thread(self, svc):
        try:
            self._start_impl(svc)
        except Exception as e:
            svc.state = 'error'
            self.emit_log(svc, f'[错误] 启动失败: {e}')
            self.emit('status', svc.sid)

    def _start_impl(self, svc):
        # 端口已被外部程序占用：直接视为运行中
        if port_open(svc.port):
            svc.external = True
            svc.state = 'running'
            self.emit_log(svc, f'[提示] 端口 {svc.port} 已被占用（可能服务已在运行），跳过启动')
            self.emit('status', svc.sid)
            return
        svc.external = False
        svc.state = 'starting'
        self.emit('status', svc.sid)

        if svc.sid == 'mysql':
            self._start_mysql(svc)
        elif svc.sid == 'redis':
            self._start_redis(svc)
        elif svc.sid == 'livekit':
            self._start_livekit(svc)
        elif svc.sid == 'backend':
            self._start_backend(svc)
        elif svc.sid == 'frontend':
            self._start_frontend(svc)
        elif svc.sid == 'transcribe':
            self._start_transcribe(svc)

    def _spawn(self, svc, cmd, cwd=None, env=None):
        self.emit_log(svc, '$ ' + ' '.join(str(c) for c in cmd))
        svc.proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=cwd or self.project_root,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            creationflags=NO_WINDOW,
            env=env,
        )
        threading.Thread(target=self._read_pipe, args=(svc,), daemon=True).start()
        self._wait_port(svc)

    def _read_pipe(self, svc):
        try:
            for line in svc.proc.stdout:
                self.emit_log(svc, line)
        except Exception:
            pass

    def _wait_port(self, svc, timeout=90):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if svc.proc is not None and svc.proc.poll() is not None:
                code = svc.proc.returncode
                svc.state = 'error'
                self.emit_log(svc, f'[错误] 进程已退出（退出码 {code}）')
                self.emit('status', svc.sid)
                return False
            if port_open(svc.port, 0.3):
                svc.state = 'running'
                self.emit_log(svc, f'[成功] {svc.name} 已启动，端口 {svc.port}')
                self.emit('status', svc.sid)
                return True
            time.sleep(0.4)
        svc.state = 'error'
        self.emit_log(svc, f'[超时] {svc.name} 在 {timeout} 秒内未监听端口 {svc.port}')
        self.emit('status', svc.sid)
        return False

    # ---------- 各服务 ----------
    def _start_mysql(self, svc):
        mysqld = self.resolve('mysqld')
        if not mysqld:
            svc.state = 'error'
            self.emit_log(svc, '[错误] 未找到 mysqld，请到「环境管理」安装或设置 MySQL')
            self.emit('status', svc.sid)
            return
        datadir = os.path.join(self.project_root, 'server-manager', 'data', 'mysql')
        os.makedirs(datadir, exist_ok=True)
        first_init = not os.path.exists(os.path.join(datadir, 'mysql'))

        if first_init:
            self.emit_log(svc, '[初始化] 首次使用 MySQL，正在初始化数据目录（约 10-30 秒）...')
            r = subprocess.run(
                [mysqld, '--initialize-insecure', f'--datadir={datadir}'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                creationflags=NO_WINDOW,
            )
            if r.returncode != 0:
                svc.state = 'error'
                for ln in ((r.stderr or '') + (r.stdout or '')).splitlines():
                    if ln.strip():
                        self.emit_log(svc, ln)
                self.emit_log(svc, '[错误] MySQL 数据目录初始化失败')
                self.emit('status', svc.sid)
                return

        self._spawn(svc, [mysqld, '--console', f'--datadir={datadir}', '--port=3306'], cwd=self.project_root)
        # 首次初始化后设置 root 密码为 root（与 application.yml 一致）
        if first_init and svc.state == 'running':
            mysql = self.env.get('mysql')
            if mysql and os.path.exists(mysql):
                time.sleep(1.0)
                r = subprocess.run(
                    [mysql, '-u', 'root', '--skip-password', '-e',
                     "ALTER USER 'root'@'localhost' IDENTIFIED BY 'root'; FLUSH PRIVILEGES;"],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    creationflags=NO_WINDOW,
                )
                if r.returncode == 0:
                    self.emit_log(svc, '[初始化] root 密码已设置为 root')
                else:
                    self.emit_log(svc, '[警告] 设置 root 密码失败，可手动执行: '
                                       "ALTER USER 'root'@'localhost' IDENTIFIED BY 'root';")
        # 确保数据库存在（幂等）；表由后端启动时自动创建（CREATE TABLE IF NOT EXISTS）
        if svc.state == 'running':
            mysql = self.env.get('mysql')
            if mysql and os.path.exists(mysql):
                time.sleep(1.0)
                r = subprocess.run(
                    [mysql, '-uroot', '-proot', '-h127.0.0.1', '-P3306', '-e',
                     "CREATE DATABASE IF NOT EXISTS litemeet "
                     "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    creationflags=NO_WINDOW,
                )
                if r.returncode == 0:
                    self.emit_log(svc, '[就绪] 数据库 litemeet 已就绪（表由后端自动创建）')
                else:
                    self.emit_log(svc, '[提示] 数据库 litemeet 将由后端自动创建（需 root 有 CREATE 权限）')

    def _start_redis(self, svc):
        redis = self.resolve('redis')
        if not redis:
            svc.state = 'error'
            self.emit_log(svc, '[错误] 未找到 redis-server，请到「环境管理」安装或设置 Redis')
            self.emit('status', svc.sid)
            return
        self._spawn(svc, [redis, '--port', '6379'])

    def _start_livekit(self, svc):
        livekit = self.resolve('livekit')
        if not livekit:
            svc.state = 'error'
            self.emit_log(svc, '[错误] 未找到 livekit-server，请到「环境管理」安装或设置 LiveKit')
            self.emit('status', svc.sid)
            return
        cfg = os.path.join(self.project_root, 'livekit', 'livekit.yaml')
        self._spawn(svc, [livekit, '--config', cfg])

    def _backend_needs_rebuild(self, jar):
        """源码/配置比 jar 新（或 jar 缺失）时需要重新构建"""
        if not os.path.exists(jar):
            return True
        jar_mtime = os.path.getmtime(jar)
        src_root = os.path.join(self.project_root, 'backend', 'src')
        pom = os.path.join(self.project_root, 'backend', 'pom.xml')
        for p in (pom,):
            if os.path.exists(p) and os.path.getmtime(p) > jar_mtime:
                return True
        for root, _dirs, files in os.walk(src_root):
            for f in files:
                if f.endswith('.java') or f.endswith('.xml'):
                    p = os.path.join(root, f)
                    if os.path.getmtime(p) > jar_mtime:
                        return True
        return False

    def _mysql_ready(self, timeout=0.5):
        """MySQL 是否真正可用（端口通 + 能通过认证执行查询）。
        端口监听≠就绪：mysqld 监听后可能还需数秒才接受客户端连接（异常关闭后做 recovery 时尤其慢），
        后端在这间隙启动会连库失败直接退出。"""
        if not port_open(3306, timeout):
            return False
        mysql = self.resolve('mysql')
        if not mysql:
            return True  # 无客户端可验证时仅按端口判断
        try:
            r = subprocess.run(
                [mysql, '-uroot', '-proot', '-h127.0.0.1', '-P3306', '-e', 'SELECT 1'],
                capture_output=True, timeout=5, creationflags=NO_WINDOW)
            return r.returncode == 0
        except Exception:
            return True

    def _start_backend(self, svc):
        java = self.resolve('java')
        jar = os.path.join(self.project_root, 'backend', 'target', 'litemeet-backend.jar')
        if not java:
            svc.state = 'error'
            self.emit_log(svc, '[错误] 未找到 Java，请到「环境管理」安装或设置 JDK')
            self.emit('status', svc.sid)
            return
        # 后端强依赖 MySQL：未就绪时明确提示并中止，避免后端启动即崩（连库失败进程直接退出）
        if not self._mysql_ready(3):
            svc.state = 'error'
            self.emit_log(svc, '[错误] 未检测到可用的 MySQL（127.0.0.1:3306），后端无法连接数据库。')
            self.emit_log(svc, '[提示] 请先在首页启动 MySQL 服务（含首次初始化），再启动后端')
            self.emit('status', svc.sid)
            return
        if self._backend_needs_rebuild(jar):
            if not self._build_backend(svc):
                return
        self._spawn(svc, [java, '-jar', jar])

    def _start_frontend(self, svc):
        npm = self.resolve('npm')
        frontend_dir = os.path.join(self.project_root, 'frontend')
        if not npm:
            svc.state = 'error'
            self.emit_log(svc, '[错误] 未找到 npm，请到「环境管理」安装或设置 Node.js')
            self.emit('status', svc.sid)
            return
        if not os.path.exists(os.path.join(frontend_dir, 'node_modules')):
            if not self._install_frontend_deps(svc, npm, frontend_dir):
                return
        # Vite 开发服务器：改前端源码即时热更新，无需构建
        self.emit_log(svc, '[开发模式] 启动 Vite 开发服务器（热更新），访问 http://localhost:5173')
        self._spawn(svc, [npm, 'run', 'dev'], cwd=frontend_dir, env=self._build_env())

    def _install_frontend_deps(self, svc, npm, frontend_dir):
        self.emit_log(svc, '[依赖] 未找到 node_modules，开始安装前端依赖（npm install，首次较慢）...')
        r = subprocess.run(
            [npm, 'install'], cwd=frontend_dir, capture_output=True, text=True,
            encoding='utf-8', errors='replace', creationflags=NO_WINDOW,
            env=self._build_env(),
        )
        for ln in ((r.stdout or '') + (r.stderr or '')).splitlines()[-30:]:
            if ln.strip():
                self.emit_log(svc, ln)
        if r.returncode != 0:
            svc.state = 'error'
            self.emit_log(svc, '[错误] 前端依赖安装失败')
            self.emit('status', svc.sid)
            return False
        self.emit_log(svc, '[依赖] 前端依赖安装完成')
        return True

    def _start_transcribe(self, svc):
        """转写服务：运行 transcribe-server\\server.py 网关（python 子进程）"""
        py = sys.executable
        script = os.path.join(self.project_root, 'transcribe-server', 'server.py')
        if not os.path.exists(script):
            svc.state = 'error'
            self.emit_log(svc, '[错误] 未找到 transcribe-server/server.py')
            self.emit('status', svc.sid)
            return
        # 提示模型状态（无模型时服务仍可启动，转写请求会返回明确错误）
        try:
            sys.path.insert(0, os.path.dirname(script))
            if 'server' not in sys.modules:
                import server as tserver
            else:
                tserver = sys.modules['server']
            models = tserver.installed_models()
            cur = tserver.get_current_model()
            if models:
                self.emit_log(svc, '[提示] 已安装模型: %s（当前 %s）' % (', '.join(models), cur))
            else:
                self.emit_log(svc, '[提示] 尚未下载任何模型，请到「转写服务」页下载；服务可先启动')
        except Exception as e:
            self.emit_log(svc, '[警告] 读取模型状态失败: %s' % e)
        # 引擎 exe：环境管理配置了 whisper 组件则优先使用（否则网关回退 Release\whisper-server.exe）
        env = None
        whisper_exe = self.env.get('whisper')
        if whisper_exe and os.path.exists(whisper_exe):
            env = os.environ.copy()
            env['WHISPER_SERVER_EXE'] = whisper_exe
            self.emit_log(svc, '[提示] 使用环境管理下载的引擎: %s' % whisper_exe)
        self._spawn(svc, [py, script], cwd=os.path.dirname(script), env=env)

    # ---------- 构建 ----------
    def _build_backend(self, svc):
        mvn = self.resolve('mvn')
        if not mvn:
            svc.state = 'error'
            self.emit_log(svc, '[错误] 后端未构建且未找到 Maven，请到「环境管理」安装或设置 Maven')
            self.emit('status', svc.sid)
            return False
        self.emit_log(svc, '[构建] 未找到 litemeet-backend.jar，开始用 Maven 构建（首次较慢）...')
        r = subprocess.run(
            [mvn, '-f', 'backend/pom.xml', '-DskipTests', 'package'],
            cwd=self.project_root, capture_output=True, text=True,
            encoding='utf-8', errors='replace', creationflags=NO_WINDOW,
            env=self._build_env(),
        )
        out = (r.stdout or '') + (r.stderr or '')
        for ln in out.splitlines():
            ln = ln.strip()
            if ln.startswith(('[INFO]', '[ERROR]', '[WARNING]')) or 'BUILD' in ln:
                self.emit_log(svc, ln)
        if r.returncode != 0:
            svc.state = 'error'
            self.emit_log(svc, '[错误] 后端构建失败，请检查网络与 Maven 配置')
            self.emit('status', svc.sid)
            return False
        self.emit_log(svc, '[构建] 后端构建完成')
        return True

    # ---------- 停止 ----------
    def stop(self, sid):
        svc = self.services.get(sid)
        if not svc:
            return
        if not svc.alive:
            svc.state = 'stopped'
            self.emit('status', svc.sid)
            return
        threading.Thread(target=self._stop_thread, args=(svc,), daemon=True).start()

    def _stop_thread(self, svc):
        svc.state = 'stopping'
        self.emit('status', svc.sid)
        self.emit_log(svc, f'[停止] 正在停止 {svc.name}...')
        try:
            if svc.proc is not None and svc.proc.poll() is None:
                # 本程序启动的进程：杀整棵进程树，避免子进程残留占用端口
                pid = svc.proc.pid
                if IS_WIN:
                    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                                   capture_output=True, creationflags=NO_WINDOW)
                else:
                    svc.proc.terminate()
                try:
                    svc.proc.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    svc.proc.kill()
                    svc.proc.wait(timeout=5)
            else:
                # 外部运行（端口被占用，但非本程序启动）：按端口找到占用进程并终止
                self._stop_external(svc)
        except Exception as e:
            self.emit_log(svc, f'[警告] 停止进程异常: {e}')
        svc.proc = None
        svc.external = False
        svc.state = 'stopped'
        self.emit_log(svc, f'[停止] {svc.name} 已停止')
        self.emit('status', svc.sid)

    def _stop_external(self, svc):
        """停止外部占用端口的进程：MySQL 先尝试优雅关闭（避免强杀损坏数据），其余直接强杀"""
        pid = self._port_pid(svc.port)
        if not pid:
            self.emit_log(svc, '[提示] 未发现占用端口的进程（可能已停止）')
            return
        if svc.sid == 'mysql':
            mysql = self.env.get('mysql')
            if mysql and os.path.exists(mysql):
                admin = os.path.join(os.path.dirname(mysql), 'mysqladmin.exe')
                if os.path.exists(admin):
                    try:
                        r = subprocess.run([admin, '-uroot', '-proot', '-h127.0.0.1', 'shutdown'],
                                           capture_output=True, text=True, encoding='utf-8',
                                           errors='replace', creationflags=NO_WINDOW, timeout=10)
                        if r.returncode == 0:
                            self.emit_log(svc, f'[停止] 已优雅关闭外部 MySQL (PID {pid})')
                            return
                    except Exception:
                        pass
        if IS_WIN:
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                           capture_output=True, creationflags=NO_WINDOW)
        else:
            try:
                os.kill(pid, 9)
            except (OSError, ProcessLookupError):
                pass
        self.emit_log(svc, f'[停止] 已终止外部进程 (PID {pid})')

    def _port_pid(self, port):
        """返回监听该端口的进程 PID；未找到返回 None"""
        try:
            out = subprocess.run(['netstat', '-ano'], capture_output=True,
                                 text=True, encoding='utf-8', errors='replace',
                                 creationflags=NO_WINDOW, timeout=5).stdout
        except Exception:
            return None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0] == 'TCP':
                try:
                    local = parts[1]
                    if local.endswith(f':{port}') and parts[3] in ('LISTENING', 'LISTEN'):
                        return int(parts[4])
                except (ValueError, IndexError):
                    continue
        return None

    def restart(self, sid):
        svc = self.services.get(sid)
        if not svc:
            return

        def run():
            if svc.alive:
                self._stop_thread(svc)
            time.sleep(0.5)
            self._start_thread(svc)

        threading.Thread(target=run, daemon=True).start()

    # ---------- 一键启动 ----------
    def start_all(self):
        order = ['mysql', 'redis', 'livekit', 'backend', 'frontend', 'transcribe']

        def run():
            for sid in order:
                svc = self.services[sid]
                self._start_thread(svc)
                if sid == 'mysql':
                    # MySQL 需等到「可认证连接」才认为就绪（仅端口通时后端仍可能连库失败）
                    t0 = time.time()
                    while time.time() - t0 < 120 and not self._mysql_ready(1):
                        time.sleep(0.5)
                    continue
                # 其余服务按端口就绪后启动下一个；后端就绪后起前端（HTTPS 证书依赖）
                wait_port = {('redis', 6379): 20, ('livekit', 7880): 30,
                             ('backend', 5678): 120, ('transcribe', 8300): 30}.get((sid, svc.port))
                if wait_port:
                    t0 = time.time()
                    while time.time() - t0 < wait_port and not port_open(svc.port, 0.3):
                        time.sleep(0.5)

        threading.Thread(target=run, daemon=True).start()

    def stop_all(self, done_cb=None):
        """并行停止所有服务；done_cb() 在全部停止后回调（供退出流程等待）"""
        sids = ['frontend', 'backend', 'transcribe', 'livekit', 'redis', 'mysql']
        targets = [s for s in (self.services.get(i) for i in sids) if s and s.alive]
        if not targets:
            if done_cb:
                done_cb()
            return

        def wrapped(svc):
            self._stop_thread(svc)
            if done_cb:
                done_cb()

        # 每个服务一个停止线程，taskkill /T /F 为强杀，各服务互不等待
        for svc in targets:
            threading.Thread(target=wrapped, args=(svc,), daemon=True).start()

    # ---------- 状态 ----------
    def any_alive(self):
        """是否有本程序启动且仍在运行的服务"""
        return any(s.alive for s in self.services.values())

    def refresh_status(self):
        """由后台线程定时调用，结合进程与端口刷新状态（勿在 UI 线程调用，含阻塞端口检测）"""
        for svc in self.services.values():
            if svc.state in ('starting', 'stopping'):
                # 状态迁移由启动/停止线程自行管理，避免轮询干扰
                continue
            if svc.alive:
                if svc.state != 'running':
                    svc.state = 'running'
                    self.emit('status', svc.sid)
            elif port_open(svc.port, 0.15):
                if not svc.external:
                    svc.external = True
                    svc.state = 'running'
                    self.emit('status', svc.sid)
            else:
                svc.external = False
                if svc.state != 'stopped':
                    svc.state = 'stopped'
                    self.emit('status', svc.sid)
