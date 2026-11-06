# aboutme-pets

Aboutme 宠物商店的目录与可下载动画资源。

## 商店地址

- 商店根：`https://raw.githubusercontent.com/hunmer/aboutme-pets/main/`
- 目录入口：`pets.json`

## 结构

```
pets.json                      商店目录（schemaVersion 1）
pets/dsh/manifest.json         基础动作 manifest（5 个动作，与客户端内置包一致）
pets/dsh/actions.json          动作列表（101 条，petId = dsh-pet）
pets/dsh/spritesheet/          101 个透明 WebP 精灵图 + 101 份同名动作 JSON
pets/dsh/NOTICE.txt            素材来源与许可说明
pets/dsh/SOURCE-upstream.txt   上游仓库与固定提交
pets/dsh/LICENSE-*.txt         上游 MIT 代码许可原文
pets/dsh1/manifest.json        新动作包 manifest（166 个动作，id = dsh1-pet）
pets/dsh1/actions.json         动作列表（166 条，petId = dsh1-pet）
pets/dsh1/spritesheet/         166 个透明 WebP 精灵图 + 166 份同名动作 JSON
pets/dsh1/audio/               2 条沿用旧版的动作音轨（工作加班、熬夜刷手机）
pets/dsh1/NOTICE.txt           第二批素材来源与许可说明
pets/dsh1/BATCH-source.txt     第二批 166 个动作的来源与转换记录
convert_video_pet_spritesheet.ps1  WebM → 透明 WebP 精灵图转换脚本（PowerShell 7）
convert_video_pet_spritesheet.py   同一脚本的 Python 实现（macOS/Linux，多进程，默认 1.5MB 上限）
index.html                    预览页（左预览 / 右分类 / 顶部角色切换，GitHub Pages 入口）
preview.json                   预览清单：宠物 → 分类 → 动作（含精灵图规格与音轨路径）
build_preview.py               读取 pets.json 与各包 actions.json 生成 preview.json
Transparent-Video-Maker/       视频去绿幕工具（Tkinter GUI + ffmpeg/rembg），用于去 AI 视频的绿幕
```

所有资源均为相对路径，客户端按同源规则解析；封面使用现有待机精灵图
`pets/dsh/spritesheet/待机呼吸休闲.webp`。

## 来源与许可

pets/dsh 素材来自 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)
，经 FFmpeg 转换
为透明 WebP 精灵图。**素材禁止商用；二创在公开渠道须署名上游仓库。**
上游两份 LICENSE 均为代码 MIT，不覆盖素材限制。转换脚本为本仓库根目录的
`convert_video_pet_spritesheet.ps1`，资源规格见 `about-me` 仓库
`assets/live2d/video/dsh_pet/README.md`。

## 在线预览（GitHub Pages）

<https://hunmer.github.io/aboutme-pets/>

## 本地预览

`index.html` 直接读 `preview.json`（`file://` fallback 到内联副本），无需构建：

```bash
python3 -m http.server 8901   # 在仓库根目录启动，浏览器打开 /
```

页面顶部切换角色包，左侧按 12fps 播放当前动作（空格暂停、`←`/`→` 逐帧、
`↑`/`↓` 换动作、滑杆拖动），右侧按分类浏览 / 搜索 267 个动作，带音轨的动作会自动播放。

动作增删后重新生成清单（同时把清单内联进 HTML）：

```bash
python3 build_preview.py
```

## 重新导出精灵图

```bash
python3 convert_video_pet_spritesheet.py \
  --source-dir <webm 目录> --json-path pets/dsh1/manifest.json --jobs 6 [--resume]
```

默认单张 WebP 上限 1.5MB；30 秒长动作可用 `--bytes-override 动作名=3000000` 放宽。

## 视频去绿幕（Transparent-Video-Maker）

`Transparent-Video-Maker/` 是配套工具面板，**用于去 AI 视频的绿幕**：把 AI 生成视频里
的纯色背景（绿幕 / 蓝幕 / 黑底 / 白底）抠掉，输出带 alpha 通道的透明视频，再交给上面的
脚本转成精灵图。

- 抠像方式：**Chroma Key**（纯色背景，按颜色 + Similarity / Edge Blend 调）或 **AI Model**
  （rembg，复杂背景），另有「Frames → Video」标签页把手工修好的透明 PNG 序列编码回视频。
- 输出格式：WebM/VP9（网页、OBS、多数剪辑软件）或 MOV/ProRes 4444（剪辑母版）。
- 命令行模式：`video` / `frames` 子命令，终端单行进度条（步骤、百分比、秒或帧、速度、ETA），
  支持 `--dry-run` 预演、`-q` 静默、`--keep-frames` 保留中间帧；无参数运行仍打开图形界面。
- 支持**目录批量**：`video -i <目录> -o <输出目录>` 转换目录下所有视频，加 `-r` 递归子目录；
  单个文件失败不中断，退出码为 1。
- 依赖本机已安装 Python 3.10+（含 Tk）、ffmpeg、`python3 -m pip install "rembg[cli]"`（`requirements.txt`）；ffmpeg 与 rembg 不随仓库分发。

```bash
cd Transparent-Video-Maker
python3 TransparentVideoMaker.py                      # 图形界面
python3 TransparentVideoMaker.py video -i clip.mp4     # 命令行，带进度条
python3 TransparentVideoMaker.py frames -i ./frames -o out.webm
```

工具原本是独立仓库，现已并入本仓库并去掉其 `.git`，且移除了 Windows 打包/启动器
（`start-tvm.bat`）与相关专用代码，只保留 macOS/Linux 路径；详细说明见
`Transparent-Video-Maker/README.md`（MIT，第三方 ffmpeg GPL v3 / rembg MIT）。
