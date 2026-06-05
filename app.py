#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import html
import json
import os
import re
import time
import uuid
import random
import base64
import secrets
import threading
from collections import deque
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response, session
import urllib.request
from bilibili_api import search, comment, Credential, video, user
from bilibili_api.search import SearchObjectType, OrderVideo
from bilibili_api.comment import CommentResourceType, OrderType
from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents


# ========== 评论内容变体生成器 ==========
# 设计原则：多策略叠加，保证每次生成结果不同，但不改变原意

# 语气前缀（轻量，不改变语义）
VARIANT_PREFIXES = [
    '', '', '',  # 大概率不加
    '嗯，', '哇，', '哈哈，', '确实，', '真的，', '说实话，',
    '不得不说，', '讲真，', '说实话 ', '确实 ', '真的 ',
]

# 语气后缀（轻量语气词）
VARIANT_TAIL_WORDS = [
    '', '', '',  # 大概率不加
    '啊', '呢', '呀', '吧', '嘛', '哦', '哈', '呐',
]

# 尾部标点组合
VARIANT_PUNCTUATIONS = [
    '~', '～', '！', '！！', '...', '。。', '～～', '!',
    '。', '👏', '👍', '✨',
]

# 表情池（按情感分组，更自然）
EMOJI_POSITIVE = ['😊', '😄', '👍', '🎉', '✨', '💪', '🔥', '❤️', '😎', '🙌', '💯', '⭐', '🌟', '🎈']
EMOJI_SUBTLE = ['🤔', '👀', '🤝', '💛', '🌿', '☕', '🎵', '📌', '💡', '📎']

# 同义词替换表（常见评论用词）
SYNONYM_MAP = {
    '好看': ['精彩', '不错看', '很赞'],
    '厉害': ['牛', '强', '优秀', '很强'],
    '不错': ['挺好', '可以', '还行', '不错'],
    '喜欢': ['爱了', '心动', '好爱', '超爱'],
    '支持': ['加油', '顶', '挺你'],
    '有趣': ['有意思', '好玩', '有趣味'],
    '感动': ['触动', '暖心', '感慨'],
    '赞': ['棒', '优秀', '给力'],
    '棒': ['赞', '优秀', '牛'],
    '太好了': ['太棒了', '太好啦', '真不错'],
    '学到了': ['涨知识了', '长见识了', '受教了'],
    '收藏了': ['马克了', '存了', '已收藏'],
    '感谢': ['谢谢', '多谢', '谢啦'],
    '期待': ['蹲', '等更新', '等着看'],
    '优秀': ['厉害', '牛', '赞'],
}


def generate_comment_variant(text):
    """对评论内容生成变体，多策略叠加确保每次不同，但不改变原意"""
    result = text.strip()
    if not result:
        return result

    # ── 策略1：同义词替换（~40%概率，替换1个词） ──
    if random.random() < 0.4:
        for word, synonyms in SYNONYM_MAP.items():
            if word in result and synonyms:
                replacement = random.choice(synonyms)
                # 只替换第一次出现
                result = result.replace(word, replacement, 1)
                break  # 只替换一个词，避免过度变形

    # ── 策略2：语气前缀（~35%概率） ──
    prefix = random.choice(VARIANT_PREFIXES)
    if prefix:
        result = prefix + result

    # ── 策略3：语气后缀（~30%概率，在标点前插入） ──
    tail = random.choice(VARIANT_TAIL_WORDS)
    if tail:
        # 去掉末尾标点，加语气词，再加回标点
        result = result.rstrip('。！!~～. ')
        result = result + tail

    # ── 策略4：尾部标点（~50%概率） ──
    if random.random() < 0.5:
        result = result.rstrip('。！!~～. ')
        punct = random.choice(VARIANT_PUNCTUATIONS)
        result = result + punct

    # ── 策略5：表情（~45%概率，位置随机） ──
    if random.random() < 0.45:
        pool = random.choice([EMOJI_POSITIVE, EMOJI_SUBTLE])
        emoji = random.choice(pool)
        pos_choice = random.random()
        if pos_choice < 0.4:
            result = emoji + ' ' + result       # 开头
        elif pos_choice < 0.8:
            result = result + ' ' + emoji       # 结尾
        else:
            # 中间插入（找空格或逗号位置）
            insert_points = [i for i, c in enumerate(result) if c in ' ，,']
            if insert_points:
                p = random.choice(insert_points)
                result = result[:p] + ' ' + emoji + result[p:]
            else:
                result = result + ' ' + emoji

    # ── 策略6：不可见字符（~15%概率，保证字符串不同） ──
    if random.random() < 0.15 and len(result) > 5:
        pos = random.randint(2, len(result) - 2)
        result = result[:pos] + '\u200b' + result[pos:]

    return result


# ========== API请求限流器 ==========
class RateLimiter:
    """简单的滑动窗口限流器"""
    def __init__(self, max_requests=10, window_seconds=60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.timestamps = deque()
        self.lock = threading.Lock()

    def acquire(self):
        """尝试获取请求许可，返回(bool, wait_seconds)"""
        with self.lock:
            now = time.time()
            # 清理过期的时间戳
            while self.timestamps and self.timestamps[0] < now - self.window_seconds:
                self.timestamps.popleft()

            if len(self.timestamps) < self.max_requests:
                self.timestamps.append(now)
                return True, 0
            else:
                # 计算需要等待的时间
                oldest = self.timestamps[0]
                wait = self.window_seconds - (now - oldest) + 0.5
                return wait <= 30, max(wait, 0)

# 全局限流器：B站搜索API 10次/分钟，评论API 5次/分钟
search_limiter = RateLimiter(max_requests=10, window_seconds=60)
comment_limiter = RateLimiter(max_requests=5, window_seconds=60)

# ========== 用户数据目录 ==========
# 固定数据目录在exe/脚本同级，确保重启后能找到历史数据
import sys as _sys
if getattr(_sys, 'frozen', False):
    # PyInstaller打包后
    _base_dir = os.path.dirname(os.path.abspath(_sys.executable))
else:
    _base_dir = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(_base_dir, "user_data")
os.makedirs(USER_DATA_DIR, exist_ok=True)

# ========== 持久化 secret_key ==========
# 从文件加载或生成新的secret_key，确保重启后Flask Session不变
_SECRET_KEY_FILE = os.path.join(USER_DATA_DIR, ".secret_key")
if os.path.exists(_SECRET_KEY_FILE):
    with open(_SECRET_KEY_FILE, 'r') as _f:
        _persisted_key = _f.read().strip()
else:
    _persisted_key = secrets.token_hex(32)
    with open(_SECRET_KEY_FILE, 'w') as _f:
        _f.write(_persisted_key)

app = Flask(__name__)
app.secret_key = _persisted_key  # 持久化的Flask session密钥，重启不变

@app.after_request
def set_user_sid_cookie(response):
    """每个响应自动设置持久的user_sid cookie，确保重启后不丢失"""
    sid = session.get('sid')
    if sid:
        # 设置cookie有效期1年，确保浏览器重启后依然可用
        response.set_cookie('user_sid', sid, max_age=365*24*3600, httponly=False, samesite='Lax')
    return response

# ========== CSRF Token ==========
csrf_token = secrets.token_hex(16)

@app.route('/api/csrf_token')
def get_csrf_token():
    return jsonify({'csrf_token': csrf_token})

@app.before_request
def check_csrf():
    """CSRF防护：POST请求需携带正确的token"""
    if request.method == 'POST' and request.path.startswith('/api/'):
        token = request.headers.get('X-CSRF-Token') or (request.json or {}).get('_csrf_token')
        if token != csrf_token:
            # 允许无token的本地请求（开发阶段兼容）
            if request.remote_addr not in ('127.0.0.1', '::1'):
                return jsonify({'success': False, 'message': 'CSRF验证失败'}), 403

# 全局错误处理：API路由出错时返回JSON而不是HTML
@app.errorhandler(500)
def internal_error(error):
    return jsonify({'success': False, 'message': f'服务器内部错误: {str(error)}'}), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'message': '接口不存在'}), 404

# BV号格式校验
BV_PATTERN = re.compile(r'^BV[a-zA-Z0-9]{10}$')

def is_valid_bvid(bvid):
    return bool(BV_PATTERN.match(bvid.strip()))

# ========== 多用户会话管理 ==========
user_sessions = {}  # session_id -> BilibiliTool instance
sessions_lock = threading.Lock()

def get_tool():
    """获取当前会话的BilibiliTool实例（多用户隔离）"""
    # 优先使用持久化cookie中的sid（不依赖Flask session签名，重启不变）
    sid = request.cookies.get('user_sid') or session.get('sid')
    if not sid:
        sid = secrets.token_hex(8)
    session['sid'] = sid
    with sessions_lock:
        if sid not in user_sessions:
            user_sessions[sid] = BilibiliTool(session_id=sid)
        return user_sessions[sid]

def get_qr_state():
    """获取当前会话的扫码登录状态"""
    sid = session.get('sid', '')
    key = f'qr_{sid}'
    with sessions_lock:
        if key not in user_sessions:
            user_sessions[key] = {
                'login': None,
                'qr_base64': None,
                'status': 'idle',
                'credential': None,
                'message': ''
            }
        return user_sessions[key]

def get_batch_tasks():
    """获取当前会话的批量任务字典"""
    sid = session.get('sid', '')
    key = f'bt_{sid}'
    with sessions_lock:
        if key not in user_sessions:
            user_sessions[key] = {}
        return user_sessions[key]

def _try_recover_orphaned_data():
    """恢复孤立的旧数据目录中的评论历史"""
    sid = session.get('sid', '')
    if not sid:
        return
    user_dir = os.path.join(USER_DATA_DIR, sid)
    history_file = os.path.join(user_dir, 'comment_history.json')
    # 已经有数据则不需要恢复
    if os.path.exists(history_file):
        with open(history_file, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if data and len(data) > 0:
                    return
            except Exception:
                pass
    # 扫描其他目录，找有数据的目录
    recovered = False
    if os.path.exists(USER_DATA_DIR):
        for dirname in os.listdir(USER_DATA_DIR):
            if dirname == sid or dirname.startswith('.'):
                continue
            orphan_dir = os.path.join(USER_DATA_DIR, dirname)
            if not os.path.isdir(orphan_dir):
                continue
            orphan_history = os.path.join(orphan_dir, 'comment_history.json')
            orphan_config = os.path.join(orphan_dir, 'config.json')
            if os.path.exists(orphan_history):
                try:
                    with open(orphan_history, 'r', encoding='utf-8') as f:
                        hist_data = json.load(f)
                    if hist_data:
                        # 恢复评论历史
                        with open(history_file, 'w', encoding='utf-8') as f:
                            json.dump(hist_data, f, indent=2, ensure_ascii=False)
                        recovered = True
                except Exception:
                    pass
            if not recovered and os.path.exists(orphan_config):
                try:
                    with open(orphan_config, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    # 检查是否有有意义的数据（评论模板、屏蔽词等）
                    has_data = any([
                        config_data.get('comment_templates'),
                        config_data.get('blocked_words'),
                        config_data.get('blocked_authors'),
                        config_data.get('excluded_bvids'),
                        config_data.get('accounts')
                    ])
                    if has_data:
                        config_file = os.path.join(user_dir, 'config.json')
                        if not os.path.exists(config_file):
                            with open(config_file, 'w', encoding='utf-8') as f:
                                json.dump(config_data, f, indent=2, ensure_ascii=False)
                            recovered = True
                except Exception:
                    pass
            if recovered:
                print(f'[数据恢复] 已从 {dirname} 恢复历史数据到当前会话')
                break  # 只恢复第一个有数据的目录

# ========== 全局状态类 ==========
class BilibiliTool:
    def __init__(self, session_id=''):
        self.session_id = session_id
        self.credential = None
        # 每用户独立数据目录
        if session_id:
            self.data_dir = os.path.join(USER_DATA_DIR, session_id)
            os.makedirs(self.data_dir, exist_ok=True)
        else:
            self.data_dir = '.'
        self.config_file = os.path.join(self.data_dir, "config.json")
        self.history_file = os.path.join(self.data_dir, "comment_history.json")
        self.comment_history = {}
        self.comment_templates = []  # 评论模板库
        self.excluded_bvids = []  # 排除的视频BV号列表（保持列表用于存储）
        self.blocked_words = set()   # 屏蔽词集合
        self.blocked_authors = set()  # 黑名单UP主集合
        self.accounts = []  # 多账号列表 [{name, sessdata, bili_jct, buvid3}]
        self.current_account_name = ''  # 当前活跃账号名称
        # 排除BV号快速查找索引
        self._excluded_bvid_set = set()
        self.load_config()
        self.load_history()
        self._rebuild_excluded_index()

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 加载多账号配置
                    self.accounts = config.get('accounts', [])
                    self.current_account_name = config.get('current_account_name', '')
                    # 如果指定了当前账号，恢复credential
                    if self.current_account_name:
                        for acc in self.accounts:
                            if acc.get('name') == self.current_account_name:
                                if acc.get('sessdata') and acc.get('bili_jct'):
                                    self.credential = Credential(
                                        sessdata=acc['sessdata'],
                                        bili_jct=acc['bili_jct'],
                                        buvid3=acc.get('buvid3', '')
                                    )
                                break
                    # 兼容旧配置：直接存储的单账号
                    if not self.credential and config.get('sessdata') and config.get('bili_jct'):
                        self.credential = Credential(
                            sessdata=config['sessdata'],
                            bili_jct=config['bili_jct'],
                            buvid3=config.get('buvid3', '')
                        )
                    # 加载评论模板
                    self.comment_templates = config.get('comment_templates', [])
                    # 加载排除列表
                    self.excluded_bvids = config.get('excluded_bvids', [])
                    # 加载屏蔽词列表（转为set）
                    bw = config.get('blocked_words', [])
                    self.blocked_words = set(bw) if isinstance(bw, list) else bw
                    # 加载黑名单UP主列表（转为set）
                    ba = config.get('blocked_authors', [])
                    self.blocked_authors = set(ba) if isinstance(ba, list) else ba
                    return True
            except Exception as e:
                print(f"加载配置失败: {e}")
        return False

    def save_config(self):
        config = {}
        # 兼容旧配置：也保存单账号字段
        if self.credential:
            config = {
                'sessdata': self.credential.sessdata,
                'bili_jct': self.credential.bili_jct,
                'buvid3': self.credential.buvid3 or ''
            }
        config['accounts'] = self.accounts
        config['current_account_name'] = self.current_account_name
        config['comment_templates'] = self.comment_templates
        config['excluded_bvids'] = self.excluded_bvids
        config['blocked_words'] = self.blocked_words
        config['blocked_words'] = list(self.blocked_words)
        config['blocked_authors'] = list(self.blocked_authors)
        with file_lock:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

    def set_account(self, name, sessdata, bili_jct, buvid3=''):
        """设置当前账号（保存或更新）"""
        # 更新或添加到accounts列表
        found = False
        for acc in self.accounts:
            if acc.get('name') == name:
                acc['sessdata'] = sessdata
                acc['bili_jct'] = bili_jct
                acc['buvid3'] = buvid3
                found = True
                break
        if not found:
            self.accounts.append({
                'name': name,
                'sessdata': sessdata,
                'bili_jct': bili_jct,
                'buvid3': buvid3
            })
        self.current_account_name = name
        self.credential = Credential(sessdata=sessdata, bili_jct=bili_jct, buvid3=buvid3)
        self.save_config()

    def switch_account(self, name):
        """切换到指定账号"""
        for acc in self.accounts:
            if acc.get('name') == name:
                self.current_account_name = name
                self.credential = Credential(
                    sessdata=acc['sessdata'],
                    bili_jct=acc['bili_jct'],
                    buvid3=acc.get('buvid3', '')
                )
                self.save_config()
                return True
        return False

    def delete_account(self, name):
        """删除指定账号"""
        self.accounts = [a for a in self.accounts if a.get('name') != name]
        if self.current_account_name == name:
            self.current_account_name = ''
            self.credential = None
        self.save_config()
        return True

    def add_template(self, text):
        """添加评论模板"""
        text = text.strip()
        if text and text not in self.comment_templates:
            self.comment_templates.append(text)
            self.save_config()
            return True
        return False

    def delete_template(self, index):
        """删除评论模板"""
        if 0 <= index < len(self.comment_templates):
            self.comment_templates.pop(index)
            self.save_config()
            return True
        return False

    def get_random_template(self):
        """随机获取一条模板"""
        if not self.comment_templates:
            return None
        return random.choice(self.comment_templates)

    def _rebuild_excluded_index(self):
        """重建排除BV号的快速查找索引"""
        self._excluded_bvid_set = {ex.get('bvid') for ex in self.excluded_bvids if ex.get('bvid')}

    def is_excluded(self, bvid):
        """O(1)检查视频是否被排除"""
        return bvid in self._excluded_bvid_set

    def load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    self.comment_history = json.load(f)
            except (json.JSONDecodeError, IOError, OSError) as e:
                print(f"加载历史失败: {e}")
                self.comment_history = {}

    def save_history(self):
        with file_lock:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.comment_history, f, indent=2, ensure_ascii=False)

    def is_commented(self, bvid, comment_text=None):
        if bvid not in self.comment_history:
            return False
        if comment_text:
            for record in self.comment_history[bvid]:
                if record.get('comment') == comment_text:
                    return True
            return False
        return len(self.comment_history[bvid]) > 0

    def add_to_history(self, bvid, comment_text, title="", pic=""):
        if bvid not in self.comment_history:
            self.comment_history[bvid] = []
        record = {
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'comment': comment_text,
            'title': title
        }
        if pic:
            record['pic'] = pic
        self.comment_history[bvid].append(record)
        self.save_history()

# ========== 图片代理缓存（LRU + TTL） ==========
from collections import OrderedDict

class LRUCache:
    """简单的LRU缓存，带TTL"""
    def __init__(self, max_size=200, ttl=3600):
        self.max_size = max_size
        self.ttl = ttl
        self._cache = OrderedDict()
        self._timestamps = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._cache:
                # 检查TTL
                if time.time() - self._timestamps[key] > self.ttl:
                    del self._cache[key]
                    del self._timestamps[key]
                    return None
                # 移到末尾（最近使用）
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    def put(self, key, value):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = value
                self._timestamps[key] = time.time()
            else:
                if len(self._cache) >= self.max_size:
                    # 淘汰最久未使用
                    oldest_key, _ = self._cache.popitem(last=False)
                    del self._timestamps[oldest_key]
                self._cache[key] = value
                self._timestamps[key] = time.time()

    def __len__(self):
        return len(self._cache)

image_cache = LRUCache(max_size=200, ttl=3600)

# ========== 文件读写锁 ==========
file_lock = threading.Lock()

def run_async(coro):
    """在新线程中运行异步函数"""
    result = None
    exception = None

    def target():
        nonlocal result, exception
        try:
            result = asyncio.run(coro)
        except Exception as e:
            exception = e

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()

    if exception:
        raise exception
    return result

# ========== 路由 ==========

@app.route('/')
def index():
    # 自动恢复孤立的旧数据
    _try_recover_orphaned_data()
    return render_template('index.html')

@app.route('/api/proxy_image')
def proxy_image():
    """代理B站图片，解决CDN Referer校验问题"""
    url = request.args.get('url', '')
    if not url:
        return '', 400

    # 安全检查：只允许B站域名
    allowed_domains = ['hdslb.com', 'bilivideo.com', 'bilibili.com']
    if not any(domain in url for domain in allowed_domains):
        return '', 403

    # LRU缓存（带TTL）
    cache_key = url
    cached = image_cache.get(cache_key)
    if cached:
        return Response(cached['data'], content_type=cached['type'])

    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        req.add_header('Referer', 'https://www.bilibili.com/')

        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
            content_type = resp.headers.get('Content-Type', 'image/jpeg')

            # 缓存（限制大小）
            if len(data) < 500 * 1024:
                image_cache.put(cache_key, {'data': data, 'type': content_type})

            return Response(data, content_type=content_type)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
        return '', 500

@app.route('/api/status')
def get_status():
    t = get_tool()
    return jsonify({
        'logged_in': t.credential is not None,
        'history_count': len(t.comment_history),
        'blocked_words_count': len(t.blocked_words),
        'blocked_authors_count': len(t.blocked_authors)
    })

@app.route('/api/user_info')
def get_user_info():
    """获取当前登录用户的基本信息"""
    t = get_tool()
    if not t.credential:
        return jsonify({'success': False, 'message': '未登录'})
    
    try:
        async def fetch_info():
            info = await user.get_self_info(credential=t.credential)
            return info
        
        info = run_async(fetch_info())
        
        if not info or not isinstance(info, dict):
            return jsonify({'success': False, 'message': '获取用户信息失败'})
        
        # 提取关键信息（兼容不同字段名）
        # B站API可能用 mid 或 uid，name 或 uname
        uid = info.get('uid', info.get('mid', 0))
        uname = info.get('uname', info.get('name', ''))
        face = info.get('face', '')
        
        # 等级可能在不同位置
        level = 0
        if 'level' in info:
            level = info['level']
        elif 'level_info' in info and isinstance(info['level_info'], dict):
            level = info['level_info'].get('current_level', 0)
        
        # 硬币
        coins = info.get('coins', info.get('money', 0))
        
        # VIP信息
        vip = info.get('vip', {})
        vip_type = 0
        vip_status = 0
        if isinstance(vip, dict):
            vip_type = vip.get('type', 0)
            vip_status = vip.get('status', 0)
        
        result = {
            'success': True,
            'uname': uname,
            'uid': uid,
            'face': face,
            'level': level,
            'coins': coins,
            'vip_type': vip_type,
            'vip_status': vip_status,
            'sex': info.get('sex', ''),
            'sign': info.get('sign', '')
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取用户信息失败: {str(e)}'})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    sessdata = data.get('sessdata', '').strip()
    bili_jct = data.get('bili_jct', '').strip()
    buvid3 = data.get('buvid3', '').strip()
    account_name = data.get('account_name', '').strip() or '默认账号'

    if not sessdata or not bili_jct:
        return jsonify({'success': False, 'message': 'SESSDATA和bili_jct不能为空'})

    get_tool().set_account(account_name, sessdata, bili_jct, buvid3)
    return jsonify({'success': True, 'message': '登录成功'})

# ========== 扫码登录 API ==========

@app.route('/api/qr_login/generate', methods=['POST'])
def qr_login_generate():
    """生成扫码登录二维码"""
    try:
        qr = QrCodeLogin()
        qrs = get_qr_state()

        # generate_qrcode是协程，需要在事件循环中运行
        async def do_generate():
            await qr.generate_qrcode()
            pic = qr.get_qrcode_picture()
            return pic

        pic = run_async(do_generate())

        if not pic:
            return jsonify({'success': False, 'message': '生成二维码失败，返回为空'})

        # Picture对象可能有content属性（bytes）或url属性（本地文件路径）
        qr_bytes = None
        if hasattr(pic, 'content') and pic.content:
            qr_bytes = pic.content
        elif hasattr(pic, 'url') and pic.url:
            # 从本地文件读取
            file_path = pic.url.replace('file://', '')
            if os.path.exists(file_path):
                with open(file_path, 'rb') as f:
                    qr_bytes = f.read()

        if not qr_bytes:
            return jsonify({'success': False, 'message': '无法获取二维码图片数据'})

        # 转换为base64
        qr_base64 = base64.b64encode(qr_bytes).decode('utf-8')

        # 保存登录状态
        qrs['login'] = qr
        qrs['qr_base64'] = qr_base64
        qrs['status'] = 'waiting'
        qrs['message'] = '请用B站APP扫描二维码'

        return jsonify({
            'success': True,
            'qr_image': qr_base64,
            'message': '请用B站APP扫描二维码登录'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'生成二维码失败: {str(e)}'})

@app.route('/api/qr_login/check')
def qr_login_check():
    """检查扫码登录状态"""
    qrs = get_qr_state()
    login_instance = qrs.get('login')
    if not login_instance:
        return jsonify({'success': False, 'status': 'idle', 'message': '未发起扫码登录'})

    try:
        # check_state是协程
        async def do_check():
            return await login_instance.check_state()

        state = run_async(do_check())

        if state == QrCodeLoginEvents.TIMEOUT:
            qrs['status'] = 'expired'
            qrs['message'] = '二维码已过期，请重新生成'
            return jsonify({'success': False, 'status': 'expired', 'message': '二维码已过期，请重新生成'})
        elif state == QrCodeLoginEvents.DONE:
            credential = login_instance.get_credential()
            # 生成账号名称（扫码登录用时间命名）
            import datetime
            account_name = '扫码账号 ' + datetime.datetime.now().strftime('%m-%d %H:%M')
            # 避免重复名称
            base_name = account_name
            suffix = 1
            existing_names = {a.get('name', '') for a in get_tool().accounts}
            while account_name in existing_names:
                account_name = base_name + ' (' + str(suffix) + ')'
                suffix += 1
            get_tool().set_account(account_name, credential.sessdata or '', credential.bili_jct or '', credential.buvid3 or '')

            qrs['status'] = 'done'
            qrs['credential'] = credential
            qrs['message'] = '登录成功！'

            return jsonify({
                'success': True,
                'status': 'done',
                'message': '扫码登录成功！',
                'sessdata': credential.sessdata or ''
            })
        elif state == QrCodeLoginEvents.SCAN:
            qrs['status'] = 'scanning'
            qrs['message'] = '已扫码，请在手机上确认登录'
            return jsonify({'success': True, 'status': 'scanning', 'message': '已扫码，请在手机上确认登录'})
        else:
            qrs['status'] = 'waiting'
            qrs['message'] = '等待扫码...'
            return jsonify({'success': True, 'status': 'waiting', 'message': '等待扫码...'})
    except Exception as e:
        qrs['status'] = 'failed'
        qrs['message'] = str(e)
        return jsonify({'success': False, 'status': 'failed', 'message': f'检查状态失败: {str(e)}'})

@app.route('/api/qr_login/reset', methods=['POST'])
def qr_login_reset():
    """重置扫码登录状态"""
    qrs = get_qr_state()
    qrs['login'] = None
    qrs['qr_base64'] = None
    qrs['status'] = 'idle'
    qrs['message'] = ''
    return jsonify({'success': True})

# ========== 多账号管理 API ==========

@app.route('/api/accounts')
def get_accounts():
    """获取已保存的账号列表"""
    return jsonify({
        'success': True,
        'accounts': get_tool().accounts,
        'current_account_name': get_tool().current_account_name,
        'logged_in': get_tool().credential is not None
    })

@app.route('/api/accounts/switch', methods=['POST'])
def switch_account():
    """切换到指定账号"""
    data = request.json
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'message': '账号名称不能为空'})
    if get_tool().switch_account(name):
        return jsonify({'success': True, 'message': '切换成功', 'current_account_name': name})
    return jsonify({'success': False, 'message': '账号不存在'})

@app.route('/api/accounts/delete', methods=['POST'])
def delete_account():
    """删除指定账号"""
    data = request.json
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'message': '账号名称不能为空'})
    get_tool().delete_account(name)
    return jsonify({'success': True, 'message': '删除成功'})

@app.route('/api/search', methods=['POST'])
def search_videos():
    t = get_tool()
    if not t.credential:
        return jsonify({'success': False, 'message': '请先登录'})

    data = request.json
    keyword = data.get('keyword', '').strip()
    page_size = min(int(data.get('page_size', 20)), 50)
    filter_commented = data.get('filter_commented', True)
    sort_by = data.get('sort_by', 'default')  # default, play, comment, duration_desc, duration_asc
    min_play = int(data.get('min_play', 0))
    min_duration = int(data.get('min_duration', 0))
    max_duration = int(data.get('max_duration', 0))

    if not keyword:
        return jsonify({'success': False, 'message': '关键词不能为空'})

    # B站API排序映射（放在try内，避免属性不存在导致500）
    try:
        order_map = {
            'default': OrderVideo.TOTALRANK,
            'play': OrderVideo.CLICK,
        }
        # COMMENT排序可能不存在于某些版本
        if hasattr(OrderVideo, 'COMMENT'):
            order_map['comment'] = OrderVideo.COMMENT
        elif hasattr(OrderVideo, 'DM'):
            order_map['comment'] = OrderVideo.DM
        order_type = order_map.get(sort_by, OrderVideo.TOTALRANK)
    except Exception as e:
        order_type = OrderVideo.TOTALRANK

    try:
        videos = []
        seen_bvids = set()
        filtered_count = 0
        filtered_invalid_bv = 0
        filtered_low_play = 0
        filtered_duration = 0
        filtered_commented = 0
        filtered_excluded = 0
        filtered_blocked = 0
        filtered_author = 0
        page = 1
        search_page_size = 50

        while len(videos) < page_size:
            # 限流检查
            allowed, wait = search_limiter.acquire()
            if not allowed:
                if wait > 0:
                    time.sleep(min(wait, 5))  # 最多等5秒
                    search_limiter.acquire()  # 重试获取

            async def do_search(p):
                result = await search.search_by_type(
                    keyword=keyword,
                    search_type=SearchObjectType.VIDEO,
                    order_type=order_type,
                    page=p,
                    page_size=search_page_size
                )
                return result

            result = run_async(do_search(page))

            if 'result' not in result or not result['result']:
                break

            page_result_count = len(result['result'])
            page_videos = 0
            for item in result['result']:
                bvid = item.get('bvid', '').strip()

                if not is_valid_bvid(bvid):
                    filtered_count += 1
                    filtered_invalid_bv += 1
                    continue

                # 去重：同一BV号不重复添加
                if bvid in seen_bvids:
                    continue

                title = html.escape(item.get('title', '').replace('<em class="keyword">', '').replace('</em>', ''))
                author = html.escape(item.get('author', ''))
                play_count = item.get('play', 0)
                danmaku_count = item.get('video_review', 0)
                comment_count = item.get('review', 0)
                duration = item.get('duration', '')
                description = item.get('description', '')
                pic = item.get('pic', '')  # 视频封面
                pubdate = item.get('pubdate', 0)  # 发布时间（Unix时间戳）

                # 解析时长（格式："MM:SS" 或 "HH:MM:SS"）
                duration_seconds = 0
                if duration:
                    parts = str(duration).split(':')
                    try:
                        if len(parts) == 2:
                            duration_seconds = int(parts[0]) * 60 + int(parts[1])
                        elif len(parts) == 3:
                            duration_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    except (ValueError, IndexError):
                        duration_seconds = 0

                # 播放量筛选
                if min_play > 0 and play_count < min_play:
                    filtered_count += 1
                    filtered_low_play += 1
                    continue

                # 时长筛选
                if min_duration > 0 and duration_seconds < min_duration:
                    filtered_count += 1
                    filtered_duration += 1
                    continue
                if max_duration > 0 and duration_seconds > max_duration:
                    filtered_count += 1
                    filtered_duration += 1
                    continue

                # 已评论筛选
                if filter_commented and t.is_commented(bvid):
                    filtered_count += 1
                    filtered_commented += 1
                    continue

                # 排除列表筛选（O(1)查找）
                if t.is_excluded(bvid):
                    filtered_count += 1
                    filtered_excluded += 1
                    continue

                # 屏蔽词筛选（匹配标题和描述，不区分大小写）
                if t.blocked_words:
                    title_lower = title.lower()
                    desc_lower = description.lower()
                    blocked = any(word.lower() in title_lower or word.lower() in desc_lower for word in t.blocked_words)
                    if blocked:
                        filtered_count += 1
                        filtered_blocked += 1
                        continue

                # 黑名单UP主筛选
                if t.blocked_authors:
                    author_lower = author.lower()
                    blocked = any(ba.lower() in author_lower for ba in t.blocked_authors)
                    if blocked:
                        filtered_count += 1
                        filtered_author += 1
                        continue

                if len(videos) >= page_size:
                    break

                # 封面URL处理
                pic_url = ''
                if pic:
                    pic_url = pic if pic.startswith('http') else 'https:' + pic

                videos.append({
                    'bvid': bvid,
                    'title': title,
                    'author': author,
                    'play_count': play_count,
                    'danmaku_count': danmaku_count,
                    'comment_count': comment_count,
                    'duration': duration,
                    'duration_seconds': duration_seconds,
                    'description': description[:100] + '...' if len(description) > 100 else description,
                    'url': f'https://www.bilibili.com/video/{bvid}',
                    'commented': t.is_commented(bvid),
                    'pic': pic_url,
                    'pubdate': pubdate
                })
                seen_bvids.add(bvid)
                page_videos += 1

            if page_result_count == 0:
                break

            page += 1

        # 本地二次排序（B站API排序不够精确时）
        if sort_by == 'duration_desc':
            videos.sort(key=lambda x: x.get('duration_seconds', 0), reverse=True)
        elif sort_by == 'duration_asc':
            videos.sort(key=lambda x: x.get('duration_seconds', 0))

        return jsonify({
            'success': True,
            'videos': videos,
            'filtered_count': filtered_count,
            'filtered_details': {
                'invalid_bv': filtered_invalid_bv,
                'low_play': filtered_low_play,
                'duration': filtered_duration,
                'commented': filtered_commented,
                'excluded': filtered_excluded,
                'blocked': filtered_blocked,
                'author': filtered_author
            },
            'total': len(videos) + filtered_count
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'搜索失败: {str(e)}'})

@app.route('/api/comment', methods=['POST'])
def send_comment():
    t = get_tool()
    if not t.credential:
        return jsonify({'success': False, 'message': '请先登录'})

    data = request.json
    bvid = data.get('bvid', '').strip()
    text = data.get('text', '').strip()
    use_variant = data.get('use_variant', True)

    if not bvid or not text:
        return jsonify({'success': False, 'message': '视频BV号和评论内容不能为空'})

    if len(text) > 1000:
        return jsonify({'success': False, 'message': '评论内容过长，最多1000字符'})

    # 应用内容变体
    original_text = text
    if use_variant:
        text = generate_comment_variant(text)

    if not is_valid_bvid(bvid):
        return jsonify({'success': False, 'message': 'BV号格式不正确，必须为BV开头的12位字符（如 BV1xx411x7h7）'})

    if t.is_commented(bvid, text):
        return jsonify({'success': False, 'message': '该视频已发送过相同的评论'})

    try:
        # 标准化bvid
        bvid = re.sub(r'(?i)\bbv', 'BV', bvid.strip())
        if len(bvid) > 12:
            bvid = bvid[:12]

        # 限流检查
        allowed, wait = comment_limiter.acquire()
        if not allowed and wait > 0:
            time.sleep(min(wait, 10))

        async def do_comment():
            # 获取视频 aid
            try:
                v = video.Video(bvid=bvid, credential=t.credential)
                aid = v.get_aid()
            except Exception as bv_err:
                return None, None, None, str(bv_err)

            if not aid:
                return None, None, None, '无法获取视频AID'

            # 获取视频标题和封面
            try:
                video_info = await v.get_info()
                title = video_info.get('title', '')
                pic = video_info.get('pic', '')
            except Exception:
                title = ''
                pic = ''

            # 使用 comment.send_comment 函数发送评论
            try:
                result = await comment.send_comment(
                    text=text,
                    oid=aid,
                    type_=CommentResourceType.VIDEO,
                    credential=t.credential
                )
                return result, title, pic, None
            except Exception as send_err:
                return None, title, pic, str(send_err)

        result, title, pic, error_msg = run_async(do_comment())

        if error_msg:
            return jsonify({'success': False, 'message': f'发送失败: {error_msg}'})

        # 分析结果
        success = False
        rpid = None
        message = ''

        if result is None:
            success = True
            message = '评论发送成功'
        elif isinstance(result, dict):
            code = result.get('code', -999)

            if code == 0:
                success = True
                message = '评论发送成功'
                rpid = result.get('data', {}).get('rpid')
            elif 'rpid' in result:
                success = True
                rpid = result.get('rpid')
                toast = result.get('success_toast', '')
                message = toast or '评论发送成功'
            elif 'success_toast' in result:
                success = True
                message = result['success_toast']
            else:
                data = result.get('data', {})
                if data and isinstance(data, dict) and 'rpid' in data:
                    success = True
                    rpid = data['rpid']
                    message = '评论发送成功'
                elif code == -1 and data:
                    success = True
                    message = '评论发送成功（非标准返回）'
                else:
                    error_messages = {
                        -101: '登录凭证已过期',
                        -400: '参数错误',
                        -403: '权限不足，可能被风控',
                        -412: '请求过于频繁',
                        12002: '评论区已关闭',
                        12009: '评论内容被过滤'
                    }
                    message = error_messages.get(code, f'错误码: {code}')
        else:
            success = True
            message = '评论发送成功'

        # 记录到历史（仅成功时，记录原始文本和封面）
        if success:
            t.add_to_history(bvid, original_text, title or '', pic)

        return jsonify({
            'success': success,
            'message': message,
            'rpid': rpid,
            'title': title or ''
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'发送失败: {str(e)}'})

@app.route('/api/reply', methods=['POST'])
def send_reply():
    """回复指定评论"""
    t = get_tool()
    if not t.credential:
        return jsonify({'success': False, 'message': '请先登录'})

    data = request.json
    bvid = data.get('bvid', '').strip()
    rpid = data.get('rpid', '').strip()
    text = data.get('text', '').strip()

    if not bvid or not rpid or not text:
        return jsonify({'success': False, 'message': 'BV号、评论ID和回复内容不能为空'})

    if not is_valid_bvid(bvid):
        return jsonify({'success': False, 'message': 'BV号格式不正确'})

    try:
        bvid = re.sub(r'(?i)\bbv', 'BV', bvid.strip())
        if len(bvid) > 12:
            bvid = bvid[:12]

        # 限流检查
        allowed, wait = comment_limiter.acquire()
        if not allowed and wait > 0:
            time.sleep(min(wait, 10))

        async def do_reply():
            v = video.Video(bvid=bvid, credential=t.credential)
            aid = v.get_aid()
            if not aid:
                return None, '无法获取视频AID'

            try:
                result = await comment.send_comment(
                    text=text,
                    oid=aid,
                    type_=CommentResourceType.VIDEO,
                    root=int(rpid),
                    credential=t.credential
                )
                return result, None
            except Exception as e:
                return None, str(e)

        result, error_msg = run_async(do_reply())

        if error_msg:
            return jsonify({'success': False, 'message': f'回复失败: {error_msg}'})

        success = False
        message = ''
        reply_rpid = None

        if isinstance(result, dict):
            code = result.get('code', -999)
            if code == 0:
                success = True
                message = '回复发送成功'
                reply_rpid = result.get('data', {}).get('rpid')
            elif 'rpid' in result:
                success = True
                reply_rpid = result.get('rpid')
                message = result.get('success_toast', '回复发送成功')
            else:
                error_messages = {
                    -101: '登录凭证已过期',
                    -400: '参数错误',
                    -403: '权限不足，可能被风控',
                    -412: '请求过于频繁',
                    12002: '评论区已关闭',
                    12009: '评论内容被过滤'
                }
                message = error_messages.get(code, f'错误码: {code}')
        else:
            success = True
            message = '回复发送成功'

        return jsonify({'success': success, 'message': message, 'rpid': reply_rpid})
    except Exception as e:
        return jsonify({'success': False, 'message': f'回复失败: {str(e)}'})

# ========== 模板管理 API ==========

@app.route('/api/templates')
def get_templates():
    return jsonify({'success': True, 'templates': get_tool().comment_templates})

@app.route('/api/templates/add', methods=['POST'])
def add_template():
    t = get_tool()
    data = request.json
    text = data.get('text', '').strip()
    if not text:
        return jsonify({'success': False, 'message': '模板内容不能为空'})
    if t.add_template(text):
        return jsonify({'success': True, 'message': '添加成功', 'templates': t.comment_templates})
    return jsonify({'success': False, 'message': '模板已存在'})

@app.route('/api/templates/delete', methods=['POST'])
def delete_template():
    t = get_tool()
    data = request.json
    index = data.get('index', -1)
    if t.delete_template(index):
        return jsonify({'success': True, 'message': '删除成功', 'templates': t.comment_templates})
    return jsonify({'success': False, 'message': '删除失败，索引无效'})

# ========== 排除视频管理 API ==========

@app.route('/api/excluded')
def get_excluded():
    """获取排除的视频列表（包含标题）"""
    return jsonify({'success': True, 'excluded_list': get_tool().excluded_bvids})

@app.route('/api/excluded/save', methods=['POST'])
def save_excluded():
    """保存排除的视频列表"""
    t = get_tool()
    data = request.json
    excluded_list = data.get('excluded_list', [])
    if not isinstance(excluded_list, list):
        return jsonify({'success': False, 'message': '参数错误'})
    t.excluded_bvids = excluded_list
    t._rebuild_excluded_index()
    t.save_config()
    return jsonify({'success': True, 'message': '保存成功'})

# ========== 屏蔽词管理 API ==========

@app.route('/api/blocked_words')
def get_blocked_words():
    """获取屏蔽词列表"""
    return jsonify({'success': True, 'blocked_words': list(get_tool().blocked_words)})

@app.route('/api/blocked_words/add', methods=['POST'])
def add_blocked_word():
    """添加屏蔽词"""
    t = get_tool()
    data = request.json
    word = data.get('word', '').strip()
    if not word:
        return jsonify({'success': False, 'message': '屏蔽词不能为空'})
    if word in t.blocked_words:
        return jsonify({'success': False, 'message': '该屏蔽词已存在'})
    t.blocked_words.add(word)
    t.save_config()
    return jsonify({'success': True, 'message': '添加成功', 'blocked_words': list(t.blocked_words)})

@app.route('/api/blocked_words/delete', methods=['POST'])
def delete_blocked_word():
    """删除屏蔽词"""
    t = get_tool()
    data = request.json
    word = data.get('word', '').strip()
    if word in t.blocked_words:
        t.blocked_words.discard(word)
        t.save_config()
        return jsonify({'success': True, 'message': '删除成功', 'blocked_words': list(t.blocked_words)})
    return jsonify({'success': False, 'message': '屏蔽词不存在'})

@app.route('/api/blocked_words/clear_all', methods=['POST'])
def clear_all_blocked_words():
    """清空所有屏蔽词"""
    t = get_tool()
    t.blocked_words.clear()
    t.save_config()
    return jsonify({'success': True, 'message': '已清空所有屏蔽词'})

# ========== 黑名单UP主管理 API ==========

@app.route('/api/blocked_authors')
def get_blocked_authors():
    """获取黑名单UP主列表"""
    return jsonify({'success': True, 'blocked_authors': list(get_tool().blocked_authors)})

@app.route('/api/blocked_authors/add', methods=['POST'])
def add_blocked_author():
    """添加黑名单UP主"""
    t = get_tool()
    data = request.json
    author = data.get('author', '').strip()
    if not author:
        return jsonify({'success': False, 'message': 'UP主名称不能为空'})
    if author in t.blocked_authors:
        return jsonify({'success': False, 'message': '该UP主已在黑名单中'})
    t.blocked_authors.add(author)
    t.save_config()
    return jsonify({'success': True, 'message': '添加成功', 'blocked_authors': list(t.blocked_authors)})

@app.route('/api/blocked_authors/delete', methods=['POST'])
def delete_blocked_author():
    """删除黑名单UP主"""
    t = get_tool()
    data = request.json
    author = data.get('author', '').strip()
    if author in t.blocked_authors:
        t.blocked_authors.discard(author)
        t.save_config()
        return jsonify({'success': True, 'message': '删除成功', 'blocked_authors': list(t.blocked_authors)})
    return jsonify({'success': False, 'message': 'UP主不在黑名单中'})

@app.route('/api/blocked_authors/clear_all', methods=['POST'])
def clear_all_blocked_authors():
    """清空所有黑名单UP主"""
    t = get_tool()
    t.blocked_authors.clear()
    t.save_config()
    return jsonify({'success': True, 'message': '已清空所有黑名单UP主'})

# ========== 异步批量评论 ==========

@app.route('/api/batch_comment', methods=['POST'])
def batch_comment():
    t = get_tool()
    if not t.credential:
        return jsonify({'success': False, 'message': '请先登录'})

    data = request.json
    videos = data.get('videos', [])
    text = data.get('text', '').strip()
    use_template = data.get('use_template', False)
    use_variant = data.get('use_variant', True)  # 默认开启变体
    delay = max(float(data.get('delay', 5)), 1)

    # 如果使用模板，检查是否有模板
    if use_template:
        if not t.comment_templates:
            return jsonify({'success': False, 'message': '模板库为空，请先添加模板'})
    elif not text:
        return jsonify({'success': False, 'message': '请输入评论内容或选择使用模板'})

    if not videos:
        return jsonify({'success': False, 'message': '请选择至少一个视频'})

    if len(videos) > 100:
        return jsonify({'success': False, 'message': '单次批量最多100个视频'})

    if len(text) > 1000:
        return jsonify({'success': False, 'message': '评论内容过长，最多1000字符'})

    # Validate all BVids
    for v in videos:
        bvid = v.get('bvid', '').strip()
        if not is_valid_bvid(bvid):
            return jsonify({'success': False, 'message': 'BV号格式不正确: %s' % bvid})

    # 创建任务ID，立即返回
    task_id = str(uuid.uuid4())[:8]
    bt = get_batch_tasks()
    bt[task_id] = {
        'status': 'running',
        'total': len(videos),
        'current': 0,
        'current_title': '',
        'results': [],
        'success_count': 0,
        'fail_count': 0,
        'skip_count': 0,
        'finished': False
    }

    # 捕获当前session的tool引用
    tool_ref = t

    # 在后台线程执行批量评论
    def run_batch():
        task = bt[task_id]
        for i, video_data in enumerate(videos):
            bvid = video_data.get('bvid', '').strip()
            bvid = re.sub(r'(?i)\bbv', 'BV', bvid.strip())
            if len(bvid) > 12:
                bvid = bvid[:12]
            title = video_data.get('title', '')
            task['current'] = i + 1
            task['current_title'] = title

            # 确定评论内容
            if use_template:
                comment_text = tool_ref.get_random_template()
                if not comment_text:
                    task['results'].append({
                        'bvid': bvid, 'title': title,
                        'status': 'failed', 'message': '模板库为空'
                    })
                    task['fail_count'] += 1
                    continue
            else:
                comment_text = text

            # 应用内容变体（避免重复被风控）
            original_text = comment_text
            if use_variant:
                comment_text = generate_comment_variant(comment_text)

            # Check if already commented
            if tool_ref.is_commented(bvid, comment_text):
                task['results'].append({
                    'bvid': bvid, 'title': title,
                    'status': 'skipped', 'message': '已发送过相同评论'
                })
                task['skip_count'] += 1
                continue

            # Define async comment function
            async def do_comment(bvid, comment_text):
                # 评论前限流检查
                allowed, wait = comment_limiter.acquire()
                if not allowed and wait > 0:
                    time.sleep(min(wait, 10))

                try:
                    v = video.Video(bvid=bvid, credential=tool_ref.credential)
                    aid = v.get_aid()
                except Exception as bv_err:
                    return {'error': '获取视频信息失败: %s' % str(bv_err)}, None, None

                if not aid:
                    return {'error': '无法获取视频AID'}, None, None

                try:
                    video_info = await v.get_info()
                    vtitle = video_info.get('title', '')
                except Exception:
                    vtitle = ''

                try:
                    result = await comment.send_comment(
                        text=comment_text,
                        oid=aid,
                        type_=CommentResourceType.VIDEO,
                        credential=tool_ref.credential
                    )
                    return result, vtitle, None
                except Exception as send_err:
                    return {'error': str(send_err)}, vtitle, None

            try:
                result, vtitle, _ = run_async(do_comment(bvid, comment_text))

                if isinstance(result, dict) and 'error' in result:
                    task['results'].append({
                        'bvid': bvid, 'title': vtitle or title,
                        'status': 'failed', 'message': result['error']
                    })
                    task['fail_count'] += 1
                    continue

                # Analyze result
                success = False
                if result is None:
                    success = True
                elif isinstance(result, dict):
                    code = result.get('code', -999)
                    if code == 0 or 'rpid' in result or 'success_toast' in result:
                        success = True
                    else:
                        rdata = result.get('data', {})
                        if rdata and isinstance(rdata, dict) and 'rpid' in rdata:
                            success = True
                        elif code == -1 and rdata:
                            success = True

                if success:
                    pic = video_data.get('pic', '')
                    tool_ref.add_to_history(bvid, original_text, vtitle or title, pic)
                    task['results'].append({
                        'bvid': bvid, 'title': vtitle or title,
                        'status': 'success', 'message': '发送成功',
                        'comment_used': comment_text if use_variant else None
                    })
                    task['success_count'] += 1
                else:
                    task['results'].append({
                        'bvid': bvid, 'title': vtitle or title,
                        'status': 'failed',
                        'message': '错误码: %s' % str(result.get('code', 'unknown'))
                    })
                    task['fail_count'] += 1
            except Exception as e:
                task['results'].append({
                    'bvid': bvid, 'title': title,
                    'status': 'failed', 'message': str(e)
                })
                task['fail_count'] += 1

            # Delay between comments（随机化 ±30%）
            if i < len(videos) - 1:
                jitter = delay * random.uniform(0.7, 1.3)
                time.sleep(max(jitter, 1))

        task['status'] = 'finished'
        task['finished'] = True
        task['current_title'] = ''
        task['finished_at'] = time.time()

        # 清理超过1小时的已完成任务
        cleanup_ids = []
        with sessions_lock:
            for tid, tk in bt.items():
                if tk.get('finished') and time.time() - tk.get('finished_at', 0) > 3600:
                    cleanup_ids.append(tid)
            for tid in cleanup_ids:
                del bt[tid]

    thread = threading.Thread(target=run_batch, daemon=True)
    thread.start()

    return jsonify({'success': True, 'task_id': task_id})

@app.route('/api/batch_progress/<task_id>')
def batch_progress(task_id):
    bt = get_batch_tasks()
    task = bt.get(task_id)
    if not task:
        return jsonify({'success': False, 'message': '任务不存在'})
    return jsonify({
        'success': True,
        'status': task['status'],
        'total': task['total'],
        'current': task['current'],
        'current_title': task['current_title'],
        'results': task['results'],
        'success_count': task['success_count'],
        'fail_count': task['fail_count'],
        'skip_count': task['skip_count'],
        'finished': task['finished']
    })
@app.route('/api/history')
def get_history():
    t = get_tool()
    # 支持搜索和筛选参数
    keyword = request.args.get('keyword', '').strip().lower()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    history_list = []
    for bvid, records in t.comment_history.items():
        if not records:
            continue

        title = records[0].get('title', '')

        # 关键词搜索（匹配标题、BV号、评论内容）
        if keyword:
            match = False
            if keyword in title.lower() or keyword in bvid.lower():
                match = True
            if not match:
                for r in records:
                    if keyword in r.get('comment', '').lower():
                        match = True
                        break
            if not match:
                continue

        # 日期筛选
        if date_from or date_to:
            latest_time = records[0].get('time', '')
            if date_from and latest_time < date_from:
                continue
            if date_to and latest_time > date_to + ' 23:59:59':
                continue

        history_list.append({
            'bvid': bvid,
            'title': title,
            'count': len(records),
            'records': records
        })
    # 按最新评论时间排序
    history_list.sort(key=lambda x: x['records'][0].get('time', '') if x['records'] else '', reverse=True)
    return jsonify({'success': True, 'history': history_list})

@app.route('/api/history/delete', methods=['POST'])
def delete_history():
    t = get_tool()
    data = request.json
    bvid = data.get('bvid', '').strip()
    comment_text = data.get('comment', '').strip()

    if not bvid:
        return jsonify({'success': False, 'message': 'BV号不能为空'})

    if bvid in t.comment_history:
        if comment_text:
            # 只删除特定评论记录
            t.comment_history[bvid] = [
                r for r in t.comment_history[bvid]
                if r.get('comment') != comment_text
            ]
            if not t.comment_history[bvid]:
                del t.comment_history[bvid]
        else:
            # 删除该视频的所有记录
            del t.comment_history[bvid]
        t.save_history()
        return jsonify({'success': True, 'message': '删除成功'})
    else:
        return jsonify({'success': False, 'message': '未找到该视频的记录'})

@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    t = get_tool()
    t.comment_history = {}
    t.save_history()
    return jsonify({'success': True, 'message': '历史记录已清空'})

# ========== 启动服务器 ==========
if __name__ == '__main__':
    import webbrowser
    import threading as _threading

    def _open_browser():
        """延迟1.5秒后自动打开浏览器"""
        import time as _time
        _time.sleep(1.5)
        webbrowser.open('http://localhost:5000')

    print('=' * 50)
    print('   B站视频搜索和评论工具 - Web版')
    print('=' * 50)
    print('   启动服务器...')
    print('   浏览器将自动打开 http://localhost:5000')
    print('=' * 50)

    # 在后台线程中打开浏览器
    _threading.Thread(target=_open_browser, daemon=True).start()

    app.run(host='127.0.0.1', port=5000, debug=False)
