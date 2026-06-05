#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站视频搜索和评论工具 v2
基于 bilibili-api-python 库实现
支持Windows控制台中文显示
"""

import asyncio
import json
import os
import sys
import time
import io
from typing import List, Dict, Optional
from dataclasses import dataclass

# 设置控制台编码为UTF-8（Windows兼容）
if sys.platform == 'win32':
    # 设置标准输出编码
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    # 尝试设置控制台代码页
    try:
        os.system('chcp 65001 >nul 2>&1')
    except:
        pass

import re
from bilibili_api import search, comment, Credential, video
from bilibili_api.search import SearchObjectType, OrderVideo
from bilibili_api.comment import CommentResourceType, OrderType

# BV号格式校验：必须以BV开头，12位字母数字（大小写敏感）
BV_PATTERN = re.compile(r'^BV[a-zA-Z0-9]{10}$')


@dataclass
class BilibiliVideo:
    """B站视频数据类"""
    bvid: str
    title: str
    author: str
    play_count: int
    danmaku_count: int
    duration: str
    description: str
    url: str


class BilibiliCommentTool:
    """B站视频搜索和评论工具"""
    
    def __init__(self):
        self.credential: Optional[Credential] = None
        self.config_file = "config.json"
        self.history_file = "comment_history.json"
        self.comment_history: Dict[str, List[Dict]] = {}  # {bvid: [{time, comment}]}
        self.load_config()
        self.load_history()
    
    def load_config(self):
        """加载配置文件"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    if config.get('sessdata') and config.get('bili_jct'):
                        self.credential = Credential(
                            sessdata=config['sessdata'],
                            bili_jct=config['bili_jct'],
                            buvid3=config.get('buvid3', '')
                        )
                        print("[OK] 已加载保存的登录凭证")
            except Exception as e:
                print(f"[警告] 加载配置文件失败: {e}")
    
    def save_config(self):
        """保存配置文件"""
        if self.credential:
            config = {
                'sessdata': self.credential.sessdata,
                'bili_jct': self.credential.bili_jct,
                'buvid3': self.credential.buvid3
            }
            try:
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
                print("[OK] 登录凭证已保存")
            except Exception as e:
                print(f"[警告] 保存配置文件失败: {e}")
    
    def load_history(self):
        """加载评论历史记录"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    self.comment_history = json.load(f)
                print(f"[OK] 已加载评论历史记录 ({len(self.comment_history)} 个视频)")
            except Exception as e:
                print(f"[警告] 加载历史记录失败: {e}")
                self.comment_history = {}
    
    def save_history(self):
        """保存评论历史记录"""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.comment_history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[警告] 保存历史记录失败: {e}")
    
    def is_commented(self, bvid: str, comment_text: str = None) -> bool:
        """检查视频是否已评论过
        
        Args:
            bvid: 视频BV号
            comment_text: 评论内容，如果提供则检查是否发送过相同内容的评论
            
        Returns:
            bool: 是否已评论过
        """
        if bvid not in self.comment_history:
            return False
        
        if comment_text:
            # 检查是否发送过相同内容的评论
            for record in self.comment_history[bvid]:
                if record.get('comment') == comment_text:
                    return True
            return False
        
        # 如果不指定评论内容，只要评论过就返回True
        return len(self.comment_history[bvid]) > 0
    
    def add_to_history(self, bvid: str, comment_text: str, title: str = ""):
        """添加评论记录到历史
        
        Args:
            bvid: 视频BV号
            comment_text: 评论内容
            title: 视频标题
        """
        if bvid not in self.comment_history:
            self.comment_history[bvid] = []
        
        record = {
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'comment': comment_text,
            'title': title
        }
        self.comment_history[bvid].append(record)
        self.save_history()
    
    def get_history_count(self, bvid: str) -> int:
        """获取视频的评论次数"""
        return len(self.comment_history.get(bvid, []))
    
    def setup_credential(self):
        """设置登录凭证"""
        print("\n" + "="*50)
        print("[设置] B站登录凭证")
        print("="*50)
        print("请从浏览器Cookie中获取以下信息：")
        print("1. 打开B站并登录")
        print("2. 按F12打开开发者工具")
        print("3. 切换到Application/存储 -> Cookies")
        print("4. 找到以下Cookie值：")
        print()
        
        sessdata = input("请输入 SESSDATA: ").strip()
        bili_jct = input("请输入 bili_jct: ").strip()
        buvid3 = input("请输入 buvid3 (可选，直接回车跳过): ").strip()
        
        if not sessdata or not bili_jct:
            print("[错误] SESSDATA 和 bili_jct 不能为空")
            return False
        
        self.credential = Credential(
            sessdata=sessdata,
            bili_jct=bili_jct,
            buvid3=buvid3 if buvid3 else ""
        )
        
        # 保存配置
        self.save_config()
        return True
    
    async def search_videos(self, keyword: str, target_count: int = 20, 
                            filter_commented: bool = False) -> tuple[List[BilibiliVideo], int]:
        """搜索B站视频，自动翻页直到凑够目标数量
        
        Args:
            keyword: 搜索关键词
            target_count: 目标视频数量（过滤的视频不占额度）
            filter_commented: 是否过滤已评论的视频
            
        Returns:
            tuple: (视频列表, 被过滤的总数)
        """
        try:
            print(f"\n[搜索] 正在搜索: {keyword}")
            print(f"[设置] 目标数量: {target_count}，过滤已评论: {filter_commented}")
            
            videos = []
            filtered_count = 0
            page = 1
            page_size = 50  # 每次搜索50个，减少翻页次数
            
            while len(videos) < target_count:
                print(f"[搜索] 正在搜索第 {page} 页...")
                
                result = await search.search_by_type(
                    keyword=keyword,
                    search_type=SearchObjectType.VIDEO,
                    order_type=OrderVideo.TOTALRANK,
                    page=page,
                    page_size=page_size
                )
                
                # 没有更多结果了
                if 'result' not in result or not result['result']:
                    print(f"[搜索] 没有更多结果了")
                    break
                
                page_videos = 0
                for item in result['result']:
                    # 提取视频信息
                    bvid = item.get('bvid', '').strip()
                    
                    # 过滤掉 BV 号格式不正确的视频
                    if not BV_PATTERN.match(bvid):
                        filtered_count += 1
                        continue
                    
                    title = item.get('title', '').replace('<em class="keyword">', '').replace('</em>', '')
                    author = item.get('author', '')
                    play_count = item.get('play', 0)
                    danmaku_count = item.get('video_review', 0)
                    duration = item.get('duration', '')
                    description = item.get('description', '')
                    
                    # 如果开启过滤，跳过已评论的视频
                    if filter_commented and self.is_commented(bvid):
                        filtered_count += 1
                        continue
                    
                    # 已经够了
                    if len(videos) >= target_count:
                        break
                    
                    video_url = f"https://www.bilibili.com/video/{bvid}"
                    
                    video_obj = BilibiliVideo(
                        bvid=bvid,
                        title=title,
                        author=author,
                        play_count=play_count,
                        danmaku_count=danmaku_count,
                        duration=duration,
                        description=description[:100] + "..." if len(description) > 100 else description,
                        url=video_url
                    )
                    videos.append(video_obj)
                    page_videos += 1
                
                print(f"[搜索] 第 {page} 页找到 {page_videos} 个有效视频，累计 {len(videos)} 个")
                
                # 如果这一页没有有效视频，可能没有更多结果了
                if page_videos == 0:
                    print(f"[搜索] 当前页没有有效视频，停止搜索")
                    break
                
                page += 1
                
                # 防止无限循环，最多搜索10页
                if page > 10:
                    print(f"[搜索] 已搜索10页，停止搜索")
                    break
            
            print(f"[完成] 共找到 {len(videos)} 个视频，过滤了 {filtered_count} 个")
            return videos, filtered_count
            
        except Exception as e:
            print(f"[错误] 搜索失败: {e}")
            return videos, filtered_count
    
    def display_videos(self, videos: List[BilibiliVideo], filtered_count: int = 0):
        """显示搜索结果"""
        if not videos:
            print("[提示] 没有找到相关视频")
            return
        
        print(f"\n[结果] 找到 {len(videos)} 个视频：")
        if filtered_count > 0:
            print(f"[过滤] 已自动过滤 {filtered_count} 个已评论过的视频")
        print("-" * 80)
        
        for i, video in enumerate(videos, 1):
            print(f"{i:2d}. {video.title}")
            print(f"    UP主: {video.author}")
            print(f"    播放: {video.play_count:,} | 弹幕: {video.danmaku_count:,}")
            print(f"    时长: {video.duration}")
            print(f"    链接: {video.url}")
            if video.description:
                print(f"    简介: {video.description}")
            print("-" * 80)
    
    async def send_comment(self, bvid: str, text: str) -> bool:
        """发送评论到指定视频"""
        if not self.credential:
            print("[错误] 请先设置登录凭证")
            return False
        
        # 检查是否已评论过相同内容
        if self.is_commented(bvid, text):
            print(f"[跳过] 该视频已发送过相同的评论，跳过")
            return False
        
        try:
            # 获取视频对象（使用Video内置方法发送评论，避免aid错误）
            print(f"\n[获取] 正在获取视频信息: {bvid}")
            v = video.Video(bvid=bvid, credential=self.credential)
            
            # 先获取视频标题
            try:
                video_info = await v.get_info()
                title = video_info.get('title', '')
            except:
                title = ''
            
            print(f"[评论] 正在发送评论到: {title or bvid}")
            
            # 获取视频的aid（必须用aid发送评论，bvid不行）
            try:
                aid = v.get_aid()
            except Exception as aid_err:
                print(f"[错误] 获取视频AID失败: {aid_err}")
                return False

            if not aid:
                print(f"[错误] 无法获取视频AID")
                return False

            print(f"[评论] 正在发送评论到: {title or bvid} (AID: {aid})")

            # 使用 comment.send_comment 函数发送评论
            try:
                result = await comment.send_comment(
                    text=text,
                    oid=aid,
                    type_=CommentResourceType.VIDEO,
                    credential=self.credential
                )
            except Exception as send_err:
                print(f"[错误] 发送失败: {send_err}")
                return False
            
            # B站API返回格式分析：
            # 成功时可能返回:
            #   1. None
            #   2. {"code": 0, "message": "0", "data": {...}}
            #   3. {"code": -1, "message": "...", "data": {"rpid": ...}} (实际成功)
            # 失败时返回:
            #   {"code": 错误码, "message": "错误信息"}
            
            success = False
            if result is None:
                # 返回None表示成功
                print("[成功] 评论发送成功！")
                success = True
            elif isinstance(result, dict):
                code = result.get('code', -1)
                message = result.get('message', '未知错误')
                data = result.get('data', {})
                
                # 判断成功的条件：
                # 1. code == 0
                # 2. 顶层有 rpid 字段（评论ID）- B站新API格式
                # 3. 有 success_toast 字段
                # 4. 或者有 data 字段且包含 rpid
                if code == 0:
                    print("[成功] 评论发送成功！")
                    success = True
                elif 'rpid' in result:
                    # 顶层有rpid，说明评论成功（B站新API返回格式）
                    rpid = result.get('rpid')
                    toast = result.get('success_toast', '')
                    print(f"[成功] 评论发送成功！（评论ID: {rpid}）")
                    if toast:
                        print(f"[提示] {toast}")
                    success = True
                elif 'success_toast' in result:
                    # 有成功提示
                    print(f"[成功] {result['success_toast']}")
                    success = True
                elif data and isinstance(data, dict) and 'rpid' in data:
                    # data内有评论ID
                    print(f"[成功] 评论发送成功！（评论ID: {data['rpid']}）")
                    success = True
                elif code == -1 and data:
                    # code=-1 但有数据，可能是成功的非标准返回
                    print("[成功] 评论发送成功！（非标准返回格式）")
                    print(f"[信息] 返回数据: {data}")
                    success = True
                else:
                    # 真正的失败
                    print(f"[失败] 评论发送失败")
                    print(f"[错误码] code={code}")
                    print(f"[错误信息] {message}")
                    
                    # 提供常见错误的解决建议
                    if code == -101:
                        print("[建议] 登录凭证已过期，请重新获取SESSDATA和bili_jct")
                    elif code == -400:
                        print("[建议] 参数错误，请检查评论内容是否包含特殊字符")
                    elif code == -403:
                        print("[建议] 权限不足，可能被风控或账号受限")
                    elif code == -412:
                        print("[建议] 请求过于频繁，请增加发送间隔或稍后再试")
                    elif code == 12002:
                        print("[建议] 评论区已关闭，该视频可能禁止评论")
                    elif code == 12009:
                        print("[建议] 评论内容可能包含敏感词或被过滤")
                    else:
                        print(f"[建议] 未知错误，请查看B站官方错误码说明")
                    
                    success = False
            else:
                # 如果返回的不是字典，可能是成功（无返回值）
                print("[成功] 评论发送成功！")
                success = True
            
            # 成功后验证评论是否真的可见
            if success:
                rpid = None
                if isinstance(result, dict):
                    rpid = result.get('rpid') or result.get('data', {}).get('rpid')
                
                if rpid:
                    print(f"[验证] 正在验证评论是否可见...")
                    await asyncio.sleep(2)
                    
                    try:
                        from bilibili_api.comment import OrderType
                        # 用视频对象直接获取评论，避免aid错误
                        comments_data = await comment.get_comments(
                            oid=v.get_aid(),
                            type_=CommentResourceType.VIDEO,
                            page_index=1,
                            order=OrderType.TIME,
                            credential=self.credential
                        )
                        
                        replies = comments_data.get('replies', [])
                        if replies:
                            found = False
                            for reply in replies:
                                if reply.get('rpid') == rpid:
                                    found = True
                                    break
                            
                            if found:
                                print(f"[验证] 评论已确认可见！")
                            else:
                                print(f"[警告] 评论未在列表中找到")
                                print(f"[提示] 可能原因：")
                                print(f"   1. 评论正在审核中（UP主开启了评论审核）")
                                print(f"   2. 评论被系统过滤（包含敏感词或账号受限）")
                                print(f"[建议] 请手动打开视频页面确认")
                        else:
                            print(f"[警告] 无法获取评论列表进行验证")
                            print(f"[建议] 请手动打开视频页面确认评论是否显示")
                    except Exception as verify_err:
                        print(f"[警告] 验证失败: {verify_err}")
                        print(f"[建议] 请手动打开视频页面确认评论是否显示")
                
                self.add_to_history(bvid, text, title)
                print(f"[记录] 已记录到评论历史")
            
            return success
                
        except Exception as e:
            print(f"[错误] 发送评论时出错: {type(e).__name__}: {e}")
            
            # 提供常见异常的解决建议
            error_str = str(e).lower()
            if '412' in error_str or 'request' in error_str:
                print("[建议] 可能触发了风控，请等待一段时间后再试")
            elif 'login' in error_str or 'credential' in error_str:
                print("[建议] 登录凭证无效，请重新获取")
            elif 'timeout' in error_str:
                print("[建议] 网络超时，请检查网络连接")
            elif 'connection' in error_str:
                print("[建议] 网络连接失败，请检查网络")
            else:
                print("[建议] 请检查网络连接和登录凭证")
            
            return False
    
    async def batch_comment(self, videos: List[BilibiliVideo], comment_text: str, 
                           indices: List[int] = None, delay: float = 5.0):
        """批量发送评论"""
        if not self.credential:
            print("[错误] 请先设置登录凭证")
            return
        
        if indices is None:
            indices = list(range(len(videos)))
        
        success_count = 0
        fail_count = 0
        skip_count = 0
        
        print(f"\n[开始] 批量发送评论...")
        print(f"[内容] 评论内容: {comment_text}")
        print(f"[数量] 目标视频数: {len(indices)}")
        print(f"[间隔] 发送间隔: {delay}秒")
        print("-" * 50)
        
        # 先检查哪些视频已评论过
        already_commented = []
        for idx in indices:
            if 0 <= idx < len(videos):
                if self.is_commented(videos[idx].bvid, comment_text):
                    already_commented.append(idx)
        
        if already_commented:
            print(f"\n[提示] 以下 {len(already_commented)} 个视频已评论过相同内容，将自动跳过：")
            for idx in already_commented:
                print(f"  - {videos[idx].title}")
            print()
        
        for i, idx in enumerate(indices, 1):
            if idx < 0 or idx >= len(videos):
                print(f"[跳过] 无效索引: {idx}")
                continue
            
            video_obj = videos[idx]
            
            # 检查是否已评论过
            if self.is_commented(video_obj.bvid, comment_text):
                print(f"\n[{i}/{len(indices)}] [跳过] 已评论过: {video_obj.title}")
                skip_count += 1
                continue
            
            print(f"\n[{i}/{len(indices)}] 正在处理: {video_obj.title}")
            
            success = await self.send_comment(video_obj.bvid, comment_text)
            if success:
                success_count += 1
            else:
                fail_count += 1
            
            # 添加延迟，避免请求过于频繁
            if i < len(indices):
                print(f"[等待] 等待 {delay} 秒后继续...")
                await asyncio.sleep(delay)
        
        print("\n" + "="*50)
        print("[完成] 批量评论完成！")
        print(f"[成功] 成功: {success_count}")
        print(f"[失败] 失败: {fail_count}")
        print(f"[跳过] 跳过: {skip_count}")
        print("="*50)
    
    def get_user_selection(self, max_index: int) -> List[int]:
        """获取用户选择的视频索引"""
        while True:
            try:
                selection = input("\n请选择要评论的视频 (输入数字，多个用逗号分隔，输入'all'选择全部): ").strip()
                
                if selection.lower() == 'all':
                    return list(range(max_index))
                
                indices = []
                for s in selection.split(','):
                    s = s.strip()
                    if '-' in s:
                        # 处理范围选择，如 1-5
                        start, end = s.split('-')
                        start = int(start.strip()) - 1
                        end = int(end.strip()) - 1
                        if 0 <= start <= end < max_index:
                            indices.extend(range(start, end + 1))
                    else:
                        idx = int(s) - 1
                        if 0 <= idx < max_index:
                            indices.append(idx)
                        else:
                            print(f"[警告] 忽略无效索引: {s}")
                
                if indices:
                    return list(set(indices))  # 去重
                else:
                    print("[错误] 请选择至少一个有效的视频")
                    
            except ValueError:
                print("[错误] 输入格式错误，请重新输入")
    
    def display_history(self):
        """显示评论历史记录"""
        if not self.comment_history:
            print("\n[提示] 暂无评论历史记录")
            return
        
        print("\n" + "="*60)
        print("[历史] 评论历史记录")
        print("="*60)
        print(f"共 {len(self.comment_history)} 个视频被评论过")
        print("-" * 60)
        
        for bvid, records in self.comment_history.items():
            if records:
                title = records[0].get('title', '未知标题')
                print(f"\n视频: {title}")
                print(f"BV号: {bvid}")
                print(f"评论次数: {len(records)}")
                print("评论记录:")
                for r in records[-3:]:  # 只显示最近3条
                    print(f"  - [{r['time']}] {r['comment'][:50]}{'...' if len(r['comment']) > 50 else ''}")
                if len(records) > 3:
                    print(f"  ... 还有 {len(records) - 3} 条更早的记录")
        
        print("\n" + "="*60)
    
    def clear_history(self):
        """清空评论历史记录"""
        if not self.comment_history:
            print("\n[提示] 暂无评论历史记录")
            return
        
        confirm = input("\n[警告] 确定要清空所有评论历史记录吗？(输入 'yes' 确认): ").strip()
        if confirm.lower() == 'yes':
            self.comment_history = {}
            self.save_history()
            print("[完成] 评论历史记录已清空")
        else:
            print("[取消] 操作已取消")
    
    async def test_comment(self):
        """测试评论功能"""
        if not self.credential:
            print("[错误] 请先设置登录凭证")
            return
        
        print("\n" + "="*50)
        print("[测试] 评论功能测试")
        print("="*50)
        
        # 测试视频
        test_bvid = input("\n请输入要测试的视频BV号 (默认: BV1GJ411x7h7): ").strip()
        if not test_bvid:
            test_bvid = "BV1GJ411x7h7"
        
        print(f"\n[测试] 视频: {test_bvid}")
        
        try:
            # 获取视频信息
            print("[获取] 正在获取视频信息...")
            v = video.Video(bvid=test_bvid, credential=self.credential)
            video_info = await v.get_info()
            
            aid = video_info['aid']
            title = video_info.get('title', '未知标题')
            
            print(f"[OK] 视频信息获取成功")
            print(f"   标题: {title}")
            print(f"   AID: {aid}")
            
            # 测试评论
            test_comment_text = input(f"\n请输入测试评论内容 (默认: '测试评论'): ").strip()
            if not test_comment_text:
                test_comment_text = "测试评论"
            
            print(f"\n[发送] 正在发送评论: {test_comment_text}")
            
            # 发送评论
            result = await comment.send_comment(
                text=test_comment_text,
                oid=aid,
                type_=CommentResourceType.VIDEO,
                credential=self.credential
            )
            
            # 分析结果
            print("\n[结果] API返回:")
            print(f"   返回类型: {type(result)}")
            
            if result is None:
                print("   返回值: None")
                print("[成功] 评论发送成功！（返回None表示成功）")
            elif isinstance(result, dict):
                print(f"   返回内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                
                code = result.get('code', -999)
                message = result.get('message', '')
                
                # 判断成功：多种B站API返回格式
                if code == 0:
                    print("[成功] 评论发送成功！（code=0）")
                    self.add_to_history(test_bvid, test_comment_text, title)
                    print("[记录] 已保存到评论历史")
                elif 'rpid' in result:
                    # 顶层有rpid，B站新API格式
                    rpid = result.get('rpid')
                    toast = result.get('success_toast', '')
                    print(f"[成功] 评论发送成功！（评论ID: {rpid}）")
                    if toast:
                        print(f"[提示] {toast}")
                    self.add_to_history(test_bvid, test_comment_text, title)
                    print("[记录] 已保存到评论历史")
                elif 'success_toast' in result:
                    print(f"[成功] {result['success_toast']}")
                    self.add_to_history(test_bvid, test_comment_text, title)
                    print("[记录] 已保存到评论历史")
                else:
                    # 尝试获取data
                    data = result.get('data', {})
                    if data and isinstance(data, dict) and 'rpid' in data:
                        print(f"[成功] 评论发送成功！（评论ID: {data['rpid']}）")
                        self.add_to_history(test_bvid, test_comment_text, title)
                        print("[记录] 已保存到评论历史")
                    elif code == -1 and data:
                        print("[成功] 评论发送成功！（非标准返回格式）")
                        self.add_to_history(test_bvid, test_comment_text, title)
                        print("[记录] 已保存到评论历史")
                    else:
                        print(f"[失败] 评论发送失败")
                        print(f"   错误码: {code}")
                        print(f"   错误信息: {message}")
                        
                        # 提供解决建议
                        print("\n[诊断] 可能的原因:")
                        if code == -101:
                            print("   - 登录凭证已过期，请重新获取SESSDATA和bili_jct")
                        elif code == -400:
                            print("   - 参数错误，评论内容可能包含特殊字符")
                        elif code == -403:
                            print("   - 权限不足，账号可能被限制评论")
                        elif code == -412:
                            print("   - 请求过于频繁，触发了风控")
                        elif code == 12002:
                            print("   - 评论区已关闭，该视频禁止评论")
                        elif code == 12009:
                            print("   - 评论内容被过滤，可能包含敏感词")
                        else:
                            print(f"   - 未知错误码: {code}")
            else:
                print(f"   返回值: {result}")
                print("[成功] 评论发送成功！（非字典返回值）")
                
        except Exception as e:
            print(f"\n[错误] 发生异常: {type(e).__name__}: {e}")
            
            # 分析异常
            error_str = str(e).lower()
            print("\n[诊断] 可能的原因:")
            if '412' in error_str:
                print("   - 触发了B站风控，请求被拦截")
                print("   - 建议等待一段时间后再试")
            elif 'login' in error_str or 'credential' in error_str:
                print("   - 登录凭证无效或已过期")
                print("   - 建议重新获取SESSDATA和bili_jct")
            elif 'timeout' in error_str:
                print("   - 网络连接超时")
                print("   - 建议检查网络连接")
            elif 'connection' in error_str:
                print("   - 网络连接失败")
                print("   - 建议检查网络设置或使用代理")
            elif 'not found' in error_str:
                print("   - 视频不存在或已被删除")
                print("   - 建议检查BV号是否正确")
            else:
                print(f"   - 未知错误: {e}")
                print("   - 建议检查网络连接和登录凭证")
        
        print("\n" + "="*50)
        print("[完成] 测试结束")
        print("="*50)
    
    async def run(self):
        """运行主程序"""
        print("\n" + "="*60)
        print("[工具] B站视频搜索和评论工具 v2")
        print("="*60)
        print("功能：根据关键词搜索B站视频，并发送评论")
        print("="*60)
        
        # 检查登录凭证
        if not self.credential:
            print("\n[提示] 首次使用需要设置B站登录凭证")
            if not self.setup_credential():
                print("[错误] 设置凭证失败，程序退出")
                return
        
        while True:
            print("\n" + "-"*40)
            print("[菜单] 主菜单：")
            print("1. 搜索视频并评论")
            print("2. 查看评论历史")
            print("3. 清空评论历史")
            print("4. 重新设置登录凭证")
            print("5. 测试评论功能")
            print("6. 退出程序")
            print("-"*40)
            
            choice = input("请选择操作 (1-6): ").strip()
            
            if choice == '1':
                await self.search_and_comment()
            elif choice == '2':
                self.display_history()
            elif choice == '3':
                self.clear_history()
            elif choice == '4':
                self.setup_credential()
            elif choice == '5':
                await self.test_comment()
            elif choice == '6':
                print("\n[退出] 感谢使用，再见！")
                break
            else:
                print("[错误] 无效选择，请重新输入")
    
    async def search_and_comment(self):
        """搜索视频并评论的完整流程"""
        # 获取搜索关键词
        keyword = input("\n[搜索] 请输入搜索关键词: ").strip()
        if not keyword:
            print("[错误] 关键词不能为空")
            return
        
        # 获取搜索数量（过滤的视频不占额度，会一直找够数量）
        try:
            target_count = int(input("[数量] 请输入要检索的视频数量 (默认20): ").strip() or "20")
            target_count = max(target_count, 1)  # 至少1个
        except ValueError:
            target_count = 20
        
        # 询问是否过滤已评论视频
        filter_choice = input("[过滤] 是否过滤已评论过的视频？(Y/n，默认过滤): ").strip().lower()
        filter_commented = filter_choice != 'n'
        
        # 搜索视频（自动翻页直到凑够数量）
        videos, filtered_count = await self.search_videos(keyword, target_count=target_count, filter_commented=filter_commented)
        if not videos:
            print(f"[提示] 没有找到足够的视频（过滤了 {filtered_count} 个）")
            return
        
        # 显示搜索结果
        self.display_videos(videos, filtered_count)
        
        # 获取评论内容
        comment_text = input("\n[评论] 请输入要发送的评论内容: ").strip()
        if not comment_text:
            print("[错误] 评论内容不能为空")
            return
        
        # 获取用户选择
        indices = self.get_user_selection(len(videos))
        if not indices:
            print("[错误] 未选择任何视频")
            return
        
        # 确认操作
        print(f"\n[确认] 即将对 {len(indices)} 个视频发送评论：")
        for idx in indices:
            print(f"  - {videos[idx].title}")
        
        confirm = input("\n确认发送？(y/n): ").strip().lower()
        if confirm != 'y':
            print("[取消] 操作已取消")
            return
        
        # 获取发送间隔
        try:
            delay = float(input("[间隔] 请输入评论发送间隔秒数 (默认5秒): ").strip() or "5")
            delay = max(delay, 1.0)  # 最小1秒
        except ValueError:
            delay = 5.0
        
        # 批量发送评论
        await self.batch_comment(videos, comment_text, indices, delay)


async def main():
    """主函数"""
    tool = BilibiliCommentTool()
    await tool.run()


if __name__ == "__main__":
    # 运行主程序
    asyncio.run(main())