// Build the gh-pages flavor of index.html from the main-branch version:
// rewrite pets/ asset URLs to raw.githubusercontent (ASSET_BASE) and enable
// crossOrigin so pixel hit-testing / per-frame bbox reads work on the live site.
const fs = require("fs");
const src = fs.readFileSync("index.html", "utf8");
let out = src, n = 0;
const sub = (from, to) => {
  const i = out.indexOf(from);
  if (i < 0) throw new Error("patch point not found: " + JSON.stringify(from.slice(0, 60)));
  if (out.indexOf(from, i + 1) >= 0) throw new Error("patch point not unique: " + JSON.stringify(from.slice(0, 60)));
  out = out.slice(0, i) + to + out.slice(i + from.length);
  n++;
};

sub(
  "  const $ = (id) => document.getElementById(id);",
  `  const $ = (id) => document.getElementById(id);

  // 素材（webp/audio/avatar）统一从 main 分支的 raw 地址加载；gh-pages 分支只托管页面与 JSON
  const ASSET_BASE = "https://raw.githubusercontent.com/hunmer/aboutme-pets/main/";
  const assetUrl = (p) => {
    if (!p) return p;
    const norm = String(p).replace(/\\\\/g, "/");
    return norm.startsWith("pets/") ? ASSET_BASE + norm : norm;
  };`
);

sub(
  "(avatar ? `<img class=\"avatar\" src=\"${escapeHtml(avatar)}\" alt=\"\">` : \"\")",
  "(avatar ? `<img class=\"avatar\" src=\"${escapeHtml(assetUrl(avatar))}\" alt=\"\">` : \"\")"
);

sub("fetch(action.audio)", "fetch(assetUrl(action.audio))");

sub(
  "    img.decoding = \"async\";",
  "    img.decoding = \"async\";\n    img.crossOrigin = \"anonymous\"; // raw.githubusercontent 素材：允许像素级读取"
);

sub("    img.src = action.webp;", "    img.src = assetUrl(action.webp);");

sub(
  "      img.onload = () => resolve(img);",
  "      img.onload = () => resolve(img);\n      img.crossOrigin = \"anonymous\";"
);
sub("      img.src = src;", "      img.src = assetUrl(src);");

sub("await fetch(action.webp)", "await fetch(assetUrl(action.webp))");

fs.writeFileSync("index.html", out);
console.log("patched", n, "points, bytes", out.length);
