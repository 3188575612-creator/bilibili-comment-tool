#!/usr/bin/env python3
"""
B站视频搜索和评论工具
基于 bilibili-api-python 库实现
"""

import asyncio
import json
import os
import sys
import time
from typing import List, Dict, Optional
from dataclasses import dataclass

from bilibili_api import search, comment, Credential, video
from bilibili_api.search import SearchObjectType, OrderVideo
from bilibili_api.comment import CommentResourceType


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
                        print("✅ 已加载保存的登录凭证")
            except Exception as e:
                print(f"⚠️  加载配置文件失败: {e}")
    
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
                print("✅ 登录凭证已保存")
            except Exception as e:
                print(f"⚠️  保存配置文件失败: {e}")
    
    def load_history(self):
        """加载评论历史记录"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    self.comment_history = json.load(f)
                print(f"✅ 已加载评论历史记录 ({len(self.comment_history)} 个视频)")
            except Exception as e:
                print(f"⚠️  加载历史记录失败: {e}")
                self.comment_history = {}
    
    def save_history(self):
        """保存评论历史记录"""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.comment_history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️  保存历史记录失败: {e}")
    
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
        print("📝 设置B站登录凭证")
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
            print("❌ SESSDATA 和 bili_jct 不能为空")
            return False
        
        self.credential = Credential(
            sessdata=sessdata,
            bili_jct=bili_jct,
            buvid3=buvid3 if buvid3 else ""
        )
        
        # 保存配置
        self.save_config()
        return True
    
    async def search_videos(self, keyword: str, page: int = 1, page_size: int = 20) -> List[BilibiliVideo]:
        """搜索B站视频"""
        try:
            print(f"\n🔍 正在搜索: {keyword}")
            
            # 使用 search_by_type 进行视频搜索
            result = await search.search_by_type(
                keyword=keyword,
                search_type=SearchObjectType.VIDEO,
                order_type=OrderVideo.TOTALRANK,
                page=page,
                page_size=page_size
            )
            
            videos = []
            if 'result' in result:
                for item in result['result']:
                    # 提取视频信息
                    bvid = item.get('bvid', '')
                    title = item.get('title', '').replace('<em class="keyword">', '').replace('</em>', '')
                    author = item.get('author', '')
                    play_count = item.get('play', 0)
                    danmaku_count = item.get('video_review', 0)
                    duration = item.get('duration', '')
                    description = item.get('description', '')
                    
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
            
            return videos
            
        except Exception as e:
            print(f"❌ 搜索失败: {e}")
            return []
    
    def display_videos(self, videos: List[BilibiliVideo]):
        """显示搜索结果"""
        if not videos:
            print("📭 没有找到相关视频")
            return
        
        print(f"\n📋 找到 {len(videos)} 个视频：")
        print("-" * 80)
        
        for i, video in enumerate(videos, 1):
            print(f"{i:2d}. {video.title}")
            print(f"    👤 UP主: {video.author}")
            print(f"    ▶️  播放: {video.play_count:,} | 💬 弹幕: {video.danmaku_count:,}")
            print(f"    ⏱️  时长: {video.duration}")
            print(f"    🔗 链接: {video.url}")
            if video.description:
                print(f"    📝 简介: {video.description}")
            print("-" * 80)
    
    async def send_comment(self, bvid: str, text: str) -> bool:
        """发送评论到指定视频"""
        if not self.credential:
            print("❌ 请先设置登录凭证")
            return False
        
        # 检查是否已评论过相同内容
        if self.is_commented(bvid, text):
            print(f"⏭️  该视频已发送过相同的评论，跳过")
            return False
        
        try:
            # 获取视频信息
            print(f"\n🔍 正在获取视频信息: {bvid}")
            v = video.Video(bvid=bvid, credential=self.credential)
            video_info = await v.get_info()
            aid = video_info['aid']
            title = video_info.get('title', '')
            
            print(f"💬 正在发送评论到: {title}")
            print(f"📊 视频AID: {aid}")
            
            # 发送评论
            result = await comment.send_comment(
                text=text,
                oid=aid,
                type_=CommentResourceType.VIDEO,
                credential=self.credential
            )
            
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
                print("✅ 评论发送成功！")
                success = True
            elif isinstance(result, dict):
                code = result.get('code', -1)
                message = result.get('message', '未知错误')
                data = result.get('data', {})
                
                # 判断成功的条件：
                # 1. code == 0
                # 2. 或者有 data 字段且包含 rpid（评论ID）
                if code == 0:
                    print("✅ 评论发送成功！")
                    success = True
                elif data and isinstance(data, dict) and 'rpid' in data:
                    # 有评论ID说明评论已成功发送
                    print(f"✅ 评论发送成功！（评论ID: {data['rpid']}）")
                    success = True
                elif code == -1 and data:
                    # code=-1 但有数据，可能是成功的非标准返回
                    print("✅ 评论发送成功！（非标准返回格式）")
                    print(f"📊 返回数据: {data}")
                    success = True
                else:
                    # 真正的失败
                    print(f"❌ 评论发送失败")
                    print(f"   错误码: code={code}")
                    print(f"   错误信息: {message}")
                    
                    # 提供常见错误的解决建议
                    if code == -101:
                        print("💡 建议: 登录凭证已过期，请重新获取SESSDATA和bili_jct")
                    elif code == -400:
                        print("💡 建议: 参数错误，请检查评论内容是否包含特殊字符")
                    elif code == -403:
                        print("💡 建议: 权限不足，可能被风控或账号受限")
                    elif code == -412:
                        print("💡 建议: 请求过于频繁，请增加发送间隔或稍后再试")
                    elif code == 12002:
                        print("💡 建议: 评论区已关闭，该视频可能禁止评论")
                    elif code == 12009:
                        print("💡 建议: 评论内容可能包含敏感词或被过滤")
                    else:
                        print(f"💡 建议: 未知错误，请查看B站官方错误码说明")
                    
                    success = False
            else:
                # 如果返回的不是字典，可能是成功（无返回值）
                print("✅ 评论发送成功！")
                success = True
            
            # 成功后记录到历史
            if success:
                self.add_to_history(bvid, text, title)
                print(f"📝 已记录到评论历史")
            
            return success
                
        except Exception as e:
            print(f"❌ 发送评论时出错: {type(e).__name__}: {e}")
            
            # 提供常见异常的解决建议
            error_str = str(e).lower()
            if '412' in error_str or 'request' in error_str:
                print("💡 建议: 可能触发了风控，请等待一段时间后再试")
            elif 'login' in error_str or 'credential' in error_str:
                print("💡 建议: 登录凭证无效，请重新获取")
            elif 'timeout' in error_str:
                print("💡 建议: 网络超时，请检查网络连接")
            elif 'connection' in error_str:
                print("💡 建议: 网络连接失败，请检查网络")
            else:
                print("💡 建议: 请检查网络连接和登录凭证")
            
            return False
    
    async def batch_comment(self, videos: List[BilibiliVideo], comment_text: str, 
                           indices: List[int] = None, delay: float = 5.0):
        """批量发送评论"""
        if not self.credential:
            print("❌ 请先设置登录凭证")
            return
        
        if indices is None:
            indices = list(range(len(videos)))
        
        success_count = 0
        fail_count = 0
        skip_count = 0
        
        print(f"\n🚀 开始批量发送评论...")
        print(f"📝 评论内容: {comment_text}")
        print(f"📊 目标视频数: {len(indices)}")
        print(f"⏱️  发送间隔: {delay}秒")
        print("-" * 50)
        
        # 先检查哪些视频已评论过
        already_commented = []
        for idx in indices:
            if 0 <= idx < len(videos):
                if self.is_commented(videos[idx].bvid, comment_text):
                    already_commented.append(idx)
        
        if already_commented:
            print(f"\n⚠️  以下 {len(already_commented)} 个视频已评论过相同内容，将自动跳过：")
            for idx in already_commented:
                print(f"  - {videos[idx].title}")
            print()
        
        for i, idx in enumerate(indices, 1):
            if idx < 0 or idx >= len(videos):
                print(f"⚠️  跳过无效索引: {idx}")
                continue
            
            video = videos[idx]
            
            # 检查是否已评论过
            if self.is_commented(video.bvid, comment_text):
                print(f"\n[{i}/{len(indices)}] ⏭️  已评论过: {video.title}")
                skip_count += 1
                continue
            
            print(f"\n[{i}/{len(indices)}] 正在处理: {video.title}")
            
            success = await self.send_comment(video.bvid, comment_text)
            if success:
                success_count += 1
            else:
                fail_count += 1
            
            # 添加延迟，避免请求过于频繁
            if i < len(indices):
                print(f"⏳ 等待 {delay} 秒后继续...")
                await asyncio.sleep(delay)
        
        print("\n" + "="*50)
        print("📊 批量评论完成！")
        print(f"✅ 成功: {success_count}")
        print(f"❌ 失败: {fail_count}")
        print(f"⏭️  跳过: {skip_count}")
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
                            print(f"⚠️  忽略无效索引: {s}")
                
                if indices:
                    return list(set(indices))  # 去重
                else:
                    print("❌ 请选择至少一个有效的视频")
                    
            except ValueError:
                print("❌ 输入格式错误，请重新输入")
    
    def display_history(self):
        """显示评论历史记录"""
        if not self.comment_history:
            print("\n📭 暂无评论历史记录")
            return
        
        print("\n" + "="*60)
        print("📋 评论历史记录")
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
            print("\n📭 暂无评论历史记录")
            return
        
        confirm = input("\n⚠️  确定要清空所有评论历史记录吗？(输入 'yes' 确认): ").strip()
        if confirm.lower() == 'yes':
            self.comment_history = {}
            self.save_history()
            print("✅ 评论历史记录已清空")
        else:
            print("❌ 操作已取消")
    
    async def test_comment(self):
        """测试评论功能"""
        if not self.credential:
            print("❌ 请先设置登录凭证")
            return
        
        print("\n" + "="*50)
        print("🔧 评论功能测试")
        print("="*50)
        
        # 测试视频
        test_bvid = input("\n请输入要测试的视频BV号 (默认: BV1GJ411x7h7): ").strip()
        if not test_bvid:
            test_bvid = "BV1GJ411x7h7"
        
        print(f"\n📹 测试视频: {test_bvid}")
        
        try:
            # 获取视频信息
            print("🔍 正在获取视频信息...")
            v = video.Video(bvid=test_bvid, credential=self.credential)
            video_info = await v.get_info()
            
            aid = video_info['aid']
            title = video_info.get('title', '未知标题')
            
            print(f"✅ 视频信息获取成功")
            print(f"   标题: {title}")
            print(f"   AID: {aid}")
            
            # 测试评论
            test_comment_text = input(f"\n💬 请输入测试评论内容 (默认: '测试评论'): ").strip()
            if not test_comment_text:
                test_comment_text = "测试评论"
            
            print(f"\n📤 正在发送评论: {test_comment_text}")
            
            # 发送评论
            result = await comment.send_comment(
                text=test_comment_text,
                oid=aid,
                type_=CommentResourceType.VIDEO,
                credential=self.credential
            )
            
            # 分析结果
            print("\n📊 API返回:")
            print(f"   返回类型: {type(result)}")
            
            if result is None:
                print("   返回值: None")
                print("✅ 评论发送成功！（返回None表示成功）")
            elif isinstance(result, dict):
                print(f"   返回内容: {json.dumps(result, ensure_ascii=False, indent=2)}")
                
                code = result.get('code', -1)
                message = result.get('message', '未知错误')
                
                if code == 0:
                    print("✅ 评论发送成功！（code=0）")
                    
                    # 记录到历史
                    self.add_to_history(test_bvid, test_comment_text, title)
                    print("📝 已保存到评论历史")
                else:
                    print(f"❌ 评论发送失败")
                    print(f"   错误码: {code}")
                    print(f"   错误信息: {message}")
                    
                    # 提供解决建议
                    print("\n🔍 可能的原因:")
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
                print("✅ 评论发送成功！（非字典返回值）")
                
        except Exception as e:
            print(f"\n❌ 发生异常: {type(e).__name__}: {e}")
            
            # 分析异常
            error_str = str(e).lower()
            print("\n🔍 可能的原因:")
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
        print("🏁 测试结束")
        print("="*50)
    
    async def run(self):
        """运行主程序"""
        print("\n" + "="*60)
        print("🎬 B站视频搜索和评论工具")
        print("="*60)
        print("功能：根据关键词搜索B站视频，并发送评论")
        print("="*60)
        
        # 检查登录凭证
        if not self.credential:
            print("\n⚠️  首次使用需要设置B站登录凭证")
            if not self.setup_credential():
                print("❌ 设置凭证失败，程序退出")
                return
        
        while True:
            print("\n" + "-"*40)
            print("📋 主菜单：")
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
                print("\n👋 感谢使用，再见！")
                break
            else:
                print("❌ 无效选择，请重新输入")
    
    async def search_and_comment(self):
        """搜索视频并评论的完整流程"""
        # 获取搜索关键词
        keyword = input("\n🔍 请输入搜索关键词: ").strip()
        if not keyword:
            print("❌ 关键词不能为空")
            return
        
        # 获取搜索数量
        try:
            page_size = int(input("📊 请输入要显示的视频数量 (默认20): ").strip() or "20")
            page_size = min(max(page_size, 1), 50)  # 限制在1-50之间
        except ValueError:
            page_size = 20
        
        # 搜索视频
        videos = await self.search_videos(keyword, page_size=page_size)
        if not videos:
            return
        
        # 显示搜索结果
        self.display_videos(videos)
        
        # 获取评论内容
        comment_text = input("\n💬 请输入要发送的评论内容: ").strip()
        if not comment_text:
            print("❌ 评论内容不能为空")
            return
        
        # 获取用户选择
        indices = self.get_user_selection(len(videos))
        if not indices:
            print("❌ 未选择任何视频")
            return
        
        # 确认操作
        print(f"\n📋 即将对 {len(indices)} 个视频发送评论：")
        for idx in indices:
            print(f"  - {videos[idx].title}")
        
        confirm = input("\n确认发送？(y/n): ").strip().lower()
        if confirm != 'y':
            print("❌ 操作已取消")
            return
        
        # 获取发送间隔
        try:
            delay = float(input("⏱️  请输入评论发送间隔秒数 (默认5秒): ").strip() or "5")
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