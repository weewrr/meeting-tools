# -*- coding: utf-8 -*-
"""图形界面：深色开发者工具风（ui-ux-pro-max 设计系统）—— 左侧导航 + 服务卡片 / 环境管理 / 日志"""
import json
import os
import queue
import sys
import threading
import time
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from env_manager import EnvManager, COMPONENTS
from servers import ServiceManager

# ---------- 配色（Dark OLED / 开发者工具风） ----------
C_BG = '#0F172A'            # 页面背景
C_CARD = '#1B2336'          # 卡片背景
C_CARD_HOVER = '#232C42'    # 卡片悬浮
C_SIDEBAR = '#0B1120'       # 侧边栏（更深）
C_SIDEBAR_HOVER = '#141C2E'
C_SIDEBAR_ACTIVE = '#1B2540'
C_BORDER = '#2A3550'
C_GREEN = '#22C55E'         # 运行 / 主 CTA
C_GREEN_HOVER = '#16A34A'
C_RED = '#EF4444'           # 危险操作
C_RED_HOVER = '#DC2626'
C_BLUE = '#3B82F6'          # 次级操作
C_BLUE_HOVER = '#2563EB'
C_ORANGE = '#F59E0B'        # 过渡态（启动中/停止中）
C_ORANGE_DIM = '#8A5A0B'    # 呼吸灯暗态
C_GRAY = '#94A3B8'          # 静默文字
C_TEXT = '#F8FAFC'           # 主文字
C_MUTED = '#94A3B8'         # 辅助文字
C_NEUTRAL = '#475569'       # 中性按钮（停止等）
C_NEUTRAL_HOVER = '#576275'
C_LOG_BG = '#0D1117'        # 终端风日志底
C_LOG_FG = '#C9D1D9'

STATE_TEXT = {
    'stopped': ('●  已停止', C_GRAY),
    'starting': ('●  启动中', C_ORANGE),
    'stopping': ('●  停止中', C_ORANGE),
    'running': ('●  运行中', C_GREEN),
    'error': ('●  启动失败', C_RED),
}

FONT = ('Microsoft YaHei UI', 10)
FONT_BOLD = ('Microsoft YaHei UI', 10, 'bold')
FONT_TITLE = ('Microsoft YaHei UI', 14, 'bold')
FONT_SMALL = ('Microsoft YaHei UI', 9)
FONT_MONO = ('Consolas', 9)


def _hover(widget, normal, hover):
    """给按钮/卡片绑定悬浮反馈（tkinter 无原生 hover）"""
    widget.bind('<Enter>', lambda e: widget.config(bg=hover))
    widget.bind('<Leave>', lambda e: widget.config(bg=normal))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('轻会议·服务器管理器')
        self.geometry('980x680')
        self.minsize(860, 600)
        self.configure(bg=C_BG)
        self._setup_ttk_style()

        # 事件队列：服务/环境后台线程 -> UI
        self.events = queue.Queue()

        self.env_mgr = EnvManager(on_event=self._push_event)
        self.svc_mgr = ServiceManager(self.env_mgr.project_root,
                                      on_event=self._push_event)

        self._build_layout()
        self.after(200, self._poll_events)
        self.after(300, self.env_mgr.scan_async)
        self._start_status_thread()
        self._start_meeting_monitor()

        # 状态呼吸灯：过渡态（启动中/停止中）闪烁，等待可感知
        self._blink_on = False
        self.after(600, self._blink_tick)

        self.protocol('WM_DELETE_WINDOW', self._on_close)

    # ---------- ttk 深色样式 ----------
    def _setup_ttk_style(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('Treeview', background=C_CARD, fieldbackground=C_CARD,
                        foreground=C_TEXT, rowheight=28, borderwidth=0,
                        bordercolor=C_BORDER)
        style.map('Treeview',
                  background=[('selected', '#2A3A5E')],
                  foreground=[('selected', C_TEXT)])
        style.configure('Treeview.Heading', background='#232C42',
                        foreground=C_MUTED, relief='flat', font=FONT_SMALL)
        style.map('Treeview.Heading', background=[('active', C_CARD_HOVER)])
        style.configure('TCombobox', fieldbackground=C_CARD, background=C_CARD,
                        foreground=C_TEXT, arrowcolor=C_MUTED, bordercolor=C_BORDER)
        style.map('TCombobox',
                  fieldbackground=[('readonly', C_CARD)],
                  foreground=[('readonly', C_TEXT)])
        style.configure('Horizontal.TProgressbar', background=C_GREEN,
                        troughcolor=C_CARD, bordercolor=C_CARD,
                        lightcolor=C_GREEN, darkcolor=C_GREEN)
        style.configure('TCheckbutton', background=C_BG, foreground=C_TEXT)
        # Combobox 下拉列表
        self.option_add('*TCombobox*Listbox*Background', C_CARD)
        self.option_add('*TCombobox*Listbox*Foreground', C_TEXT)
        self.option_add('*TCombobox*Listbox*selectBackground', '#2A3A5E')
        self.option_add('*TCombobox*Listbox*selectForeground', C_TEXT)

    # ---------- 事件桥 ----------
    def _push_event(self, kind, sid, data):
        self.events.put((kind, sid, data))

    # ---------- 后台状态轮询（端口检测耗时，放后台线程避免卡 UI） ----------
    def _start_status_thread(self):
        def loop():
            while True:
                time.sleep(2)
                try:
                    self.svc_mgr.refresh_status()
                except Exception:
                    pass
        threading.Thread(target=loop, daemon=True).start()

    # ---------- 会议监听轮询（后台线程，每 3 秒查询后端活跃会议） ----------
    def _start_meeting_monitor(self):
        self._meet_sig = None
        self._meet_duration_labels = []

        def fetch_meetings():
            # 后端在 Windows 上可能只监听 IPv6(::1)，双栈尝试避免误报"未运行"
            for host in ('127.0.0.1', '::1'):
                base = f'http://[{host}]:5678' if ':' in host else f'http://{host}:5678'
                try:
                    with urllib.request.urlopen(base + '/api/meetings/active', timeout=2) as r:
                        return json.loads(r.read().decode('utf-8'))
                except Exception:
                    continue
            return None

        def loop():
            while True:
                self._push_event('meetings', 'monitor', fetch_meetings())
                time.sleep(3)
        threading.Thread(target=loop, daemon=True).start()

    # ---------- 布局 ----------
    def _build_layout(self):
        self.sidebar = tk.Frame(self, bg=C_SIDEBAR, width=190)
        self.sidebar.pack(side='left', fill='y')
        self.sidebar.pack_propagate(False)

        # 品牌区
        brand = tk.Frame(self.sidebar, bg=C_SIDEBAR)
        brand.pack(fill='x', padx=18, pady=(22, 18))
        tk.Label(brand, text='服务器管理器', font=FONT_TITLE, bg=C_SIDEBAR,
                 fg=C_TEXT, anchor='w').pack(fill='x')
        tk.Label(brand, text='Server Manager', font=FONT_SMALL, bg=C_SIDEBAR,
                 fg='#5B6B85', anchor='w').pack(fill='x')

        self.nav_buttons = {}
        self._active_page = 'home'
        for key, label in [('home', '首页'), ('meet', '会议监听'), ('env', '环境管理'),
                           ('trans', '转写服务'), ('keys', 'Key 管理'), ('logs', '运行日志')]:
            b = tk.Label(self.sidebar, text='  ' + label, font=FONT_BOLD, anchor='w',
                         bg=C_SIDEBAR, fg=C_GRAY, padx=22, pady=11, cursor='hand2')
            b.pack(fill='x')
            b.bind('<Button-1>', lambda e, k=key: self.show_page(k))
            b.bind('<Enter>', lambda e, w=b, k=key: w.config(
                bg=C_SIDEBAR_ACTIVE if k == self._active_page else C_SIDEBAR_HOVER))
            b.bind('<Leave>', lambda e, w=b, k=key: w.config(
                bg=C_SIDEBAR_ACTIVE if k == self._active_page else C_SIDEBAR))
            self.nav_buttons[key] = b

        # 底部版本信息
        tk.Label(self.sidebar, text='LiteMeet · dev', font=FONT_SMALL,
                 bg=C_SIDEBAR, fg='#3D4A63').pack(side='bottom', pady=14)

        self.content = tk.Frame(self, bg=C_BG)
        self.content.pack(side='left', fill='both', expand=True, padx=18, pady=16)

        self.pages = {
            'home': self._build_home(self.content),
            'meet': self._build_meet(self.content),
            'env': self._build_env(self.content),
            'trans': self._build_transcribe(self.content),
            'keys': self._build_keys(self.content),
            'logs': self._build_logs(self.content),
        }
        self.show_page('home')

    def show_page(self, key):
        self._active_page = key
        for k, b in self.nav_buttons.items():
            if k == key:
                b.config(bg=C_SIDEBAR_ACTIVE, fg=C_TEXT)
            else:
                b.config(bg=C_SIDEBAR, fg=C_GRAY)
        for k, p in self.pages.items():
            p.pack_forget()
        self.pages[key].pack(fill='both', expand=True)

    # ---------- 首页 ----------
    def _build_home(self, parent):
        page = tk.Frame(parent, bg=C_BG)
        top = tk.Frame(page, bg=C_BG)
        top.pack(fill='x', pady=(0, 14))
        tk.Label(top, text='服务管理', font=FONT_TITLE, bg=C_BG, fg=C_TEXT).pack(side='left')
        b_start_all = tk.Button(top, text='一键启动', font=FONT_BOLD, bg=C_GREEN, fg='#04150A',
                                activebackground=C_GREEN_HOVER, activeforeground='#04150A',
                                relief='flat', padx=18, pady=5, bd=0,
                                cursor='hand2', command=self.svc_mgr.start_all)
        b_start_all.pack(side='right', padx=4)
        _hover(b_start_all, C_GREEN, C_GREEN_HOVER)
        b_stop_all = tk.Button(top, text='全部停止', font=FONT_BOLD, bg=C_NEUTRAL, fg=C_TEXT,
                               activebackground=C_NEUTRAL_HOVER, relief='flat',
                               padx=18, pady=5, bd=0, cursor='hand2',
                               command=self.svc_mgr.stop_all)
        b_stop_all.pack(side='right', padx=4)
        _hover(b_stop_all, C_NEUTRAL, C_NEUTRAL_HOVER)

        grid = tk.Frame(page, bg=C_BG)
        grid.pack(fill='both', expand=True, anchor='nw')
        # 两行等高（取内容最高者）：所有卡片统一尺寸
        grid.rowconfigure(0, uniform='cards')
        grid.rowconfigure(1, uniform='cards')
        self.cards = {}
        sids = ['mysql', 'redis', 'livekit', 'backend', 'frontend', 'transcribe']
        for i, sid in enumerate(sids):
            svc = self.svc_mgr.services[sid]
            card = tk.Frame(grid, bg=C_CARD, highlightbackground=C_BORDER,
                            highlightthickness=1)
            # 卡片填满行高（行高=本行最高卡片），横向铺满列
            card.grid(row=i // 3, column=i % 3, sticky='nsew', padx=6, pady=6)
            grid.columnconfigure(i % 3, weight=1)

            head = tk.Frame(card, bg=C_CARD)
            head.pack(fill='x', padx=14, pady=(12, 0))
            tk.Label(head, text=svc.name, font=FONT_BOLD, bg=C_CARD,
                     fg=C_TEXT).pack(side='left')
            state_lbl = tk.Label(head, text='●  已停止', font=FONT_SMALL, bg=C_CARD,
                                 fg=C_GRAY)
            state_lbl.pack(side='right')

            tk.Label(card, text=f'端口 {svc.port} · {svc.desc}', font=FONT_SMALL,
                     bg=C_CARD, fg=C_MUTED, anchor='w', justify='left',
                     wraplength=210).pack(fill='x', padx=14, pady=(4, 0))

            # 弹性空隙：等高卡片中，按钮统一锚定底部对齐
            tk.Frame(card, bg=C_CARD).pack(fill='both', expand=True)

            # 底部分隔线 + 按钮区
            tk.Frame(card, bg=C_BORDER, height=1).pack(fill='x', padx=14)
            btns = tk.Frame(card, bg=C_CARD)
            btns.pack(fill='x', padx=14, pady=(8, 12))
            b_start = tk.Button(btns, text='启动', font=FONT_SMALL, bg=C_BLUE, fg='white',
                                activebackground=C_BLUE_HOVER, relief='flat',
                                bd=0, cursor='hand2', pady=4,
                                command=lambda s=sid: self.svc_mgr.start(s))
            b_start.pack(side='left', expand=True, fill='x')
            _hover(b_start, C_BLUE, C_BLUE_HOVER)
            b_stop = tk.Button(btns, text='停止', font=FONT_SMALL, bg=C_NEUTRAL, fg=C_TEXT,
                               activebackground=C_NEUTRAL_HOVER, relief='flat',
                               bd=0, cursor='hand2', pady=4,
                               command=lambda s=sid: self.svc_mgr.stop(s))
            b_stop.pack(side='left', expand=True, fill='x', padx=(6, 6))
            _hover(b_stop, C_NEUTRAL, C_NEUTRAL_HOVER)
            b_log = tk.Button(btns, text='日志', font=FONT_SMALL, bg=C_CARD_HOVER,
                              fg=C_MUTED, activebackground=C_BORDER, relief='flat',
                              bd=0, cursor='hand2', pady=4,
                              command=lambda s=sid: self._jump_log(s))
            b_log.pack(side='left', expand=True, fill='x')
            _hover(b_log, C_CARD_HOVER, C_BORDER)

            self.cards[sid] = {'state': state_lbl, 'card': card}
        self._update_cards()
        return page

    def _update_cards(self):
        for sid, refs in self.cards.items():
            svc = self.svc_mgr.services[sid]
            text, color = STATE_TEXT.get(svc.state, (svc.state, C_GRAY))
            if svc.external and svc.state == 'running':
                text = '●  运行中(外部)'
            refs['state'].config(text=text, fg=color)

    def _blink_tick(self):
        """过渡态呼吸灯：启动中/停止中的状态点闪烁，让等待可感知"""
        self._blink_on = not self._blink_on
        for sid, refs in self.cards.items():
            svc = self.svc_mgr.services[sid]
            if svc.state in ('starting', 'stopping'):
                refs['state'].config(fg=C_ORANGE if self._blink_on else C_ORANGE_DIM)
        self.after(600, self._blink_tick)

    def _jump_log(self, sid):
        self.show_page('logs')
        if hasattr(self, 'log_combo'):
            self.log_combo.set(self.svc_mgr.services[sid].name)
            self._rebuild_log()

    # ---------- 会议监听 ----------
    ROLE_TEXT = {'owner': '创建者', 'host': '主持人', 'member': '成员'}
    ROLE_COLOR = {'owner': C_ORANGE, 'host': C_BLUE, 'member': C_GRAY}

    def _build_meet(self, parent):
        page = tk.Frame(parent, bg=C_BG)
        top = tk.Frame(page, bg=C_BG)
        top.pack(fill='x', pady=(0, 12))
        tk.Label(top, text='会议监听', font=FONT_TITLE, bg=C_BG, fg=C_TEXT).pack(side='left')
        self.meet_status = tk.Label(top, text='●  连接中...', font=FONT_SMALL,
                                    bg=C_BG, fg=C_GRAY)
        self.meet_status.pack(side='right')

        # 可滚动内容区（Canvas 承载，会议多时可滚动查看）
        canvas = tk.Canvas(page, bg=C_BG, highlightthickness=0, bd=0)
        scroll = tk.Scrollbar(page, command=canvas.yview, bg=C_CARD,
                              troughcolor=C_BG, activebackground=C_NEUTRAL)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        inner = tk.Frame(canvas, bg=C_BG)
        win = canvas.create_window((0, 0), window=inner, anchor='nw')
        inner.bind('<Configure>', lambda e: canvas.configure(
            scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(
            win, width=e.width))
        # 鼠标滚轮（进入区域时接管，离开时释放，避免影响其他页面）
        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units')
        canvas.bind('<Enter>', lambda e: canvas.bind_all('<MouseWheel>', _wheel))
        canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))
        self.meet_inner = inner
        self._meet_empty = tk.Label(inner, text='等待后端连接...', font=FONT,
                                     bg=C_BG, fg=C_GRAY, pady=40)
        self._meet_empty.pack(fill='x')
        return page

    def _render_meetings(self, meetings):
        if not hasattr(self, 'meet_status'):
            return
        if meetings is None:
            self.meet_status.config(text='●  后端未运行', fg=C_RED)
        else:
            self.meet_status.config(
                text=f"●  后端在线 · {time.strftime('%H:%M:%S')} 更新", fg=C_GREEN)
        # 数据结构未变时只刷新时长，不重建控件（避免闪烁）
        sig = None if meetings is None else json.dumps(
            meetings, sort_keys=True, ensure_ascii=False)
        if sig != self._meet_sig:
            self._meet_sig = sig
            self._rebuild_meeting_cards(meetings)
        self._update_meeting_durations()

    def _rebuild_meeting_cards(self, meetings):
        for w in self.meet_inner.winfo_children():
            w.destroy()
        self._meet_duration_labels = []
        if meetings is None:
            self._meet_empty = tk.Label(self.meet_inner,
                                        text='后端服务未运行\n请到「首页」先启动 MySQL，再启动后端服务',
                                        font=FONT, bg=C_BG, fg=C_GRAY,
                                        justify='center', pady=60)
            self._meet_empty.pack(fill='x')
            return
        if not meetings:
            self._meet_empty = tk.Label(self.meet_inner,
                                        text='当前没有正在进行的会议',
                                        font=FONT, bg=C_BG, fg=C_GRAY,
                                        justify='center', pady=60)
            self._meet_empty.pack(fill='x')
            return
        for m in meetings:
            self._build_one_meeting_card(m)

    def _build_one_meeting_card(self, m):
        card = tk.Frame(self.meet_inner, bg=C_CARD, highlightbackground=C_BORDER,
                        highlightthickness=1)
        card.pack(fill='x', pady=6)

        # 头部：会议号 + 标题 | 人数
        head = tk.Frame(card, bg=C_CARD)
        head.pack(fill='x', padx=14, pady=(10, 0))
        room_lbl = tk.Label(head, text=str(m.get('roomId', '')), font=FONT_MONO,
                            bg=C_CARD, fg=C_GREEN)
        room_lbl.pack(side='left')
        title = str(m.get('title') or '').strip()
        if title:
            tk.Label(head, text=' · ' + title, font=FONT_BOLD, bg=C_CARD,
                     fg=C_TEXT).pack(side='left')
        tk.Label(head, text=f"{m.get('participantCount', 0)} / {m.get('maxPeers', 50)} 人",
                 font=FONT_SMALL, bg=C_CARD, fg=C_MUTED).pack(side='right')

        # 信息行：开始时间 / 创建者 / 主持人 / 锁定 | 已进行时长（动态刷新）
        info = tk.Frame(card, bg=C_CARD)
        info.pack(fill='x', padx=14, pady=(4, 0))
        created = m.get('createdAt', 0) / 1000
        flags = []
        if m.get('locked'):
            flags.append('已锁定')
        if m.get('muteLocked'):
            flags.append('全员禁言')
        base = (f"开始 {time.strftime('%H:%M:%S', time.localtime(created))}"
                f" · 创建者 {m.get('ownerName') or '—'}"
                f" · 主持人 {m.get('hostName') or '—'}"
                + (' · ' + ' / '.join(flags) if flags else ''))
        tk.Label(info, text=base, font=FONT_SMALL, bg=C_CARD, fg=C_MUTED).pack(side='left')
        dur_lbl = tk.Label(info, text='', font=FONT_SMALL, bg=C_CARD, fg=C_BLUE)
        dur_lbl.pack(side='right')
        # 记录时长标签，数据不变时也随时间刷新
        self._meet_duration_labels.append((dur_lbl, m.get('createdAt', 0),
                                           m.get('deadline', 0)))

        # 分隔线 + 参会人列表
        tk.Frame(card, bg=C_BORDER, height=1).pack(fill='x', padx=14, pady=(8, 4))
        peers = m.get('peers', [])
        body = tk.Frame(card, bg=C_CARD)
        body.pack(fill='x', padx=14, pady=(0, 10))
        if not peers:
            tk.Label(body, text='（暂无参会人）', font=FONT_SMALL, bg=C_CARD,
                     fg=C_GRAY).pack(anchor='w')
            return
        for p in peers:
            row = tk.Frame(body, bg=C_CARD)
            row.pack(fill='x', pady=2)
            role = p.get('role', 'member')
            dot = tk.Label(row, text='●', font=FONT_SMALL, bg=C_CARD,
                           fg=self.ROLE_COLOR.get(role, C_GRAY), width=2)
            dot.pack(side='left')
            name = tk.Label(row, text=str(p.get('name', '参会者')), font=FONT_SMALL,
                            bg=C_CARD, fg=C_TEXT, width=12, anchor='w')
            name.pack(side='left')
            tk.Label(row, text=self.ROLE_TEXT.get(role, '成员'), font=FONT_SMALL,
                     bg=C_CARD, fg=self.ROLE_COLOR.get(role, C_GRAY), width=6,
                     anchor='w').pack(side='left')
            states = []
            for key, label in [('audio', '麦克风'), ('video', '摄像头'), ('screen', '共享')]:
                on = bool(p.get(key))
                states.append(f"{label} {'开' if on else '关'}")
            tk.Label(row, text=' · '.join(states), font=FONT_SMALL, bg=C_CARD,
                     fg=C_MUTED).pack(side='left', padx=(6, 0))

    def _update_meeting_durations(self):
        now = time.time() * 1000
        for lbl, created, deadline in self._meet_duration_labels:
            secs = max(0, int((now - created) / 1000))
            text = f"已进行 {secs // 60} 分 {secs % 60:02d} 秒"
            if deadline and deadline > now:
                remain = int((deadline - now) / 60000) + 1
                text += f" · 剩余 {remain} 分钟"
            lbl.config(text=text)

    # ---------- 环境管理 ----------
    def _build_env(self, parent):
        page = tk.Frame(parent, bg=C_BG)
        top = tk.Frame(page, bg=C_BG)
        top.pack(fill='x', pady=(0, 12))
        tk.Label(top, text='环境管理', font=FONT_TITLE, bg=C_BG, fg=C_TEXT).pack(side='left')
        b_rescan = tk.Button(top, text='重新扫描', font=FONT_SMALL, bg=C_BLUE, fg='white',
                             activebackground=C_BLUE_HOVER, relief='flat', padx=14,
                             pady=2, bd=0, cursor='hand2',
                             command=self.env_mgr.scan_async)
        b_rescan.pack(side='right')
        _hover(b_rescan, C_BLUE, C_BLUE_HOVER)

        columns = ('name', 'status', 'version', 'path')
        tree = ttk.Treeview(page, columns=columns, show='headings', height=10)
        for col, text, w, anchor in [('name', '组件', 130, 'w'), ('status', '状态', 90, 'center'),
                                     ('version', '版本', 110, 'w'), ('path', '路径', 430, 'w')]:
            tree.heading(col, text=text)
            tree.column(col, width=w, anchor=anchor)
        # 表格自动撑满剩余空间，窗口越大表格越大
        tree.pack(fill='both', expand=True)
        tree.bind('<<TreeviewSelect>>', lambda e: self._env_detail())
        self.env_tree = tree
        self.env_items = {}

        detail = tk.Frame(page, bg=C_CARD, highlightbackground=C_BORDER,
                          highlightthickness=1)
        detail.pack(fill='x', pady=14)
        tk.Label(detail, text='组件操作', font=FONT_BOLD, bg=C_CARD, fg=C_TEXT,
                 anchor='w').pack(fill='x', padx=14, pady=(10, 4))
        self.env_detail_lbl = tk.Label(detail, text='请选择上方组件', font=FONT_SMALL,
                                       bg=C_CARD, fg=C_MUTED, anchor='w', justify='left')
        self.env_detail_lbl.pack(fill='x', padx=14, pady=(0, 10))

        btns = tk.Frame(detail, bg=C_CARD)
        btns.pack(fill='x', padx=14, pady=(0, 12))
        self.btn_download = tk.Button(btns, text='下载安装', font=FONT_SMALL, bg=C_GREEN,
                                      fg='#04150A', activebackground=C_GREEN_HOVER,
                                      activeforeground='#04150A', relief='flat', padx=16,
                                      pady=2, bd=0, cursor='hand2',
                                      command=self._download_selected)
        self.btn_download.pack(side='left')
        _hover(self.btn_download, C_GREEN, C_GREEN_HOVER)
        self.btn_setpath = tk.Button(btns, text='设置本地路径', font=FONT_SMALL, bg=C_BLUE,
                                     fg='white', activebackground=C_BLUE_HOVER,
                                     relief='flat', padx=16, pady=2, bd=0, cursor='hand2',
                                     command=self._setpath_selected)
        self.btn_setpath.pack(side='left', padx=8)
        _hover(self.btn_setpath, C_BLUE, C_BLUE_HOVER)
        self.btn_delete = tk.Button(btns, text='删除', font=FONT_SMALL, bg=C_RED,
                                    fg='white', activebackground=C_RED_HOVER,
                                    relief='flat', padx=16, pady=2, bd=0, cursor='hand2',
                                    command=self._delete_selected)
        self.btn_delete.pack(side='left')
        _hover(self.btn_delete, C_RED, C_RED_HOVER)
        self.btn_delete.config(state='disabled')

        prog_wrap = tk.Frame(page, bg=C_BG)
        prog_wrap.pack(fill='x', pady=(4, 0))
        self.dl_lbl = tk.Label(prog_wrap, text='', font=FONT_SMALL, bg=C_BG, fg=C_MUTED,
                               anchor='w')
        self.dl_lbl.pack(fill='x')
        self.dl_bar = ttk.Progressbar(prog_wrap, mode='determinate', maximum=100)
        self.dl_bar.pack(fill='x', pady=4)

        tip = tk.Label(page, text='说明：下载会安装到 server-manager\\envs 目录，不影响系统环境；'
                                  '本机已安装的环境请点「设置本地路径」直接指定；'
                                  '「删除」仅对通过本程序下载的环境可用。',
                       font=FONT_SMALL, bg=C_BG, fg='#5B6B85', anchor='w', justify='left')
        tip.pack(fill='x', pady=(8, 0))
        return page

    def _refresh_env_tree(self):
        for iid in self.env_tree.get_children():
            self.env_tree.delete(iid)
        self.env_items = {}
        for comp in COMPONENTS:
            info = self.env_mgr.results.get(comp.id, {})
            status = '已安装' if info.get('found') else '未安装'
            iid = self.env_tree.insert('', 'end', values=(
                info.get('name', comp.name), status,
                info.get('version', ''), info.get('path', '')))
            self.env_items[iid] = comp.id

    def _selected_env(self):
        sel = self.env_tree.selection()
        if not sel:
            return None
        return self.env_items.get(sel[0])

    def _env_detail(self):
        cid = self._selected_env()
        if not cid:
            return
        info = self.env_mgr.results.get(cid, {})
        comp = next(c for c in COMPONENTS if c.id == cid)
        state = '已安装' if info.get('found') else '未安装'
        managed = self.env_mgr.is_managed(cid)
        source = '（本程序下载，可删除）' if managed else '（本机安装 / 自定义路径）'
        self.env_detail_lbl.config(
            text=f"{info.get('name', comp.name)}（{state}）{source}\n"
                 f"用途：{comp.note}\n"
                 f"路径：{info.get('path') or '—'}")
        self.btn_download.config(state='normal')
        self.btn_delete.config(state='normal' if managed else 'disabled')

    def _download_selected(self):
        cid = self._selected_env()
        if not cid:
            messagebox.showinfo('提示', '请先在上方列表选择一个组件')
            return
        if messagebox.askyesno('确认下载', '将下载并安装到 server-manager\\envs 目录，'
                                            '下载体积较大（MySQL 约 200MB），是否继续？'):
            self.btn_download.config(state='disabled')
            self.dl_bar['value'] = 0
            self.env_mgr.download_async(cid)

    def _setpath_selected(self):
        cid = self._selected_env()
        if not cid:
            messagebox.showinfo('提示', '请先在上方列表选择一个组件')
            return
        comp = next(c for c in COMPONENTS if c.id == cid)
        hint = {'jdk': 'java.exe', 'maven': 'mvn.cmd', 'node': 'node.exe',
                'mysql': 'mysqld.exe', 'redis': 'redis-server.exe',
                'livekit': 'livekit-server.exe', 'whisper': 'whisper-server.exe'}.get(cid, '')
        path = filedialog.askopenfilename(
            title=f'选择 {comp.name} 的可执行文件（{hint}）',
            filetypes=[('可执行文件', '*.exe;*.cmd;*.bat'), ('所有文件', '*.*')])
        if path:
            self.env_mgr.set_path(cid, path)

    # 删除环境时，检查依赖它的服务是否在运行
    ENV_TO_SVC = {
        'jdk': ('backend', 'frontend'),
        'mysql': ('mysql',),
        'redis': ('redis',),
        'livekit': ('livekit',),
        'whisper': ('transcribe',),
        'maven': (),
        'node': (),
    }

    def _delete_selected(self):
        cid = self._selected_env()
        if not cid:
            messagebox.showinfo('提示', '请先在上方列表选择一个组件')
            return
        if not self.env_mgr.is_managed(cid):
            messagebox.showinfo('提示', '只能删除通过「环境管理」下载安装的环境')
            return
        # 相关服务运行中时不允许删除（文件被占用）
        for sid in self.ENV_TO_SVC.get(cid, ()):
            svc = self.svc_mgr.services[sid]
            if svc.alive or svc.state == 'running':
                messagebox.showwarning('无法删除',
                                       f'{svc.name} 正在运行，请先停止该服务后再删除环境')
                return
        comp = next(c for c in COMPONENTS if c.id == cid)
        if not messagebox.askyesno('确认删除',
                                   f'确定删除 {comp.name} 吗？\n'
                                   '将删除 server-manager\\envs 下对应的文件，不影响系统环境。'):
            return
        # 删除大量文件耗时，放后台线程避免卡死界面
        self.btn_delete.config(state='disabled')

        def work():
            ok, msg = self.env_mgr.delete(cid)
            self._push_event('del_result', cid, (ok, msg))

        threading.Thread(target=work, daemon=True).start()

    # ---------- 转写服务 ----------
    def _tmod(self):
        """延迟加载 transcribe-server/server.py 模块（管理模型/Key/限时）"""
        if not hasattr(self, '_tserver'):
            sys.path.insert(0, os.path.join(self.env_mgr.project_root, 'transcribe-server'))
            import server as tserver
            self._tserver = tserver
        return self._tserver

    def _build_transcribe(self, parent):
        page = tk.Frame(parent, bg=C_BG)
        top = tk.Frame(page, bg=C_BG)
        top.pack(fill='x', pady=(0, 12))
        tk.Label(top, text='转写服务', font=FONT_TITLE, bg=C_BG, fg=C_TEXT).pack(side='left')
        self.t_state_lbl = tk.Label(top, text='●  已停止', font=FONT_SMALL, bg=C_BG, fg=C_GRAY)
        self.t_state_lbl.pack(side='right')
        tk.Button(top, text='启动服务', font=FONT_SMALL, bg=C_GREEN, fg='#04150A',
                  activebackground=C_GREEN_HOVER, relief='flat', bd=0, padx=12, pady=2,
                  cursor='hand2', command=lambda: self.svc_mgr.start('transcribe')
                  ).pack(side='right', padx=(0, 8))

        # --- 模型管理 ---
        mcard = tk.Frame(page, bg=C_CARD, highlightbackground=C_BORDER, highlightthickness=1)
        mcard.pack(fill='both', expand=True, pady=(0, 10))
        tk.Label(mcard, text='模型管理', font=FONT_BOLD, bg=C_CARD,
                 fg=C_TEXT, anchor='w').pack(fill='x', padx=14, pady=(10, 4))
        columns = ('name', 'size', 'status')
        tree = ttk.Treeview(mcard, columns=columns, show='headings', height=5)
        tree.heading('name', text='模型')
        tree.heading('size', text='大小')
        tree.heading('status', text='状态')
        tree.column('name', width=150)
        tree.column('size', width=90, anchor='center')
        tree.column('status', width=160, anchor='center')
        tree.pack(fill='both', expand=True, padx=14)
        self.t_tree = tree
        self.t_tree_items = {}

        mrow = tk.Frame(mcard, bg=C_CARD)
        mrow.pack(fill='x', padx=14, pady=8)
        self.t_dl_combo = ttk.Combobox(mrow, state='readonly', width=16, font=FONT_SMALL)
        self.t_dl_combo.pack(side='left')
        b_dl = tk.Button(mrow, text='下载', font=FONT_SMALL, bg=C_GREEN, fg='#04150A',
                         activebackground=C_GREEN_HOVER, relief='flat', bd=0,
                         cursor='hand2', padx=10,
                         command=self._t_download_model)
        b_dl.pack(side='left', padx=6)
        _hover(b_dl, C_GREEN, C_GREEN_HOVER)
        b_use = tk.Button(mrow, text='设为当前', font=FONT_SMALL, bg=C_BLUE, fg='white',
                          activebackground=C_BLUE_HOVER, relief='flat', bd=0,
                          cursor='hand2', padx=10,
                          command=self._t_use_model)
        b_use.pack(side='left', padx=6)
        _hover(b_use, C_BLUE, C_BLUE_HOVER)
        b_del = tk.Button(mrow, text='删除', font=FONT_SMALL, bg=C_RED, fg='white',
                          activebackground=C_RED_HOVER, relief='flat', bd=0,
                          cursor='hand2', padx=10,
                          command=self._t_delete_model)
        b_del.pack(side='left', padx=6)
        _hover(b_del, C_RED, C_RED_HOVER)

        self.t_dl_bar = ttk.Progressbar(mcard, mode='determinate')
        self.t_dl_bar.pack(fill='x', padx=14)
        self.t_dl_lbl = tk.Label(mcard, text='', font=FONT_SMALL, bg=C_CARD,
                                 fg=C_MUTED, anchor='w')
        self.t_dl_lbl.pack(fill='x', padx=14, pady=(2, 10))

        # --- 限时开放 ---
        scard = tk.Frame(page, bg=C_CARD, highlightbackground=C_BORDER, highlightthickness=1)
        scard.pack(fill='x')
        tk.Label(scard, text='限时开放', font=FONT_BOLD, bg=C_CARD,
                 fg=C_TEXT, anchor='w').pack(fill='x', padx=14, pady=(10, 4))
        srow = tk.Frame(scard, bg=C_CARD)
        srow.pack(fill='x', padx=14, pady=(0, 6))
        self.t_sched_combo = ttk.Combobox(srow, state='readonly', width=12, font=FONT_SMALL,
                                          values=['永久开放', '每日时段', '日期范围'])
        self.t_sched_combo.current(0)
        self.t_sched_combo.bind('<<ComboboxSelected>>', lambda e: self._t_sched_mode_changed())
        self.t_sched_combo.pack(side='left')
        tk.Label(srow, text='开始', font=FONT_SMALL, bg=C_CARD, fg=C_MUTED).pack(side='left', padx=(10, 2))
        self.t_start_var = tk.StringVar(value='09:00')
        tk.Entry(srow, textvariable=self.t_start_var, width=14, font=FONT_SMALL,
                 bg=C_LOG_BG, fg=C_TEXT, insertbackground=C_TEXT, relief='flat',
                 highlightthickness=1, highlightbackground=C_BORDER).pack(side='left')
        tk.Label(srow, text='结束', font=FONT_SMALL, bg=C_CARD, fg=C_MUTED).pack(side='left', padx=(10, 2))
        self.t_end_var = tk.StringVar(value='18:00')
        tk.Entry(srow, textvariable=self.t_end_var, width=14, font=FONT_SMALL,
                 bg=C_LOG_BG, fg=C_TEXT, insertbackground=C_TEXT, relief='flat',
                 highlightthickness=1, highlightbackground=C_BORDER).pack(side='left')
        b_save = tk.Button(srow, text='保存', font=FONT_SMALL, bg=C_GREEN, fg='#04150A',
                           activebackground=C_GREEN_HOVER, relief='flat', bd=0,
                           cursor='hand2', padx=10,
                           command=self._t_save_schedule)
        b_save.pack(side='left', padx=10)
        _hover(b_save, C_GREEN, C_GREEN_HOVER)
        # 状态行（单行，绿色=开放中 / 红色=已关闭）
        self.t_sched_hint = tk.Label(scard, text='当前状态：●  开放中', font=FONT_SMALL,
                                     bg=C_CARD, fg=C_GREEN, anchor='w')
        self.t_sched_hint.pack(fill='x', padx=14, pady=(0, 2))
        self.t_sched_fmt = tk.Label(
            scard, text='格式：每日 09:00-18:00（支持跨天）| 日期范围 2026-08-29 10:00 - 2026-09-05 22:00｜开放时段内免 Key',
            font=FONT_SMALL, bg=C_CARD, fg=C_MUTED, anchor='w')
        self.t_sched_fmt.pack(fill='x', padx=14, pady=(0, 10))

        self._t_refresh()
        self.after(1000, self._t_poll)
        return page

    # ---- Key 管理页 ----
    def _build_keys(self, parent):
        page = tk.Frame(parent, bg=C_BG)
        top = tk.Frame(page, bg=C_BG)
        top.pack(fill='x', pady=(0, 14))
        tk.Label(top, text='Key 管理', font=FONT_TITLE, bg=C_BG, fg=C_TEXT).pack(side='left')
        tk.Label(top, text='转写服务访问密钥（限时开放窗口内免 Key）', font=FONT_SMALL,
                 bg=C_BG, fg=C_MUTED).pack(side='left', padx=12)

        # 密钥列表卡
        kcard = tk.Frame(page, bg=C_CARD, highlightbackground=C_BORDER, highlightthickness=1)
        kcard.pack(fill='both', expand=True, pady=(0, 10))
        tk.Label(kcard, text='密钥列表', font=FONT_BOLD, bg=C_CARD, fg=C_TEXT, anchor='w'
                 ).pack(fill='x', padx=14, pady=(10, 4))
        cols = ('key', 'status', 'note', 'created')
        tree = ttk.Treeview(kcard, columns=cols, show='headings', height=8)
        for col, text, w, anchor in [('key', 'API Key', 330, 'w'), ('status', '状态', 80, 'center'),
                                     ('note', '备注', 150, 'w'), ('created', '创建时间', 130, 'w')]:
            tree.heading(col, text=text)
            tree.column(col, width=w, anchor=anchor)
        tree.pack(fill='both', expand=True, padx=14)
        self.k_tree = tree
        self.k_items = {}

        # 操作区
        krow = tk.Frame(kcard, bg=C_CARD)
        krow.pack(fill='x', padx=14, pady=(6, 12))
        tk.Label(krow, text='备注:', font=FONT_SMALL, bg=C_CARD, fg=C_MUTED).pack(side='left')
        self.k_note_var = tk.StringVar(value='')
        tk.Entry(krow, textvariable=self.k_note_var, width=20, font=FONT_SMALL, bg=C_LOG_BG,
                 fg=C_TEXT, insertbackground=C_TEXT, relief='flat', highlightthickness=1,
                 highlightbackground=C_BORDER).pack(side='left', padx=4)
        b_gen = tk.Button(krow, text='生成新 Key', font=FONT_SMALL, bg=C_GREEN, fg='#04150A',
                          activebackground=C_GREEN_HOVER, relief='flat', bd=0, cursor='hand2',
                          padx=10, command=self._k_gen)
        b_gen.pack(side='left', padx=4)
        _hover(b_gen, C_GREEN, C_GREEN_HOVER)
        b_add = tk.Button(krow, text='手动添加', font=FONT_SMALL, bg=C_BLUE, fg='white',
                          activebackground=C_BLUE_HOVER, relief='flat', bd=0, cursor='hand2',
                          padx=10, command=self._k_add)
        b_add.pack(side='left', padx=4)
        _hover(b_add, C_BLUE, C_BLUE_HOVER)
        b_copy = tk.Button(krow, text='复制选中', font=FONT_SMALL, bg=C_CARD_HOVER, fg=C_MUTED,
                           relief='flat', bd=0, cursor='hand2', padx=10, command=self._k_copy)
        b_copy.pack(side='left', padx=4)
        _hover(b_copy, C_CARD_HOVER, C_BORDER)
        b_toggle = tk.Button(krow, text='禁用/启用', font=FONT_SMALL, bg=C_NEUTRAL, fg=C_TEXT,
                             activebackground=C_NEUTRAL_HOVER, relief='flat', bd=0,
                             cursor='hand2', padx=10, command=self._k_toggle)
        b_toggle.pack(side='left', padx=4)
        _hover(b_toggle, C_NEUTRAL, C_NEUTRAL_HOVER)
        b_del = tk.Button(krow, text='删除', font=FONT_SMALL, bg=C_RED, fg='white',
                          activebackground=C_RED_HOVER, relief='flat', bd=0, cursor='hand2',
                          padx=10, command=self._k_delete)
        b_del.pack(side='left', padx=4)
        _hover(b_del, C_RED, C_RED_HOVER)

        # 网页后端使用 Key（存于 MySQL）
        dcard = tk.Frame(page, bg=C_CARD, highlightbackground=C_BORDER, highlightthickness=1)
        dcard.pack(fill='x')
        tk.Label(dcard, text='网页后端使用（存于 MySQL）', font=FONT_BOLD, bg=C_CARD,
                 fg=C_TEXT, anchor='w').pack(fill='x', padx=14, pady=(10, 4))
        drow = tk.Frame(dcard, bg=C_CARD)
        drow.pack(fill='x', padx=14, pady=(0, 6))
        self.k_db_lbl = tk.Label(drow, text='后端 Key: （未获取）', font=FONT_SMALL,
                                 bg=C_CARD, fg=C_MUTED, anchor='w')
        self.k_db_lbl.pack(side='left')
        b_set_db = tk.Button(drow, text='将选中 Key 设为网页后端使用', font=FONT_SMALL, bg=C_BLUE,
                             fg='white', activebackground=C_BLUE_HOVER, relief='flat', bd=0,
                             cursor='hand2', padx=10, command=self._k_set_db)
        b_set_db.pack(side='right')
        _hover(b_set_db, C_BLUE, C_BLUE_HOVER)
        self.k_db_hint = tk.Label(dcard, text='网页后端（MySQL）以单个 Key 访问转写服务；选中上方某行后点此按钮即可切换。',
                                  font=FONT_SMALL, bg=C_CARD, fg=C_MUTED, anchor='w')
        self.k_db_hint.pack(fill='x', padx=14, pady=(0, 10))

        self.after(1500, self._k_poll)
        return page

    def _k_selected(self):
        sel = self.k_tree.selection()
        if not sel:
            return None
        return str(self.k_tree.item(sel[0], 'values')[0])

    def _k_refresh(self):
        try:
            t = self._tmod()
            keys = t.list_keys()
            sig = json.dumps(keys, ensure_ascii=False, sort_keys=True)
            if sig == getattr(self, '_k_sig', None):
                return
            self._k_sig = sig
            self.k_tree.delete(*self.k_tree.get_children())
            self.k_items = {}
            for k in keys:
                enabled = k.get('enabled')
                status = '●  启用' if enabled else '○  禁用'
                self.k_items[k['key']] = self.k_tree.insert(
                    '', 'end', values=(k['key'], status, k.get('note', ''), k.get('created', '')))
        except Exception:
            pass

    def _k_refresh_db(self):
        """获取网页后端（MySQL）当前使用的 Key（掩码显示）"""
        try:
            req = urllib.request.Request('http://127.0.0.1:5678/api/config', method='GET')
            with urllib.request.urlopen(req, timeout=3) as r:
                cfg = json.loads(r.read().decode('utf-8'))
            key = (cfg.get('transcribe') or {}).get('apiKey') or ''
            self.k_db_lbl.config(text='后端 Key: %s' % (key if key else '（未设置）'))
        except Exception:
            self.k_db_lbl.config(text='后端 Key: （后端未运行，无法获取）')

    def _k_poll(self):
        self._k_refresh()
        try:
            self._k_refresh_db()
        except Exception:
            pass
        self.after(1500, self._k_poll)

    def _k_gen(self):
        note = self.k_note_var.get().strip()
        key = 'sk-' + __import__('secrets').token_hex(16)
        def run():
            t = self._tmod()
            ok, msg = t.add_key(key, note or '自动生成')
            self._push_event('k_msg', 'keys', (ok, msg))
            self._push_event('k_refresh', 'keys', None)
        threading.Thread(target=run, daemon=True).start()

    def _k_add(self):
        key = __import__('tkinter').simpledialog.askstring(
            '手动添加 Key', '输入要添加的 API Key：', parent=self)
        if not key:
            return
        note = self.k_note_var.get().strip()
        def run():
            t = self._tmod()
            ok, msg = t.add_key(key.strip(), note)
            self._push_event('k_msg', 'keys', (ok, msg))
            self._push_event('k_refresh', 'keys', None)
        threading.Thread(target=run, daemon=True).start()

    def _k_copy(self):
        key = self._k_selected()
        if not key:
            messagebox.showinfo('提示', '请先选择一行 Key')
            return
        self.clipboard_clear()
        self.clipboard_append(key)
        messagebox.showinfo('已复制', '已复制完整 Key，可直接粘贴到网页设置 → 语音转写服务。')

    def _k_toggle(self):
        key = self._k_selected()
        if not key:
            messagebox.showinfo('提示', '请先选择一行 Key')
            return
        def run():
            t = self._tmod()
            cur = next((k for k in t.list_keys() if k['key'] == key), None)
            if not cur:
                return
            ok, msg = t.set_key_enabled(key, not cur.get('enabled'))
            self._push_event('k_msg', 'keys', (ok, msg))
            self._push_event('k_refresh', 'keys', None)
        threading.Thread(target=run, daemon=True).start()

    def _k_delete(self):
        key = self._k_selected()
        if not key:
            messagebox.showinfo('提示', '请先选择一行 Key')
            return
        if not messagebox.askyesno('确认删除', '确定删除该 Key？\n%s\n\n删除后立即失效。' % key):
            return
        def run():
            t = self._tmod()
            ok, msg = t.delete_key(key)
            self._push_event('k_msg', 'keys', (ok, msg))
            self._push_event('k_refresh', 'keys', None)
        threading.Thread(target=run, daemon=True).start()

    def _k_set_db(self):
        key = self._k_selected()
        if not key:
            messagebox.showinfo('提示', '请先选择一行 Key')
            return
        if not messagebox.askyesno('确认', '将所选 Key 设为网页后端使用的 Key？\n网页设置将用此 Key 访问转写服务。'):
            return
        def run():
            ok, msg = False, ''
            try:
                req = urllib.request.Request(
                    'http://127.0.0.1:5678/api/config',
                    data=json.dumps({'transcribe': {'apiKey': key}}).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}, method='POST')
                urllib.request.urlopen(req, timeout=8)
                ok = True
                msg = '已同步到网页后端（MySQL）'
            except Exception as e:
                msg = '同步失败：' + str(e)
            self._push_event('k_msg', 'keys', (ok, msg))
            self._push_event('k_refresh', 'keys', None)
        threading.Thread(target=run, daemon=True).start()

    # ---- 转写页：数据刷新 ----
    def _t_refresh(self):
        """刷新模型表 / 下载下拉（从模块与配置读取，轻量文件 IO）"""
        try:
            t = self._tmod()
            models = t.installed_models()
            cur = t.get_current_model()
            # 模型表
            self.t_tree.delete(*self.t_tree.get_children())
            self.t_tree_items = {}
            dl = t.download_status()
            for m in models:
                size = os.path.getsize(t.model_path(m)) / 1048576
                status = '●  当前使用' if m == cur else '可用'
                self.t_tree_items[m] = self.t_tree.insert('', 'end', values=(m, '%.0fMB' % size, status))
            for name, st in dl.items():
                if st['status'] == 'downloading':
                    self.t_tree_items['@' + name] = self.t_tree.insert(
                        '', 'end', values=(name, t.MODELS_CATALOG.get(name, ('?',))[0],
                                           '下载中 %d%%' % st['progress']))
            # 下载下拉：未安装且不在下载中的模型
            busy = {n for n, st in dl.items() if st['status'] == 'downloading'}
            pending = [m for m in t.MODELS_CATALOG if m not in models and m not in busy]
            self.t_dl_combo['values'] = ['%s（%s）' % (m, t.MODELS_CATALOG[m][0]) for m in pending]
            if pending:
                self.t_dl_combo.current(0)
            else:
                self.t_dl_combo.set('')
            # 限时设置回显
            sch = cfg.get('schedule') or {'mode': 'always'}
            mode = sch.get('mode', 'always')
            self.t_sched_combo.current({'always': 0, 'daily': 1, 'range': 2}.get(mode, 0))
            if mode == 'daily':
                self.t_start_var.set(sch.get('start', '09:00'))
                self.t_end_var.set(sch.get('end', '18:00'))
            elif mode == 'range':
                self.t_start_var.set(sch.get('start', ''))
                self.t_end_var.set(sch.get('end', ''))
            self._t_sched_mode_changed(refresh_only=True)
        except Exception:
            pass

    def _t_poll(self):
        """轮询：服务状态灯 + 下载进度 + 开放状态"""
        try:
            svc = self.svc_mgr.services.get('transcribe')
            if svc:
                text, color = STATE_TEXT.get(svc.state, (svc.state, C_GRAY))
                if svc.external and svc.state == 'running':
                    text = '●  运行中(外部)'
                self.t_state_lbl.config(text=text, fg=color)
            t = self._tmod()
            dl = t.download_status()
            active = [(n, st) for n, st in dl.items() if st['status'] == 'downloading']
            if active:
                n, st = active[0]
                self.t_dl_bar['value'] = st['progress']
                self.t_dl_lbl.config(text='正在下载 %s：%s' % (n, st.get('msg', '')))
            else:
                fin = [(n, st) for n, st in dl.items() if st['status'] in ('done', 'error')]
                if fin:
                    n, st = fin[-1]
                    self.t_dl_lbl.config(text=st.get('msg', ''), fg=C_MUTED)
                    if st['status'] == 'done':
                        self.t_dl_bar['value'] = 100
                # 无下载任务时静默；有新完成时刷新一次模型表
                if hasattr(self, '_t_last_done') and self._t_last_done != fin:
                    self._t_refresh()
                self._t_last_done = fin
            # 开放状态提示（单行）
            if hasattr(self, 't_sched_hint'):
                self.t_sched_hint.config(
                    text='当前状态：' + ('●  开放中' if t.is_open_now() else '●  已关闭'),
                    fg=C_GREEN if t.is_open_now() else C_RED)
        except Exception:
            pass
        self.after(1000, self._t_poll)

    # ---- 转写页：操作 ----
    def _t_selected_model(self):
        sel = self.t_tree.selection()
        if not sel:
            return None
        values = self.t_tree.item(sel[0], 'values')
        name = str(values[0])
        return None if name.startswith('@') else name

    def _t_download_model(self):
        t = self._tmod()
        raw = self.t_dl_combo.get()
        if not raw:
            messagebox.showinfo('提示', '没有可下载的模型（全部已安装）')
            return
        name = raw.split('（')[0].strip()
        if name not in t.MODELS_CATALOG:
            return
        ok = t.download_model_async(name)
        if not ok:
            messagebox.showinfo('提示', '模型 %s 已在下载中或已安装' % name)
        self._t_refresh()

    def _t_use_model(self):
        name = self._t_selected_model()
        if not name:
            messagebox.showinfo('提示', '请先在表格中选择一个已下载的模型')
            return
        def run():
            t = self._tmod()
            ok, msg = t.switch_model(name)
            self._push_event('t_msg', 'transcribe', (ok, msg))
            self._push_event('t_refresh', 'transcribe', None)
        threading.Thread(target=run, daemon=True).start()

    def _t_delete_model(self):
        name = self._t_selected_model()
        if not name:
            messagebox.showinfo('提示', '请先在表格中选择一个模型')
            return
        if not messagebox.askyesno('确认删除', '确定删除模型 %s？删除后需重新下载。' % name):
            return
        def run():
            t = self._tmod()
            ok, msg = t.delete_model(name)
            self._push_event('t_msg', 'transcribe', (ok, msg))
            self._push_event('t_refresh', 'transcribe', None)
        threading.Thread(target=run, daemon=True).start()

    def _t_sched_mode_changed(self, refresh_only=False):
        """切换限时模式时给输入框填合适的占位格式"""
        mode = self.t_sched_combo.get()
        if mode == '每日时段':
            if ':' not in self.t_start_var.get():
                self.t_start_var.set('09:00')
            if ':' not in self.t_end_var.get():
                self.t_end_var.set('18:00')
        elif mode == '日期范围':
            cur = self.t_start_var.get()
            if ':' in cur and '-' not in cur:
                self.t_start_var.set('')
                self.t_end_var.set('')

    def _t_save_schedule(self):
        t = self._tmod()
        mode = {'永久开放': 'always', '每日时段': 'daily', '日期范围': 'range'}[self.t_sched_combo.get()]
        sch = {'mode': mode}
        s, e = self.t_start_var.get().strip(), self.t_end_var.get().strip()
        if mode == 'daily':
            import re as _re
            if not _re.match(r'^\d{1,2}:\d{2}$', s) or not _re.match(r'^\d{1,2}:\d{2}$', e):
                messagebox.showerror('格式错误', '每日时段格式应为 HH:MM，如 09:00 / 18:00')
                return
            sch.update({'start': s, 'end': e})
        elif mode == 'range':
            try:
                from datetime import datetime as _dt
                st, et = _dt.strptime(s, '%Y-%m-%d %H:%M'), _dt.strptime(e, '%Y-%m-%d %H:%M')
                if et <= st:
                    raise ValueError
            except Exception:
                messagebox.showerror('格式错误', '日期范围格式应为 YYYY-MM-DD HH:MM，且结束晚于开始')
                return
            sch.update({'start': s, 'end': e})
        t.update_config(lambda c: c.update({'schedule': sch}))
        messagebox.showinfo('已保存', '限时开放设置已保存，立即生效')
        self._t_refresh()

    # ---------- 日志 ----------
    def _build_logs(self, parent):
        page = tk.Frame(parent, bg=C_BG)
        top = tk.Frame(page, bg=C_BG)
        top.pack(fill='x', pady=(0, 12))
        tk.Label(top, text='运行日志', font=FONT_TITLE, bg=C_BG, fg=C_TEXT).pack(side='left')
        self.log_combo = ttk.Combobox(top, state='readonly', width=22, font=FONT_SMALL)
        self.log_combo['values'] = [s.name for s in self.svc_mgr.services.values()]
        self.log_combo.current(0)
        self.log_combo.bind('<<ComboboxSelected>>', lambda e: self._rebuild_log())
        self.log_combo.pack(side='left', padx=14)
        b_clear = tk.Button(top, text='清空显示', font=FONT_SMALL, bg=C_NEUTRAL, fg=C_TEXT,
                            activebackground=C_NEUTRAL_HOVER, relief='flat', padx=12,
                            pady=2, bd=0, cursor='hand2', command=self._clear_log)
        b_clear.pack(side='left')
        _hover(b_clear, C_NEUTRAL, C_NEUTRAL_HOVER)
        self.auto_scroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text='自动滚动', variable=self.auto_scroll).pack(side='left', padx=10)

        body = tk.Frame(page, bg=C_LOG_BG, highlightbackground=C_BORDER, highlightthickness=1)
        body.pack(fill='both', expand=True)
        scroll = tk.Scrollbar(body)
        scroll.pack(side='right', fill='y')
        self.log_text = tk.Text(body, wrap='none', font=FONT_MONO, bd=0,
                                bg=C_LOG_BG, fg=C_LOG_FG, relief='flat',
                                insertbackground=C_TEXT,
                                selectbackground='#2A3A5E',
                                yscrollcommand=scroll.set)
        self.log_text.pack(fill='both', expand=True, padx=8, pady=8)
        self.log_text.config(state='disabled')
        scroll.config(command=self.log_text.yview)
        return page

    def _current_log_sid(self):
        name = self.log_combo.get()
        return next((s for s, v in self.svc_mgr.services.items() if v.name == name), None)

    def _rebuild_log(self):
        """切换服务时重建日志视图（只显示尾部，避免大量插入卡顿）"""
        if not hasattr(self, 'log_text'):
            return
        sid = self._current_log_sid()
        if not sid:
            return
        lines = self.svc_mgr.services[sid].log_lines()
        tail = lines[-800:]
        self.log_text.config(state='normal')
        self.log_text.delete('1.0', 'end')
        if len(lines) > 800:
            self.log_text.insert('end', f'...（省略 {len(lines) - 800} 行历史日志）\n')
        for ln in tail:
            self.log_text.insert('end', ln + '\n')
        if self.auto_scroll.get():
            self.log_text.see('end')
        self.log_text.config(state='disabled')

    def _append_log(self, sid, lines):
        """批量追加当前显示服务的日志，并限制控件总行数"""
        if not lines:
            return
        self.log_text.config(state='normal')
        for ln in lines[-400:]:
            self.log_text.insert('end', ln + '\n')
        total = int(self.log_text.index('end-1c').split('.')[0])
        if total > 2000:
            self.log_text.delete('1.0', f'{total - 1500}.0')
        if self.auto_scroll.get():
            self.log_text.see('end')
        self.log_text.config(state='disabled')

    def _clear_log(self):
        self.log_text.config(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.config(state='disabled')

    # ---------- 事件轮询（日志按批合并渲染，避免逐条刷新卡顿） ----------
    def _poll_events(self):
        pending_logs = {}      # sid -> [行]
        status_dirty = False
        try:
            while True:
                kind, sid, data = self.events.get_nowait()
                if kind == 'log':
                    lst = pending_logs.setdefault(sid, [])
                    lst.append(data)
                    if len(lst) > 600:      # 防止极端刷屏撑爆内存
                        del lst[:len(lst) - 400]
                elif kind == 'status':
                    status_dirty = True
                elif kind == 'meetings':
                    self._render_meetings(data)
                elif kind == 'env_scan':
                    self._refresh_env_tree()
                    self.svc_mgr.env = self.env_mgr.get_server_env()
                    self._env_detail()
                elif kind == 'dl_progress':
                    loaded, total = data
                    if total:
                        self.dl_bar['maximum'] = total
                        self.dl_bar['value'] = loaded
                        self.dl_lbl.config(text=f'下载中 {loaded / 1048576:.1f} / {total / 1048576:.1f} MB')
                    else:
                        self.dl_lbl.config(text=f'下载中 {loaded / 1048576:.1f} MB')
                elif kind == 'dl_state':
                    state, msg = data
                    if state == 'done':
                        self.dl_bar['value'] = 100
                        self.dl_lbl.config(text=msg)
                        self.btn_download.config(state='normal')
                        messagebox.showinfo('安装完成', msg)
                    elif state == 'error':
                        self.dl_lbl.config(text='失败：' + msg)
                        self.btn_download.config(state='normal')
                        messagebox.showerror('下载失败', msg)
                    else:
                        self.dl_lbl.config(text=msg)
                elif kind == 'del_result':
                    ok, msg = data
                    self.btn_delete.config(state='normal')
                    if ok:
                        messagebox.showinfo('删除完成', msg)
                    else:
                        messagebox.showerror('删除失败', msg)
                elif kind == 't_msg':
                    ok, msg = data
                    if ok:
                        messagebox.showinfo('转写服务', msg)
                    else:
                        messagebox.showerror('转写服务', msg)
                elif kind == 't_refresh':
                    self._t_refresh()
                elif kind == 'k_msg':
                    ok, msg = data
                    if ok:
                        messagebox.showinfo('Key 管理', msg)
                    else:
                        messagebox.showerror('Key 管理', msg)
                elif kind == 'k_refresh':
                    self._k_refresh()
        except queue.Empty:
            pass
        if status_dirty:
            self._update_cards()
        # 只渲染当前查看的服务，其余服务的日志保存在各自 deque 中，切换时再取
        if pending_logs and hasattr(self, 'log_combo'):
            sid = self._current_log_sid()
            if sid and sid in pending_logs:
                self._append_log(sid, pending_logs[sid])
        self.after(200, self._poll_events)

    # ---------- 退出 ----------
    def _on_close(self):
        if not messagebox.askyesno('退出', '退出程序时会停止所有由它启动的服务，确定退出吗？'):
            return
        # 点确定后立即隐藏窗口（体感"秒关"），停止工作在后台完成
        self.withdraw()
        if self.svc_mgr.any_alive():
            # 后台并行停止全部服务，完成后（或超时 5 秒）真正销毁窗口
            def finalize():
                self.after(0, self.destroy)
            threading.Timer(5.0, finalize).start()  # 超时兜底，防卡死
            self.svc_mgr.stop_all(done_cb=lambda: finalize())
        else:
            self.destroy()
