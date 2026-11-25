/**
 * Live smoke test: call the real Agnes image API and save a PNG into the
 * workspace. Run with `node test/live.mjs [prompt...]`.
 *
 * The key is read from AGNES_API_KEY, then from the credential reference in
 * $DSH_HOME/.credentials.yaml (defaults to ~/.dsh).
 */

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { AgnesImageClient } from '../lib/agnes-client.js'
import { defaultImageFileName, imageDimensions, sniffImageMediaType } from '../lib/image.js'

const workspaceRoot = resolve(import.meta.dirname, '..', '..', '..')

/** Read one `refs:` value from the credentials document without a YAML dependency. */
async function readCredentialRef(reference) {
  if (process.env[reference]) return process.env[reference]
  const home = process.env.DSH_HOME ?? join(homedir(), '.dsh')
  let text
  try {
    text = await readFile(join(home, '.credentials.yaml'), 'utf8')
  } catch {
    return undefined
  }
  let inRefs = false
  for (const line of text.split(/\r?\n/u)) {
    if (/^refs:\s*$/u.test(line)) {
      inRefs = true
      continue
    }
    if (inRefs && /^\S/u.test(line)) break
    const match = inRefs ? /^\s{2}([A-Za-z_][A-Za-z0-9_]*):\s*(.+?)\s*$/u.exec(line) : null
    if (match !== null && match[1] === reference) return match[2].replace(/^['"]|['"]$/gu, '')
  }
  return undefined
}

const prompt = process.argv.slice(2).join(' ').trim()
  || 'A cinematic hero image of a translucent blue glass cube floating above a dark studio floor, soft rim light, ultra detailed, centered composition'

const apiKey = await readCredentialRef('AGNES_API_KEY')
if (apiKey === undefined) {
  console.error('AGNES_API_KEY is not set and was not found in $DSH_HOME/.credentials.yaml')
  process.exit(1)
}

const client = new AgnesImageClient({ resolveApiKey: async () => apiKey })
const started = Date.now()
const result = await client.generate({ prompt, size: '1K', ratio: '1:1' })
const mediaType = sniffImageMediaType(result.bytes)
if (mediaType === undefined) throw new Error('Agnes returned bytes that are not a supported image')
const dimensions = imageDimensions(result.bytes, mediaType)
const outputDir = join(workspaceRoot, 'assets', 'generated')
const outputPath = join(outputDir, defaultImageFileName(new Date(), 'live'))
await mkdir(dirname(outputPath), { recursive: true })
await writeFile(outputPath, result.bytes)
console.log(JSON.stringify({
  path: outputPath,
  mediaType,
  bytes: result.bytes.byteLength,
  ...dimensions,
  url: result.url,
  taskId: result.taskId,
  seconds: Number(((Date.now() - started) / 1000).toFixed(1)),
}, null, 2))
