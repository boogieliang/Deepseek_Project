/**
 * Host-free integration test for the plugin entry module.
 *
 * The harness package `@deepseek-ai/dsh-tools` is replaced with a tiny stub so
 * `apply()` can run under plain Node, then the registered tool is executed
 * against the real Agnes API. Covers text-to-image, image-to-image, file
 * output, argument validation, and the model-facing render.
 */

import assert from 'node:assert/strict'
import { mkdir, readdir, readFile, rm, writeFile } from 'node:fs/promises'
import { existsSync, rmSync } from 'node:fs'
import { registerHooks } from 'node:module'
import { homedir, tmpdir } from 'node:os'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { imageDimensions, sniffImageMediaType } from '../lib/image.js'

const here = import.meta.dirname
const stubPath = join(tmpdir(), `dsh-tools-stub-${process.pid}.mjs`)
await writeFile(stubPath, 'export const defineTool = (definition) => definition\n')
process.on('exit', () => { try { rmSync(stubPath, { force: true }) } catch {} })

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '@deepseek-ai/dsh-tools') return { url: pathToFileURL(stubPath).href, shortCircuit: true }
    return nextResolve(specifier, context)
  },
})

async function readCredentialRef(reference) {
  if (process.env[reference]) return process.env[reference]
  const home = process.env.DSH_HOME ?? join(homedir(), '.dsh')
  const text = await readFile(join(home, '.credentials.yaml'), 'utf8')
  let inRefs = false
  for (const line of text.split(/\r?\n/u)) {
    if (/^refs:\s*$/u.test(line)) { inRefs = true; continue }
    if (inRefs && /^\S/u.test(line)) break
    const match = inRefs ? /^\s{2}([A-Za-z_][A-Za-z0-9_]*):\s*(.+?)\s*$/u.exec(line) : null
    if (match !== null && match[1] === reference) return match[2].replace(/^['"]|['"]$/gu, '')
  }
  return undefined
}

const apiKey = await readCredentialRef('AGNES_API_KEY')
if (apiKey === undefined) throw new Error('AGNES_API_KEY unavailable')

const workspace = resolve(here, '..', '..', '..')
const generatedDir = join(workspace, 'assets', 'generated')
const outputDir = join(generatedDir, 'tool-test')
await rm(outputDir, { recursive: true, force: true })
await mkdir(outputDir, { recursive: true })

const fsFake = {
  async resolve(filePath, options = {}) {
    const absolute = isAbsolute(filePath) ? filePath : resolve(options.cwd ?? workspace, filePath)
    return { targetKey: absolute, displayPath: filePath }
  },
  processPath(target) { return target.targetKey },
  async stat(target) {
    if (!existsSync(target.targetKey)) return undefined
    return { type: 'file', size: 0, version: 'v1' }
  },
  async readBytes(target) { return await readFile(target.targetKey) },
  contains(parent, child) {
    const p = resolve(parent.targetKey)
    const c = resolve(child.targetKey)
    return c === p || c.startsWith(p + '\\') || c.startsWith(p + '/')
  },
}

const attachments = {
  async saveImage({ data, mediaType, name }) {
    const dims = imageDimensions(data, mediaType) ?? { width: 0, height: 0 }
    return { attachmentId: 'att_test', mediaType, bytes: data.byteLength, ...dims, name }
  },
}

const ctx = {
  tools: { register(definition) { ctx.tools.registered.push(definition) }, registered: [] },
  logger: { warn() {} },
  get(service) {
    if (service === 'credentials') return { async resolve(ref) { return ref === 'AGNES_API_KEY' ? { value: apiKey } : undefined } }
    if (service === 'fs') return fsFake
    if (service === 'attachments') return attachments
    if (service === 'workingDirectory') return { async ensure() { return workspace } }
    if (service === 'llm') return { async resolveModelInfo() { return { inputModalities: ['text', 'image'] } } }
    return undefined
  },
}

const { apply } = await import('../index.js')
apply(ctx, { outputDir: 'assets/generated/tool-test' })
assert.equal(ctx.tools.registered.length, 1)
const tool = ctx.tools.registered[0]
assert.equal(tool.name, 'agnes_generate_image')
assert.ok(tool.description.includes('Agnes'))
assert.deepEqual(Object.keys(tool.parameters), ['prompt', 'size', 'ratio', 'images', 'output_path', 'overwrite'])

const exec = {
  agent: {
    session: { requestHeader: () => ({ config: { provider: 'p', model: 'm' } }) },
    options: { provider: 'p', model: 'm' },
  },
  signal: AbortSignal.timeout(300_000),
}

// 1. Text-to-image with a 16:9 tier.
const t2i = await tool.execute({ prompt: 'A single ripe red apple on a matte gray table, soft window light, product photo', size: '1K', ratio: '16:9' }, exec)
assert.equal(t2i.mediaType, 'image/png')
assert.ok(t2i.bytes > 1000)
assert.ok(existsSync(t2i.path), 'generated file exists')
assert.equal(t2i.width, 1312)
assert.equal(t2i.height, 736)
assert.equal(t2i.attached, true)
assert.equal(t2i.image.attachmentId, 'att_test')
assert.ok(t2i.url.startsWith('https://'))

const rendered = tool.output.render({}, t2i)
assert.equal(rendered[0].type, 'text')
assert.ok(rendered[0].text.includes(t2i.path))
assert.equal(rendered[1].type, 'image')
assert.ok(tool.output.schema.properties.path.required)

// 2. Image-to-image from a local file, explicit output path.
const reference = join(generatedDir, 'agnes-20261026-225624-live.png')
if (!existsSync(reference)) throw new Error(`reference image missing: ${reference}`)
const i2iOutput = join(outputDir, 'i2i-apple.png')
const i2i = await tool.execute({
  prompt: 'Turn the object into a polished copper cube while keeping the composition, lighting, and background',
  size: '1K',
  ratio: '1:1',
  images: [reference],
  output_path: i2iOutput,
  overwrite: true,
}, exec)
assert.equal(i2i.path, i2iOutput)
assert.ok(existsSync(i2iOutput))
const bytes = await readFile(i2iOutput)
assert.equal(sniffImageMediaType(bytes), 'image/png')
assert.equal(i2i.width, 1024)

// 3. Argument validation happens before any network work.
await writeFile(join(outputDir, 'exists.png'), Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))
await assert.rejects(tool.execute({ prompt: '   ' }, exec), /prompt must be a non-empty string/)
await assert.rejects(tool.execute({ prompt: 'x', images: 'not-an-array' }, exec), /must be an array/)
await assert.rejects(
  tool.execute({ prompt: 'x', output_path: join(outputDir, 'exists.png') }, exec),
  /already exists/,
)
await rm(join(outputDir, 'exists.png'), { force: true })

console.log(JSON.stringify({
  tool: tool.name,
  textToImage: { path: t2i.path, width: t2i.width, height: t2i.height, bytes: t2i.bytes },
  imageToImage: { path: i2i.path, width: i2i.width, height: i2i.height, bytes: i2i.bytes },
  renderBlocks: rendered.map((block) => block.type),
}, null, 2))
