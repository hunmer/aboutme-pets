#requires -Version 7.0
# Run with pwsh, which reads this UTF-8 script and Chinese filenames correctly.
# 只处理 WebM 源：枚举 -SourceDir 下所有 *.webm（文件名去掉扩展名即动作 id），
# 为每个动作生成 spritesheet/<id>.webp 与同名 JSON，并把动作条目合并写回
# -JsonPath（沿用 video pet manifest 结构）。目标 JSON 中已存在的动作只更新
# sources.webp / spritesheet / audio，其余字段（name、category、idle、movement…）
# 原样保留；文件不存在时自动建最小骨架。
# 产物目录固定为 -JsonPath 所在目录下的 spritesheet/ 与 audio/。
# 示例：
#   pwsh -NoProfile -File "D:/aboutme-pets/convert_video_pet_spritesheet.ps1" `
#     -SourceDir "assets/live2d/video/dsh_pet/webm" `
#     -JsonPath "assets/live2d/video/dsh_pet/manifest.json"
[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$SourceDir,
  [Parameter(Mandatory)][string]$JsonPath,
  [switch]$Resume,
  [ValidateRange(1, 8192)][int]$FrameWidth = 640,
  [ValidateRange(1, 8192)][int]$FrameHeight = 360,
  [ValidateRange(1, 120)][int]$Fps = 12,
  [ValidateRange(1, 100)][int]$Columns = 8,
  [ValidateRange(1, 2147483647)][int]$MaxBytes = 1500000,
  [ValidateRange(0, 100)][int]$Quality = 100,
  [string]$Ffmpeg = 'E:/ffmpeg/ffmpeg.exe',
  [string]$Ffprobe = '',
  [string]$Cwebp = 'D:/tmp/webp-1.6.0/libwebp-1.6.0-windows-x64/bin/cwebp.exe'
)

$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$culture = [System.Globalization.CultureInfo]::InvariantCulture

$sourceDirPath = (Resolve-Path -LiteralPath $SourceDir).Path
$jsonFullPath = [System.IO.Path]::GetFullPath($JsonPath)
$packageDir = Split-Path -Parent $jsonFullPath
if (-not $packageDir) { throw "Invalid -JsonPath: $JsonPath" }
$outDir = Join-Path $packageDir 'spritesheet'
$audioDir = Join-Path $packageDir 'audio'

function Resolve-Tool([string]$ConfiguredPath) {
  if (-not (Test-Path -LiteralPath $ConfiguredPath -PathType Leaf)) {
    throw "Tool not found: $ConfiguredPath"
  }
  return (Resolve-Path -LiteralPath $ConfiguredPath).Path
}

function Write-Json($Value, [string]$Path) {
  $json = ConvertTo-Json -InputObject $Value -Depth 100
  [System.IO.File]::WriteAllText($Path, $json + "`n", $utf8)
}

# Extracts audio/<id>.m4a when the source has an audio stream. Returns an ordered
# hashtable with the manifest-relative path. A silent source instead RETAINS an
# existing non-empty m4a (retained = $true) so audio support survives re-exports
# without their own track; only when no m4a exists does it return $null.
# Resume reuses an existing non-empty m4a, but a missing file is always re-extracted.
function Ensure-AudioTrack([string]$Id, [string]$Source) {
  $probeText = & $ffprobePath -v error -select_streams a:0 `
    -show_entries stream=codec_name -of json $Source
  if ($LASTEXITCODE -ne 0) { throw "Unable to probe audio stream: $Source" }
  $streams = @((($probeText -join "`n") | ConvertFrom-Json).streams)
  if ($streams.Count -eq 0 -or -not $streams[0].codec_name) {
    # Silent source: keep an existing m4a from an earlier export of the same
    # performance so audio support survives re-exports whose video has no track.
    $existingPath = Join-Path $audioDir "$Id.m4a"
    if ((Test-Path -LiteralPath $existingPath -PathType Leaf) -and
        (Get-Item -LiteralPath $existingPath).Length -gt 0) {
      $m4aProbe = & $ffprobePath -v error -select_streams a:0 `
        -show_entries stream=codec_name -of json $existingPath
      if ($LASTEXITCODE -eq 0) {
        $m4aStreams = @((($m4aProbe -join "`n") | ConvertFrom-Json).streams)
        $m4aCodec = if ($m4aStreams.Count -gt 0 -and $m4aStreams[0].codec_name) { [string]$m4aStreams[0].codec_name } else { 'aac' }
        return [ordered]@{ rel = "audio/$Id.m4a"; codec = $m4aCodec; streamCopy = $false; bytes = (Get-Item -LiteralPath $existingPath).Length; retained = $true }
      }
    }
    return $null
  }
  $codec = [string]$streams[0].codec_name
  $audioPath = Join-Path $audioDir "$Id.m4a"
  $streamCopy = $codec -ceq 'aac'
  if ($Resume -and (Test-Path -LiteralPath $audioPath -PathType Leaf) -and
      (Get-Item -LiteralPath $audioPath).Length -gt 0) {
    return [ordered]@{ rel = "audio/$Id.m4a"; codec = $codec; streamCopy = $streamCopy; bytes = (Get-Item -LiteralPath $audioPath).Length; reused = $true }
  }
  New-Item -ItemType Directory -Force -Path $audioDir | Out-Null
  $encodeArgs = if ($streamCopy) { @('-c:a', 'copy') } else { @('-c:a', 'aac', '-b:a', '192k') }
  & $ffmpegPath -hide_banner -nostdin -y -v error -i $Source `
    -map 0:a:0 -vn -sn -dn @encodeArgs -movflags +faststart $audioPath
  if ($LASTEXITCODE -ne 0) { throw "Audio extraction failed: $Source" }
  if (-not (Test-Path -LiteralPath $audioPath -PathType Leaf) -or (Get-Item -LiteralPath $audioPath).Length -eq 0) {
    throw "Audio extraction produced no output: $audioPath"
  }
  return [ordered]@{ rel = "audio/$Id.m4a"; codec = $codec; streamCopy = $streamCopy; bytes = (Get-Item -LiteralPath $audioPath).Length; reused = $false }
}

function Add-SortedAnimation([System.Collections.Generic.List[object]]$List, $Entry) {
  $insertAt = $List.Count
  for ($i = 0; $i -lt $List.Count; $i++) {
    if ([string]::CompareOrdinal([string]$List[$i].id, [string]$Entry.id) -gt 0) { $insertAt = $i; break }
  }
  $List.Insert($insertAt, $Entry)
}

$ffmpegPath = Resolve-Tool $Ffmpeg
if (-not $Ffprobe) { $Ffprobe = Join-Path (Split-Path -Parent $ffmpegPath) 'ffprobe.exe' }
$ffprobePath = Resolve-Tool $Ffprobe
$cwebpPath = Resolve-Tool $Cwebp

$files = @(Get-ChildItem -LiteralPath $sourceDirPath -File -Filter '*.webm' | Sort-Object Name)
if ($files.Count -eq 0) { throw "No .webm files found in: $sourceDirPath" }

# Target JSON is the manifest itself: keep every existing field and animation
# entry, only overwrite sources.webp / spritesheet / audio for converted ids.
if (Test-Path -LiteralPath $jsonFullPath -PathType Leaf) {
  $manifest = Get-Content -LiteralPath $jsonFullPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
} else {
  $packageName = Split-Path -Leaf $packageDir
  $manifest = [ordered]@{
    schemaVersion = 1
    id = $packageName
    name = $packageName
    canvasWidth = $FrameWidth
    canvasHeight = $FrameHeight
    animations = @()
    metadata = [ordered]@{}
  }
}
if (-not $manifest.Contains('animations') -or $null -eq $manifest.animations) { $manifest['animations'] = @() }
if (-not $manifest.Contains('metadata') -or $null -eq $manifest.metadata) { $manifest['metadata'] = [ordered]@{} }

New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("aboutme-spritesheet-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $workDir | Out-Null
$timer = [System.Diagnostics.Stopwatch]::StartNew()
$results = [ordered]@{}
$audioByAction = @{}
$audioStats = [ordered]@{ count = 0; bytes = 0; reused = 0 }
$index = 0
try {
  foreach ($file in $files) {
    $id = [System.IO.Path]::GetFileNameWithoutExtension($file.Name)
    $source = $file.FullName
    # Audio is ensured even when the spritesheet is later reused via -Resume,
    # so a missing m4a never survives a resumed run.
    $audio = Ensure-AudioTrack $id $source
    $audioByAction[$id] = if ($audio) { $audio.rel } else { $null }
    if ($audio) {
      $audioStats.count++
      $audioStats.bytes += $audio.bytes
      if ($audio.reused) { $audioStats.reused++ }
      Write-Host "[$id] audio codec=$($audio.codec) copy=$($audio.streamCopy) bytes=$($audio.bytes)$(if ($audio.reused) { ' (reused)' } elseif ($audio.retained) { ' (retained: source silent)' })"
    }
    $output = Join-Path $outDir "$id.webp"
    $metadataPath = Join-Path $outDir "$id.json"
    if ($Resume -and (Test-Path -LiteralPath $output) -and (Test-Path -LiteralPath $metadataPath)) {
      $existing = Get-Content -LiteralPath $metadataPath -Raw -Encoding utf8 | ConvertFrom-Json
      if ($existing.FrameWidth -eq $FrameWidth -and $existing.FrameHeight -eq $FrameHeight -and
          $existing.SampleFps -eq $Fps -and $existing.Columns -eq $Columns -and
          $existing.MaxImageBytes -eq $MaxBytes -and $existing.AlphaQuality -gt 0 -and
          $existing.EncodedBytes -eq (Get-Item -LiteralPath $output).Length -and $existing.EncodedBytes -lt $MaxBytes) {
        $results[$id] = [ordered]@{ frameWidth=$FrameWidth; frameHeight=$FrameHeight; columns=$Columns; rows=$existing.Rows; frameCount=$existing.FrameCount; fps=$Fps }
        $index++
        Write-Host "[$index/$($files.Count)] $id reused bytes=$($existing.EncodedBytes)"
        continue
      }
    }
    $rawSheet = Join-Path $workDir 'sheet.png'
    $candidate = Join-Path $workDir 'candidate.webp'
    $best = Join-Path $workDir 'best.webp'
    $probeText = & $ffprobePath -v error -select_streams v:0 `
      -show_entries 'format=duration:stream=r_frame_rate,pix_fmt:stream_tags=alpha_mode' -of json $source
    if ($LASTEXITCODE -ne 0) { throw "Unable to probe source: $source" }
    $probe = ($probeText -join "`n") | ConvertFrom-Json
    $duration = [double]::Parse($probe.format.duration, $culture)
    $rational = $probe.streams[0].r_frame_rate.Split('/')
    $sourceFps = [double]::Parse($rational[0], $culture) / [double]::Parse($rational[1], $culture)
    $sourcePixFmt = [string]$probe.streams[0].pix_fmt
    # VP9 WebM keeps alpha as side data: ffprobe still reports yuv420p but sets
    # the alpha_mode tag, and only decoding surfaces the RGBA plane.
    $sourceHasAlpha = $sourcePixFmt -match 'yuva|rgba|bgra|argb|abgr|pal8' -or
      [string]$probe.streams[0].tags.alpha_mode -eq '1'

    # Duration is descriptive only; count after the fps filter's EOF rounding.
    # WebM is always decoded through libvpx-vp9 to keep its alpha plane.
    $countArgs = @('-hide_banner', '-nostdin', '-v', 'error', '-nostats', '-c:v', 'libvpx-vp9',
      '-i', $source, '-map', '0:v:0', '-an', '-sn',
      '-vf', "fps=$Fps", '-fps_mode', 'passthrough', '-progress', 'pipe:1', '-f', 'null', '-')
    $progress = & $ffmpegPath @countArgs
    if ($LASTEXITCODE -ne 0) { throw "Frame counting failed: $source" }
    $frameLines = @($progress | Where-Object { $_ -match '^frame=\d+$' })
    if ($frameLines.Count -eq 0 -or $progress -notcontains 'progress=end') {
      throw "Missing completed FFmpeg progress: $source"
    }
    $frameCount = [int]($frameLines[-1].Split('=')[1])
    if ($frameCount -le 0) { throw "No sampled frames: $source" }
    $rows = [int][math]::Ceiling($frameCount / [double]$Columns)

    # Preserve source alpha and transparent padding before lossy RGB encoding.
    $sheetArgs = @('-hide_banner', '-nostdin', '-y', '-v', 'error', '-c:v', 'libvpx-vp9',
      '-i', $source, '-map', '0:v:0', '-an', '-sn',
      '-vf', "fps=$Fps,scale=${FrameWidth}:${FrameHeight}:flags=lanczos,format=rgba,tile=layout=${Columns}x${rows}:nb_frames=${frameCount}:color=0x00000000",
      '-frames:v', '1', '-update', '1', '-pix_fmt', 'rgba', $rawSheet)
    & $ffmpegPath @sheetArgs
    if ($LASTEXITCODE -ne 0) { throw "Spritesheet conversion failed: $source" }

    # Prefer usable RGB detail; quantize soft alpha edges only as needed.
    $bestQuality = -1
    $alphaQuality = 50
    $baseQuality = [math]::Min(35, $Quality)
    foreach ($trialAlpha in @(50, 25, 10, 1)) {
      & $cwebpPath -quiet -m 4 -mt -alpha_q $trialAlpha -q $baseQuality $rawSheet -o $candidate
      if ($LASTEXITCODE -ne 0) { throw "WebP encoding failed: $id" }
      if ((Get-Item -LiteralPath $candidate).Length -lt $MaxBytes) {
        Copy-Item -LiteralPath $candidate -Destination $best -Force
        $bestQuality = $baseQuality
        $alphaQuality = $trialAlpha
        break
      }
    }
    $low = if ($bestQuality -ge 0) { $baseQuality + 1 } else { 0 }
    $high = if ($bestQuality -ge 0) { $Quality } else { $baseQuality - 1 }
    if ($bestQuality -lt 0) { $alphaQuality = 1 }
    while ($low -le $high) {
      $trialQuality = [int][math]::Floor(($low + $high) / 2)
      & $cwebpPath -quiet -m 4 -mt -alpha_q $alphaQuality -q $trialQuality $rawSheet -o $candidate
      if ($LASTEXITCODE -ne 0) { throw "WebP encoding failed: $id quality=$trialQuality" }
      if ((Get-Item -LiteralPath $candidate).Length -lt $MaxBytes) {
        Copy-Item -LiteralPath $candidate -Destination $best -Force
        $bestQuality = $trialQuality
        $low = $trialQuality + 1
      } else {
        $high = $trialQuality - 1
      }
    }
    if ($bestQuality -lt 0) { throw "Cannot fit $id below $MaxBytes bytes without changing dimensions or frames" }
    $encodedBytes = (Get-Item -LiteralPath $best).Length
    if ($encodedBytes -ge $MaxBytes) { throw "WebP exceeds byte limit: $id" }
    $imageText = & $ffprobePath -v error -select_streams v:0 `
      -show_entries stream=width,height,pix_fmt -of json $best
    if ($LASTEXITCODE -ne 0) { throw "Unable to probe WebP: $id" }
    $image = (($imageText -join "`n") | ConvertFrom-Json).streams[0]
    $allowedPixFmts = if ($sourceHasAlpha) { @('rgba', 'bgra', 'yuva420p') } else { @('rgba', 'bgra', 'yuva420p', 'yuv420p') }
    if ($image.width -ne ($FrameWidth * $Columns) -or $image.height -ne ($FrameHeight * $rows) -or $image.pix_fmt -notin $allowedPixFmts) {
      throw "WebP dimensions or alpha format mismatch: $id"
    }
    Move-Item -LiteralPath $best -Destination $output -Force
    $sheet = [ordered]@{
      frameWidth = $FrameWidth
      frameHeight = $FrameHeight
      columns = $Columns
      rows = $rows
      frameCount = $frameCount
      fps = $Fps
    }
    $meta = [ordered]@{
      Source = Split-Path -Leaf $source
      FrameWidth = $FrameWidth
      FrameHeight = $FrameHeight
      Columns = $Columns
      Rows = $rows
      FrameCount = $frameCount
      SampleFps = $Fps
      SourceDurationSeconds = $duration
      SourceFps = $sourceFps
      CanvasWidth = $image.width
      CanvasHeight = $image.height
      SourceVideoBytes = (Get-Item -LiteralPath $source).Length
      SourcePixFmt = $sourcePixFmt
      Alpha = if ($sourceHasAlpha) { 'RGBA' } else { 'Opaque (source has no alpha channel; libwebp omits the alpha plane)' }
      ImageFormat = 'webp'
      Lossless = $false
      EncodingQuality = $bestQuality
      AlphaQuality = $alphaQuality
      Encoder = 'cwebp -m 4 -mt'
      EncodedBytes = $encodedBytes
      MaxImageBytes = $MaxBytes
      License = 'Non-commercial; attribution required. See NOTICE.txt and SOURCE-upstream.txt.'
    }
    if ($audio) {
      $meta['HasAudio'] = $true
      $meta['AudioFormat'] = 'm4a'
      $meta['AudioCodec'] = $audio.codec
      $meta['AudioStreamCopy'] = [bool]$audio.streamCopy
      $meta['AudioBytes'] = $audio.bytes
      if ($audio.retained) { $meta['AudioNote'] = 'Source video has no audio stream; existing m4a from the same performance kept' }
    } else {
      $meta['HasAudio'] = $false
    }
    Write-Json $meta $metadataPath
    $results[$id] = $sheet
    $index++
    Write-Host "[$index/$($files.Count)] $id quality=$bestQuality frames=$frameCount grid=${Columns}x${rows} texture=$($image.width)x$($image.height) bytes=$encodedBytes limit=$MaxBytes"
  }

  # Merge converted ids into the manifest: existing entries keep every other
  # field and are sorted in place; brand-new ids get a placeholder entry.
  $list = [System.Collections.Generic.List[object]]::new()
  foreach ($item in @($manifest.animations)) { $list.Add($item) }
  foreach ($id in $results.Keys) {
    $target = $null
    foreach ($item in $list) {
      if ([string]$item.id -ceq $id) { $target = $item; break }
    }
    if (-not $target) {
      $target = [ordered]@{ id = $id; name = $id; category = '未分类'; sources = [ordered]@{}; spritesheet = $null }
      Add-SortedAnimation $list $target
    }
    $target.sources = [ordered]@{ webp = "spritesheet/$id.webp" }
    $target.spritesheet = $results[$id]
    $audioRel = $audioByAction[$id]
    if ($audioRel) {
      $target.audio = $audioRel
    } else {
      $target.Remove('audio') | Out-Null
    }
  }
  $manifest.animations = @($list)
  $manifest.metadata['resourceFormat'] = 'Per-action lossy WebP spritesheets with alpha; optional AAC m4a audio track'
  $manifest.metadata['sourceVideos'] = '*.webm enumerated from -SourceDir; filename without extension is the animation id'
  $manifest.metadata['conversion'] = [ordered]@{
    frameWidth = $FrameWidth
    frameHeight = $FrameHeight
    fps = $Fps
    columns = $Columns
    rows = 'ceil(actual fps-filtered frame count / columns)'
    frameCountMethod = 'FFmpeg fps filter -> null output -> final progress frame count'
    imageFormat = 'webp'
    lossless = $false
    maxImageBytes = $MaxBytes
    qualityCeiling = $Quality
    qualitySearch = 'Per-image RGB integer binary search; alpha quality 50/25/10/1 as needed; encoded bytes strictly below maxImageBytes'
    encoder = 'cwebp -m 4 -mt'
    alphaHandling = 'Source alpha preserved via RGBA tiles; alpha-free sources encode fully opaque and libwebp omits the alpha plane'
    audio = 'First audio stream extracted to audio/<id>.m4a (AAC; stream copy when the source is already AAC, otherwise 192k re-encode); a silent source retains an existing m4a from an earlier export of the same performance (json AudioNote); no manifest audio reference only when neither exists'
    script = 'convert_video_pet_spritesheet.ps1'
  }
  Write-Json $manifest $jsonFullPath
  $timer.Stop()
  $images = @(Get-ChildItem -LiteralPath $outDir -Filter '*.webp' -File)
  $largest = Get-ChildItem -LiteralPath $outDir -Filter '*.json' -File | ForEach-Object {
    Get-Content -LiteralPath $_.FullName -Raw -Encoding utf8 | ConvertFrom-Json
  } | Sort-Object { $_.CanvasWidth * $_.CanvasHeight } -Descending | Select-Object -First 1
  Write-Host "Completed $index actions in $([math]::Round($timer.Elapsed.TotalSeconds, 2)) seconds. WebP count=$($images.Count), WebP bytes=$(($images | Measure-Object Length -Sum).Sum), max bytes=$(($images | Measure-Object Length -Maximum).Maximum)."
  Write-Host "Audio count=$($audioStats.count) (reused $($audioStats.reused)), audio bytes=$($audioStats.bytes)."
  if ($largest) {
    Write-Host "Largest texture: $($largest.CanvasWidth)x$($largest.CanvasHeight), RGBA bytes=$($largest.CanvasWidth * $largest.CanvasHeight * 4), source=$($largest.Source)."
  }
} finally {
  Remove-Item -LiteralPath $workDir -Recurse -Force
}
