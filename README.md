# B站评论工具

Flask Web 界面，支持 B 站视频搜索和批量评论。

## 功能

- **视频搜索** — 关键词搜索、多种排序、过滤设置
- **扫码登录** — 二维码扫码登录，支持多账号
- **模板评论** — 预设模板 + 智能变体生成（6 种策略防重复）
- **批量评论** — 多视频批量发送，预览确认 + 实时进度
- **评论历史** — 查看记录、再次评论、导出 CSV/JSON
- **屏蔽管理** — 屏蔽词 + 黑名单 UP 主过滤
- **多用户隔离** — Session 隔离，支持内网穿透多人使用
- **深色模式** — 支持深色/浅色切换，自动持久化

## 快速开始

### 方式一：下载 exe（推荐）

1. 从 [Releases](https://github.com/3188575612-creator/bilibili-comment-tool/releases) 下载 `BilibiliCommentTool.exe`
2. 双击启动，浏览器自动打开
3. 扫码登录 B 站即可使用

### 方式二：源码运行

```bash
pip install -r requirements.txt
python app.py
```

## 技术栈

- Python 3.11 / Flask
- bilibili-api-python
- PyInstaller（打包单文件 exe）

## License

MIT
