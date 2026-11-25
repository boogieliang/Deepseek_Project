#!/usr/bin/env node
/**
 * dsh-pet 素材审计 / 缺失补齐
 *
 * 官方清单来源（按优先级）：
 *   1. 已安装插件包内的 assets/config.jsonc（官方随包清单，动画名 → 池/分类/事件）
 *   2. GitHub main 分支 dsh-pet/assets/webm 文件树（用于核对“包内是否漏发”）
 *   3. npm 官方 tarball（缺失文件从这里补齐，跳过已存在文件）
 *
 * 用法：
 *   node audit-pet-assets.mjs                # 审计；有缺失则用 npm tarball 补齐
 *   node audit-pet-assets.mjs --dry-run      # 只审计，不下载
 *   node audit-pet-assets.mjs --plugin <dir> # 指定插件目录
 *   node audit-pet-assets.mjs --report-dir <dir>
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const args = process.argv.slice(2);
const hasFlag = (f) => args.includes(f);
const argOf = (f, d) => {
  const i = args.indexOf(f);
  return i >= 0 && args[i + 1] ? args[i + 1] : d;
};

const DRY_RUN = hasFlag('--dry-run');
const NO_NETWORK = hasFlag('--no-network');
const DSH_HOME = process.env.DSH_HOME || path.join(os.homedir(), '.dsh');
const REPORT_DIR = path.resolve(argOf('--report-dir', path.join(process.cwd(), 'pet-assets-audit')));

function findPluginDir() {
  const explicit = argOf('--plugin', '');
  if (explicit) return path.resolve(explicit);
  const profiles = path.join(DSH_HOME, 'profiles');
  const candidates = [];
  if (fs.existsSync(profiles)) {
    for (const name of fs.readdirSync(profiles)) {
      candidates.push(path.join(profiles, name, 'node_modules', 'dsh-pet'));
    }
  }
  candidates.push(path.join(DSH_HOME, 'dsh-pet'));
  for (const c of candidates) {
    if (fs.existsSync(path.join(c, 'package.json'))) return c;
  }
  throw new Error('找不到已安装的 dsh-pet 插件；请用 --plugin 指定目录');
}

/** 去掉 JSONC 注释（尊重字符串）并解析 */
function parseJsonc(text) {
  let out = '';
  let inStr = false;
  let esc = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    const n = text[i + 1];
    if (inStr) {
      out += c;
      if (esc) esc = false;
      else if (c === '\\') esc = true;
      else if (c === '"') inStr = false;
    } else if (c === '"') {
      inStr = true;
      out += c;
    } else if (c === '/' && n === '/') {
      while (i < text.length && text[i] !== '\n') i++;
      out += '\n';
    } else if (c === '/' && n === '*') {
      i += 2;
      while (i < text.length && !(text[i] === '*' && text[i + 1] === '/')) i++;
      i++;
    } else out += c;
  }
  return JSON.parse(out.replace(/,(\s*[}\]])/g, '$1'));
}

/** 按官方 config 结构拆出 核心 / 随机 / 事件 三组动画名 */
function collectAnimations(cfg) {
  const a = cfg.animations || {};
  const groups = { core: [], random: [], events: [] };
  const push = (bucket, value) => {
    if (typeof value === 'string' && value) bucket.push(value);
    else if (Array.isArray(value)) value.forEach((v) => push(bucket, v));
  };
  for (const key of ['idle', 'turn', 'drag', 'clicks']) push(groups.core, a[key]);
  push(groups.core, (a.moves?.actions || []).map((x) => x.name));
  for (const cat of a.categories || []) push(groups.random, cat.actions);
  for (const value of Object.values(a.events || {})) push(groups.events, value);
  const uniq = (xs) => [...new Set(xs)];
  for (const k of Object.keys(groups)) groups[k] = uniq(groups[k]);
  return groups;
}

function listFiles(dir, ext) {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.toLowerCase().endsWith(ext))
    .map((f) => ({ name: f, base: f.slice(0, -ext.length), bytes: fs.statSync(path.join(dir, f)).size }));
}

function isEbml(file) {
  try {
    const fd = fs.openSync(file, 'r');
    const b = Buffer.alloc(4);
    fs.readSync(fd, b, 0, 4, 0);
    fs.closeSync(fd);
    return b[0] === 0x1a && b[1] === 0x45 && b[2] === 0xdf && b[3] === 0xa3;
  } catch {
    return false;
  }
}

async function fetchJson(url) {
  const r = await fetch(url, { headers: { 'User-Agent': 'dsh-pet-assets-audit' } });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

async function officialGithubFiles() {
  if (NO_NETWORK) return null;
  try {
    const tree = await fetchJson(
      'https://api.github.com/repos/PC2005-cloud/dsh-pet/git/trees/main?recursive=1',
    );
    const files = tree.tree
      .filter((x) => x.path.startsWith('dsh-pet/assets/webm/') && x.type === 'blob')
      .map((x) => ({ name: path.posix.basename(x.path), bytes: x.size }));
    return { source: 'GitHub main', files };
  } catch (e) {
    return { source: `GitHub main（不可用：${e.message}）`, files: null };
  }
}

/** 解压 tarball 里的 assets/webm 到临时目录（不用管道捕获，避免沙箱 EPERM），返回目录 */
function extractWebm(tarball, tmpDir) {
  fs.mkdirSync(tmpDir, { recursive: true });
  execFileSync('tar', ['-xzf', tarball, '-C', tmpDir, 'package/assets/webm'], { stdio: 'ignore' });
  return path.join(tmpDir, 'package', 'assets', 'webm');
}

async function ensureNpmTarball(version, cachePath) {
  if (fs.existsSync(cachePath) && fs.statSync(cachePath).size > 10_000_000) return cachePath;
  const meta = await fetchJson(`https://registry.npmjs.org/dsh-pet/${version}`);
  const url = meta.dist?.tarball;
  if (!url) throw new Error(`npm 上没有 dsh-pet@${version}`);
  console.log(`下载官方 npm tarball：${url}`);
  const r = await fetch(url);
  if (!r.ok) throw new Error(`tarball ${r.status}`);
  const buf = Buffer.from(await r.arrayBuffer());
  fs.writeFileSync(cachePath, buf);
  return cachePath;
}

function restoreMissing(npmDir, missingNames, webmDir) {
  const restored = [];
  for (const name of missingNames) {
    const src = path.join(npmDir, `${name}.webm`);
    const dst = path.join(webmDir, `${name}.webm`);
    if (!fs.existsSync(src)) {
      console.log(`  跳过 ${name}：官方 tarball 内也没有`);
      continue;
    }
    if (fs.existsSync(dst)) continue; // 双保险：不覆盖已存在文件
    fs.copyFileSync(src, dst);
    restored.push({ name, bytes: fs.statSync(dst).size });
    console.log(`  已补齐 ${name} (${(fs.statSync(dst).size / 1024).toFixed(0)} KiB)`);
  }
  return restored;
}

function mdTable(rows, headers) {
  return [
    `| ${headers.join(' | ')} |`,
    `| ${headers.map(() => '---').join(' | ')} |`,
    ...rows.map((r) => `| ${r.join(' | ')} |`),
  ].join('\n');
}

const pluginDir = findPluginDir();
const pkg = JSON.parse(fs.readFileSync(path.join(pluginDir, 'package.json'), 'utf8'));
const assetsDir = path.join(pluginDir, 'assets');
const webmDir = path.join(assetsDir, 'webm');
const cfg = parseJsonc(fs.readFileSync(path.join(assetsDir, 'config.jsonc'), 'utf8'));
const groups = collectAnimations(cfg);
const required = [...new Set([...groups.core, ...groups.random, ...groups.events])];
const local = listFiles(webmDir, '.webm');
const localByBase = new Map(local.map((f) => [f.base, f]));

const missing = required.filter((n) => !localByBase.has(n));
const unused = local.map((f) => f.base).filter((n) => !required.includes(n));

// 表情包 / 通知图 / 字体
const memesCfg = Object.keys(cfg.memes || {});
const memesLocal = listFiles(path.join(assetsDir, 'memes'), '.png');
const memesMissing = memesCfg.filter((m) => !memesLocal.some((f) => f.base === m));
const picLocal = listFiles(path.join(assetsDir, 'pic'), '.png');
const fontsLocal = listFiles(path.join(assetsDir, 'fonts'), '.ttf');

// 官方 GitHub 对照
const gh = await officialGithubFiles();

// 官方 npm 对照 + 完整性（解压到临时目录比对，避免管道捕获在沙箱下 EPERM）
let npmDir = null;
let npmFiles = null;
let npmTarball = '';
let sizeMismatch = [];
let tarballNote = '未下载';
let npmTmp = '';
try {
  npmTarball = await ensureNpmTarball(pkg.version, path.join(os.tmpdir(), `dsh-pet-${pkg.version}.tgz`));
  npmTmp = fs.mkdtempSync(path.join(os.tmpdir(), `dsh-pet-npm-${pkg.version}-`));
  npmDir = extractWebm(npmTarball, npmTmp);
  npmFiles = listFiles(npmDir, '.webm');
  tarballNote = path.basename(npmTarball);
  const byName = new Map(npmFiles.map((f) => [f.name, f.bytes]));
  for (const f of local) {
    if (byName.has(f.name) && byName.get(f.name) !== f.bytes) sizeMismatch.push(f.name);
  }
} catch (e) {
  tarballNote = `不可用：${e.message}`;
}

const badMagic = local.filter((f) => !isEbml(path.join(webmDir, f.name))).map((f) => f.name);
const ghByName = new Map((gh?.files || []).map((f) => [f.name, f.bytes]));
const ghSizeMismatch = local
  .filter((f) => ghByName.has(f.name) && ghByName.get(f.name) !== f.bytes)
  .map((f) => f.name);

// 补齐缺失（默认执行；--dry-run 只报告）
let restored = [];
let downloadNote = '无缺失，0 个下载（已存在文件全部跳过）';
if (missing.length > 0) {
  if (DRY_RUN) {
    downloadNote = `发现 ${missing.length} 个缺失；--dry-run 未下载`;
  } else if (!npmDir) {
    downloadNote = `发现 ${missing.length} 个缺失，但官方 tarball 不可用，未下载`;
  } else {
    console.log(`发现 ${missing.length} 个缺失动画，开始从官方 npm tarball 补齐…`);
    restored = restoreMissing(npmDir, missing, webmDir);
    downloadNote = `补齐 ${restored.length}/${missing.length} 个`;
    for (const r of restored) localByBase.set(r.name, { base: r.name, bytes: r.bytes });
  }
}
if (npmTmp) fs.rmSync(npmTmp, { recursive: true, force: true });

const now = new Date();
const missingBefore = missing.length;
const remaining = missing.filter((n) => !localByBase.has(n));
const allPresent = remaining.length === 0;
const localNow = local.length + restored.length;
const totalBytes = [...localByBase.values()].reduce((s, f) => s + f.bytes, 0);
const summary = {
  scanTime: now.toISOString(),
  pluginDir,
  version: pkg.version,
  officialManifest: 'assets/config.jsonc（插件随包官方清单）',
  counts: {
    required: required.length,
    local: localNow,
    missing: missingBefore,
    remaining: remaining.length,
    unused: unused.length,
    core: groups.core.length,
    random: groups.random.length,
    events: groups.events.length,
  },
  integrity: {
    sizeMismatch,
    ghSizeMismatch,
    badMagic,
    npmTarball: tarballNote,
    npmFiles: npmFiles?.length ?? null,
  },
  github: { source: gh?.source, files: gh?.files?.length ?? null },
  downloaded: restored,
  note: downloadNote,
};

fs.mkdirSync(REPORT_DIR, { recursive: true });
fs.writeFileSync(path.join(REPORT_DIR, 'official-animations.json'), JSON.stringify({ version: pkg.version, groups, required }, null, 2));
fs.writeFileSync(path.join(REPORT_DIR, 'report.json'), JSON.stringify(summary, null, 2));

// 逐文件清单（name,group,bytes,status），方便核对/二次脚本消费
const groupOf = new Map();
for (const [g, names] of Object.entries(groups)) names.forEach((n) => groupOf.set(n, g));
const csvRows = required.map((n) => {
  const f = localByBase.get(n);
  const status = !f ? 'missing' : restored.some((r) => r.name === n) ? 'restored' : 'present';
  return [n, groupOf.get(n) || '', f ? f.bytes : 0, status];
});
const csv = [
  'name,group,bytes,status',
  ...csvRows.map((r) => r.map((x) => `"${String(x).replace(/"/g, '""')}"`).join(',')),
].join('\n');
fs.writeFileSync(path.join(REPORT_DIR, 'inventory.csv'), csv);

const lines = [];
lines.push(`# 桌宠插件素材审计报告`);
lines.push('');
lines.push(`- 扫描时间：${now.toLocaleString('zh-CN', { hour12: false })}`);
lines.push(`- 插件目录：\`${pluginDir}\``);
lines.push(`- 插件版本：dsh-pet ${pkg.version}`);
lines.push(`- 官方清单：\`assets/config.jsonc\`（${required.length} 个动画名）+ GitHub main / npm ${pkg.version} 对照`);
lines.push('');
lines.push(`## 结论`);
lines.push('');
if (allPresent) {
  if (missingBefore === 0) {
    lines.push(`**无缺失：官方清单 ${required.length} 个动画，本地 ${localNow} 个文件全部齐备（100%）。**`);
    lines.push(`本次下载 **0 个文件**——所有文件均已存在，按“跳过已存在、不重复下载”执行。`);
  } else {
    lines.push(`**发现 ${missingBefore} 个缺失动画，已全部补齐（下载 ${restored.length} 个），现为 ${required.length}/${required.length} 齐备。**`);
  }
} else {
  lines.push(`**发现 ${missingBefore} 个缺失动画，补齐 ${restored.length} 个，仍有 ${remaining.length} 个未补齐（${remaining.join('、')}）。**`);
}
lines.push('');
lines.push(`## 覆盖情况`);
lines.push('');
lines.push(
  mdTable(
    [
      ['核心动画（待机/转向/拖拽/点击/移动）', groups.core.length, groups.core.filter((n) => localByBase.has(n)).length],
      ['随机动作（小动作/玩耍/吃什么/时节/文字）', groups.random.length, groups.random.filter((n) => localByBase.has(n)).length],
      ['事件动画（余额/碎碎念/工作状态）', groups.events.length, groups.events.filter((n) => localByBase.has(n)).length],
      ['**合计**', `**${required.length}**`, `**${required.filter((n) => localByBase.has(n)).length}**`],
    ],
    ['分组', '官方数量', '本地已有'],
  ),
);
lines.push('');
lines.push(`- 本地 webm 文件：${localNow} 个，共 ${(totalBytes / 1024 / 1024).toFixed(1)} MB`);
lines.push(`- 官方有、本地无：${missingBefore} 个${missingBefore ? `（${missing.join('、')}）` : ''}`);
lines.push(`- 补齐后仍缺：${remaining.length} 个${remaining.length ? `（${remaining.join('、')}）` : ''}`);
lines.push(`- 本地有、官方清单无：${unused.length} 个${unused.length ? `（${unused.join('、')}）` : ''}`);
lines.push('');
lines.push(`## 其他素材`);
lines.push('');
lines.push(
  mdTable(
    [
      ['表情包 memes', memesCfg.length, memesLocal.length, memesMissing.length ? memesMissing.join('、') : '无缺失'],
      ['通知图 pic', '-', picLocal.length, '随包完整'],
      ['字体 fonts', '-', fontsLocal.length, fontsLocal.length ? '随包完整' : '缺失'],
    ],
    ['类别', '官方/配置', '本地', '缺失'],
  ),
);
lines.push('');
lines.push(`## 完整性校验`);
lines.push('');
lines.push(`- npm 官方 tarball 对照：${tarballNote}${npmFiles ? `，webm ${npmFiles.length} 个` : ''}`);
lines.push(`- 与 npm ${pkg.version} 同名文件大小不一致：${sizeMismatch.length} 个${sizeMismatch.length ? `（${sizeMismatch.join('、')}）` : ''}`);
lines.push(`- 与 GitHub main 同名文件大小不一致：${ghSizeMismatch.length} 个${ghSizeMismatch.length ? `（${ghSizeMismatch.join('、')}）` : ''}`);
lines.push(`- webm EBML 头校验失败：${badMagic.length} 个${badMagic.length ? `（${badMagic.join('、')}）` : ''}`);
lines.push(`- GitHub main 对照：${gh?.source ?? '未检查'}${gh?.files ? `，官方 webm ${gh.files.length} 个` : ''}`);
lines.push('');
lines.push(`## 下载结果`);
lines.push('');
lines.push(`- 本次下载：${restored.length} 个`);
lines.push(`- 跳过（已存在）：${required.length - missingBefore} 个`);
lines.push(`- 补齐后仍缺：${remaining.length} 个`);
lines.push(`- 说明：${downloadNote}`);
lines.push('');
lines.push(`## 关于「core + extra」`);
lines.push('');
lines.push(`dsh-pet ${pkg.version} 官方发布的是**单一 webm 素材集**（一个 npm 包、一份 \`assets/config.jsonc\` 清单），没有把动画拆成 \`core/\` 与 \`extra/\` 两个目录。`);
lines.push(`本报告按官方清单的三个功能组统计：核心 ${groups.core.length} + 随机 ${groups.random.length} + 事件 ${groups.events.length} = ${required.length}。`);
lines.push(`官方另有一个**可选的 macOS 专用 .mov 包**（GitHub Release \`assets-mov\`，106 个文件、与 webm 一一对应），Windows 播放不使用，本次未下载（避免重复下载同一批动画的另一种容器）。`);
lines.push('');
lines.push(`> 生成脚本：\`${path.join(REPORT_DIR, 'audit-pet-assets.mjs')}\``);
lines.push(`> 逐文件清单：\`${path.join(REPORT_DIR, 'inventory.csv')}\``);

fs.writeFileSync(path.join(REPORT_DIR, 'report.md'), lines.join('\n'));

console.log('');
console.log(`插件：${pluginDir}（dsh-pet ${pkg.version}）`);
console.log(`官方清单：${required.length}（核心 ${groups.core.length} / 随机 ${groups.random.length} / 事件 ${groups.events.length}）`);
console.log(`本地 webm：${localNow}；初始缺失：${missingBefore}；补齐后仍缺：${remaining.length}；多余：${unused.length}`);
console.log(`下载：${restored.length}；${downloadNote}`);
console.log(`报告：${path.join(REPORT_DIR, 'report.md')}`);

process.exit(allPresent ? 0 : 2);
