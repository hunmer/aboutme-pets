# aboutme-pets

Aboutme 宠物商店的目录与可下载动画资源。

## 在线预览（GitHub Pages）

<https://hunmer.github.io/aboutme-pets/>

页面顶部切换角色包，左侧按 12fps 播放当前动作（空格暂停、`←`/`→` 逐帧、
`↑`/`↓` 换动作、滑杆拖动），右侧按分类浏览 / 搜索全部 829 个动作，
带音轨的动作会自动播放。

## 已收录宠物

| 角色 | 目录 | 动作数 | 来源 |
| --- | --- | ---: | --- |
| 蓝毛小女仆 | `pets/dsh` | 101 | 上游 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)，FFmpeg 转换为透明 WebP |
| 蓝毛小女仆·新动作包 | `pets/dsh1` | 167 | 作者生成（2026-11 第二批，含 2 条动作音轨） |
| GPT 动作包 | `pets/gpt` | 92 | 作者生成 |
| Claude 动作包 | `pets/claude` | 166 | 作者生成（含 8 条动作音轨） |
| Gemini 动作包（猫耳娘版） | `pets/gemini` | 100 | 作者生成（含 96 条动作音轨） |
| Qwen 动作包 | `pets/qwen` | 106 | 作者生成（含 66 条动作音轨） |
| GLM 动作包（蓝狐耳娘版） | `pets/glm` | 97 | 作者生成（含 AAC 音轨） |

7 个角色包合计 **829** 个动作。除 `pets/dsh`（素材来自上游 dsh-pet 仓库）外，
其余角色包的全部素材均为本仓库作者生成。

## 商店地址

- 商店根：`https://raw.githubusercontent.com/hunmer/aboutme-pets/main/`
- 目录入口：`pets.json`

## 来源与许可

pets/dsh 素材来自 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)
，经 FFmpeg 转换
为透明 WebP 精灵图。**素材禁止商用；二创在公开渠道须署名上游仓库。**
上游两份 LICENSE 均为代码 MIT，不覆盖素材限制。转换脚本为本仓库根目录的
`convert_video_pet_spritesheet.ps1`，资源规格见 `about-me` 仓库
`assets/live2d/video/dsh_pet/README.md`。

## 重新导出精灵图

```bash
python3 convert_video_pet_spritesheet.py \
  --source-dir <webm 目录> --json-path pets/dsh1/manifest.json --jobs 6 [--resume]
```

默认单张 WebP 上限 1.5MB；30 秒长动作可用 `--bytes-override 动作名=3000000` 放宽。

## 精灵图还原为透明 WebM

通过网盘分享的知识：dsh-webm 透明素材
链接: [https://pan.baidu.com/s/5oxtYiRXZUZILismVlh0XRA](https://pan.baidu.com/s/5oxtYiRXZUZILismVlh0XRA)

`spritesheet_to_video_pet.py` 是上一步的逆操作：读取 `spritesheet/` 下成对的
`<id>.webp` + `<id>.json`，按元数据（`FrameWidth/FrameHeight/Columns/FrameCount/SampleFps`）
把拼贴大图逐帧裁出，重编码为带 alpha 的 VP9 WebM（`yuva420p` + `alpha_mode=1`，
浏览器可直接播放透明背景）。

```bash
python3 spritesheet_to_video_pet.py \
  --input-dir pets/claude/spritesheet --output-dir webm-restored --jobs 4
```

- 自动探测 input-dir 同级的 `audio/<id>.m4a` 并混流（也可 `--audio-dir` 显式指定）；
- `--resume` 跳过已有输出，`--crf`（默认 20）调质量；单个动作失败不中断整批。

注意：部分 ffmpeg 构建（如 9.x gyan）的原生 VP9 解码器默认不导出 alpha，
需 `-c:v libvpx-vp9` 解码或在浏览器中查看，文件本身是标准透明 WebM
（alpha 以 Matroska BlockAdditional 轨存储）。

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
