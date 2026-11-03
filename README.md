# aboutme-pets

Aboutme 宠物商店的目录与可下载动画资源。

## 商店地址

- 商店根：`https://raw.githubusercontent.com/hunmer/aboutme-pets/main/`
- 目录入口：`pets.json`

## 结构

```
pets.json                      商店目录（schemaVersion 1）
pets/dsh/manifest.json         基础动作 manifest（5 个动作，与客户端内置包一致）
pets/dsh/actions.json          动作列表（122 条，petId = dsh-pet）
pets/dsh/spritesheet/          122 个透明 WebP 精灵图 + 122 份同名动作 JSON
pets/dsh/audio/                2 条动作音轨（m4a）
pets/dsh/NOTICE.txt            素材来源与许可说明
pets/dsh/SOURCE-upstream.txt   上游仓库与固定提交
pets/dsh/LICENSE-*.txt         上游 MIT 代码许可原文
```

所有资源均为相对路径，客户端按同源规则解析；封面使用现有待机精灵图
`pets/dsh/spritesheet/待机呼吸休闲.webp`。

## 来源与许可

素材来自 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)
（固定提交 `b78e3fcf92ac78cee569aed7cc27eeb2a8d51757`），经 FFmpeg 转换
为透明 WebP 精灵图。**素材禁止商用；二创在公开渠道须署名上游仓库。**
上游两份 LICENSE 均为代码 MIT，不覆盖素材限制。转换脚本与规格见
`about-me` 仓库 `assets/live2d/video/dsh_pet/README.md`。
