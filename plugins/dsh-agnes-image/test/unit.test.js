import assert from 'node:assert/strict'
import test from 'node:test'
import {
  AgnesApiError,
  AGNES_DEFAULT_BASE_URL,
  buildGenerationRequest,
  parseGenerationResponse,
} from '../lib/agnes-client.js'
import {
  defaultImageFileName,
  extensionFor,
  formatBytes,
  imageDimensions,
  safeBaseName,
  sniffImageMediaType,
  toDataUri,
} from '../lib/image.js'

const PNG_1X1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)
const JPEG_1X1 = Buffer.from(
  '/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==',
  'base64',
)

test('buildGenerationRequest follows the documented request shape', () => {
  const body = buildGenerationRequest({
    prompt: '  A glass cube  ',
    size: '2K',
    ratio: '16:9',
    images: ['https://example.com/a.png'],
  })
  assert.equal(body.model, 'agnes-image-2.5-flash')
  assert.equal(body.prompt, 'A glass cube')
  assert.equal(body.size, '2K')
  assert.equal(body.ratio, '16:9')
  assert.deepEqual(body.extra_body, {
    response_format: 'url',
    image: ['https://example.com/a.png'],
  })
  assert.equal(body.response_format, undefined)
  assert.equal(body.tags, undefined)
})

test('buildGenerationRequest rejects invalid input without a network call', () => {
  assert.throws(() => buildGenerationRequest({ prompt: '   ' }), AgnesApiError)
  assert.throws(() => buildGenerationRequest({ prompt: 'x', size: '8K' }), /size must be one of/)
  assert.throws(() => buildGenerationRequest({ prompt: 'x', ratio: '5:4' }), /ratio must be one of/)
  assert.throws(() => buildGenerationRequest({ prompt: 'x', images: 'nope' }), /images must be an array/)
})

test('buildGenerationRequest omits extra_body.image for text-to-image', () => {
  const body = buildGenerationRequest({ prompt: 'x' })
  assert.equal(Object.hasOwn(body.extra_body, 'image'), false)
})

test('parseGenerationResponse handles URL, base64, and malformed payloads', () => {
  assert.deepEqual(
    parseGenerationResponse({ data: [{ url: 'https://x.test/a.png', b64_json: '' }], task_id: 't1' }),
    { b64Json: undefined, url: 'https://x.test/a.png', taskId: 't1', revisedPrompt: undefined },
  )
  const b64 = parseGenerationResponse({ data: [{ url: null, b64_json: 'AAAA' }] })
  assert.equal(b64.b64Json, 'AAAA')
  assert.equal(b64.url, undefined)
  assert.throws(() => parseGenerationResponse({}), /no image data/)
  assert.throws(() => parseGenerationResponse({ data: [{}] }), /neither url nor b64_json/)
})

test('sniffImageMediaType recognises the four accepted formats', () => {
  assert.equal(sniffImageMediaType(PNG_1X1), 'image/png')
  assert.equal(sniffImageMediaType(JPEG_1X1), 'image/jpeg')
  assert.equal(sniffImageMediaType(Buffer.from('GIF89a' + '\0'.repeat(10), 'latin1')), 'image/gif')
  assert.equal(sniffImageMediaType(Buffer.from('RIFF' + '\0'.repeat(4) + 'WEBP' + '\0'.repeat(4), 'latin1')), 'image/webp')
  assert.equal(sniffImageMediaType(Buffer.from('not an image at all')), undefined)
})

test('imageDimensions reads PNG and JPEG headers', () => {
  assert.deepEqual(imageDimensions(PNG_1X1), { width: 1, height: 1 })
  assert.deepEqual(imageDimensions(JPEG_1X1), { width: 1, height: 1 })
})

test('toDataUri prefixes the sniffed media type', () => {
  assert.ok(toDataUri(PNG_1X1).startsWith('data:image/png;base64,iVBOR'))
  assert.ok(toDataUri(JPEG_1X1, 'image/jpeg').startsWith('data:image/jpeg;base64,/9j/'))
})

test('defaultImageFileName is timestamped and filesystem safe', () => {
  const name = defaultImageFileName(new Date(2026, 9, 26, 22, 55, 30), 'a1b2c3')
  assert.equal(name, 'agnes-20261026-225530-a1b2c3.png')
  assert.equal(extensionFor('image/jpeg'), '.jpg')
  assert.equal(safeBaseName('a/b:c*?'), 'a-b-c')
  assert.equal(safeBaseName('   '), 'agnes-image')
  assert.equal(formatBytes(1024 * 1024), '1.00 MB')
})

test('default base URL targets the documented API host', () => {
  assert.equal(AGNES_DEFAULT_BASE_URL, 'https://apihub.agnes-ai.com/v1')
})
